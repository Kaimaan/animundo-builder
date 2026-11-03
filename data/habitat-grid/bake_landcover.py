"""Bake z10 land-cover shares from ESA WorldCover 2021 v200 (#238).

Every 3° WorldCover tile touching a grid land cell is read once
at 1:2 overview through Planetary Computer (free, $0); class
pixels are counted per z10 cell box (built-up 50, cropland 40,
water 80), and shares go over non-water pixels. The 5×5 mask
comes from the same read. Checkpoints to --resume as it goes;
re-running with the same --resume continues where it stopped.

    uv run python data/habitat-grid/bake_landcover.py \\
        --resume /tmp/wc/bake.pkl --out /tmp/wc/landcover.json.gz

then move the output to data/habitat-grid/landcover.json.gz.
"""

import argparse
import gzip
import json
import math
import os
import pickle
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from builder.grid import bounds  # noqa: E402

BASE = (
    "https://ai4edataeuwest.blob.core.windows.net/esa-worldcover/v200/2021"
    "/map/ESA_WorldCover_10m_2021_v200_{t}_Map.tif"
)

BUILT, CROP, WATER = 50, 40, 80
DIV = 2


def wc_name(lat: float, lon: float) -> str:
    an = abs(math.floor(lat / 3) * 3)
    n = math.floor((lon + 180) / 3) * 3 - 180
    return f"{'N' if lat >= 0 else 'S'}{an:02d}{'E' if n >= 0 else 'W'}{abs(n):03d}"


def wc_range(name: str) -> tuple[float, float, float, float]:
    ns, latn, ew, lonn = name[0], int(name[1:3]), name[3], int(name[4:7])
    lats = (latn, latn + 3) if ns == "N" else (-latn, -latn + 3)
    lons = (lonn, lonn + 3) if ew == "E" else (-lonn, -lonn + 3)
    return (*lats, *lons)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", default="/tmp/wc/bake.pkl")
    ap.add_argument("--out", default="/tmp/wc/landcover.json.gz")
    ap.add_argument(
        "--grid",
        default=str(ROOT / "data" / "habitat-grid" / "z10.json.gz"),
    )
    args = ap.parse_args()

    import planetary_computer
    import rasterio

    grid = json.load(gzip.open(args.grid, "rt"))["cells"]
    land = sorted(
        (int(k.split(",")[0]), int(k.split(",")[1]))
        for k, v in grid.items()
        if v[5] > 0
    )
    print(f"land cells: {len(land)}", flush=True)

    need: dict[str, None] = {}
    for col, row in land:
        mnla, mxla, mnlo, mxlo = bounds(f"z10_{col:04d}_{row:04d}")
        for la in (mnla + 1e-9, mxla - 1e-9):
            for lo in (mnlo + 1e-9, mxlo - 1e-9):
                need.setdefault(wc_name(la, lo))
    tiles = sorted(need)
    if args.limit:
        tiles = tiles[: args.limit]
    print(f"WC tiles: {len(tiles)}", flush=True)

    # WC tiles each cell needs, and each tile's cells.
    tile_cells: dict[str, list[int]] = {t: [] for t in tiles}
    only = set(tiles)
    for i, (col, row) in enumerate(land):
        mnla, mxla, mnlo, mxlo = bounds(f"z10_{col:04d}_{row:04d}")
        names = set()
        for la in (mnla + 1e-9, mxla - 1e-9):
            for lo in (mnlo + 1e-9, mxlo - 1e-9):
                names.add(wc_name(la, lo))
        for t in names:
            if t in only:
                tile_cells[t].append(i)

    creds = {"token": "", "at": 0.0}

    def refresh_token(force=False):
        if force or time.time() - creds["at"] > 1500:
            creds["token"] = planetary_computer.sign(BASE.format(t="S03W060")).split(
                "?", 1
            )[1]
            creds["at"] = time.time()
            print("signed", flush=True)

    refresh_token(force=True)

    acc = np.zeros((len(land), 4), dtype=np.int64)  # built, crop, water, land
    masks: list[str | None] = [None] * len(land)
    missing: set[str] = set()
    done: set[str] = set()
    if os.path.isfile(args.resume):
        try:
            state = pickle.load(open(args.resume, "rb"))
            if state.get("n") == len(land):
                acc = state["acc"]
                masks = state["masks"]
                done = set(state["done"])
                missing = set(state.get("missing", ()))
                print(f"resumed: {len(done)} tiles done", flush=True)
        except Exception as error:
            print(f"resume failed: {error}", flush=True)
    lock = threading.Lock()

    def window_px(a_shape, a_bounds, box):
        mnla, mxla, mnlo, mxlo = box
        h, w = a_shape
        lon0, lat0, lon1, lat1 = a_bounds  # west, south, east, north
        x0 = int((mnlo - lon0) / (lon1 - lon0) * w)
        x1 = int(math.ceil((mxlo - lon0) / (lon1 - lon0) * w))
        y0 = int((lat1 - mxla) / (lat1 - lat0) * h)
        y1 = int(math.ceil((lat1 - mnla) / (lat1 - lat0) * h))
        return (
            max(0, y0),
            min(h, y1),
            max(0, x0),
            min(w, x1),
        )

    def bake_tile(tile):
        lat0, lat1, lon0, lon1 = wc_range(tile)
        url = f"{BASE.format(t=tile)}?{creds['token']}"
        a = None
        for attempt in range(4):
            try:
                with rasterio.open(url) as src:
                    h, w = src.height // DIV, src.width // DIV
                    a = src.read(
                        1,
                        out_shape=(h, w),
                        resampling=rasterio.enums.Resampling.nearest,
                    )
                break
            except Exception as error:
                text = str(error)
                if "404" in text or attempt == 3:
                    with lock:
                        missing.add(tile)
                    print(f"{tile}: MISSING ({text[:80]})", flush=True)
                    return
                time.sleep(5 * (attempt + 1))
        assert a is not None
        bnds = (lon0, lat0, lon1, lat1)
        for i in tile_cells[tile]:
            col, row = land[i]
            box = bounds(f"z10_{col:04d}_{row:04d}")
            y0, y1, x0, x1 = window_px((h, w), bnds, box)
            if y1 <= y0 or x1 <= x0:
                continue
            win = a[y0:y1, x0:x1]
            b = int((win == BUILT).sum())
            c = int((win == CROP).sum())
            wt = int((win == WATER).sum())
            with lock:
                acc[i, 0] += b
                acc[i, 1] += c
                acc[i, 2] += wt
                acc[i, 3] += win.size - wt
            # The mask comes from the tile holding the cell centre.
            mnla, mxla, mnlo, mxlo = box
            if wc_name((mnla + mxla) / 2, (mnlo + mxlo) / 2) != tile:
                continue
            chars = []
            for r in range(5):
                for s in range(5):
                    sy0, sy1 = y0 + (y1 - y0) * r // 5, y0 + (y1 - y0) * (r + 1) // 5
                    sx0, sx1 = x0 + (x1 - x0) * s // 5, x0 + (x1 - x0) * (s + 1) // 5
                    sub = a[sy0:sy1, sx0:sx1]
                    n = sub.size or 1
                    if (sub == BUILT).sum() / n >= 0.01:
                        chars.append("b")
                    elif (sub == CROP).sum() / n >= 0.05:
                        chars.append("c")
                    elif (sub == WATER).sum() / n > 0.5:
                        chars.append("w")
                    else:
                        chars.append("l")
            with lock:
                masks[i] = "".join(chars)

    completed = [0]
    finished: set[str] = set(done)

    def checkpoint():
        with lock:
            pickle.dump(
                {
                    "n": len(land),
                    "acc": acc.copy(),
                    "masks": list(masks),
                    "done": sorted(finished),
                    "missing": sorted(missing),
                },
                open(args.resume, "wb"),
            )

    def counted(tile):
        try:
            bake_tile(tile)
        finally:
            with lock:
                finished.add(tile)
                completed[0] += 1
                n = completed[0]
                if n % 25 == 0:
                    print(f"baked {n}/{len(todo)}", flush=True)
            if n % 100 == 0:
                checkpoint()
            refresh_token()

    with ThreadPoolExecutor(max_workers=8) as pool:
        todo = [t for t in tiles if t not in done]
        print(f"todo: {len(todo)}", flush=True)
        for tile in todo:
            pool.submit(counted, tile)
        pool.shutdown(wait=True)
    print("pass 1 done", flush=True)
    checkpoint()

    cells: dict[str, list] = {}
    for i, (col, row) in enumerate(land):
        b, c, w, lnd = (int(x) for x in acc[i])
        denom = b + c + lnd
        if denom <= 0:
            cells[f"{col},{row}"] = [0.0, 0.0, masks[i] or "w" * 25]
        else:
            cells[f"{col},{row}"] = [
                round(b / denom, 4),
                round(c / denom, 4),
                masks[i] or "l" * 25,
            ]
    json.dump(
        {
            "cells": cells,
            "source": "esa-worldcover-v200-2021",
            "missing_tiles": sorted(missing),
        },
        gzip.open(args.out, "wt", encoding="utf-8"),
    )
    print(
        f"wrote {args.out}: {len(cells)} cells, {len(missing)} missing tiles",
        flush=True,
    )


if __name__ == "__main__":
    main()
