"""Stage 10, pack: the round as a content pack, validated and ready to play.

After the whole draft checker runs again on the round's region with
its new animals and plants in place, the stage lays the pack out in
the job's `pack/` folder exactly like spec 01: the region under its
draft id, only the new species' files and keys, preview sheets for
the draft's screen, and a manifest with every file's hash and size.
A pack that breaks the size limits (2 MB a model, 8 MB the pack)
is refused here, not published. Uploading to Storage is the
worker's job (T22); this stage ends with a local, checked pack.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from ..schemas import validate
from ..world import check_draft

PACK_FORMAT = 1
MAX_GLB_BYTES = 2 * 1024 * 1024
MAX_PACK_BYTES = 8 * 1024 * 1024


class PackStage:
    """Validate the round and write `pack/` with its manifest."""

    name = "pack"

    def run(self, ctx: Any) -> dict[str, Any]:
        if ctx.world_path is None:
            raise ValueError("pack needs --world")
        world = Path(ctx.world_path)
        self._gate(world, ctx)
        plan = json.loads((ctx.job_dir / "species-plan.json").read_text())
        new_ids = [
            entry["species"] for entry in plan["species"] if entry["decision"] == "new"
        ]
        reused = sorted(
            entry["species"]
            for entry in plan["species"]
            if entry["decision"] == "reuse"
        )
        region_path = next((ctx.job_dir / "regions").glob("*.json"))
        region = json.loads(region_path.read_text(encoding="utf-8"))
        draft_place_id = f"draft_{ctx.job_id}"
        pack = ctx.job_dir / "pack"
        if pack.exists():
            shutil.rmtree(pack)
        pack_region = dict(region, id=draft_place_id)
        _write(pack / "regions" / f"{draft_place_id}.json", pack_region)
        for species_id in new_ids:
            _copy(
                ctx.job_dir / "animals" / f"{species_id}.json",
                pack / "animals" / f"{species_id}.json",
            )
            _copy(
                ctx.job_dir / "models" / f"{species_id}.glb",
                pack / "models" / f"{species_id}.glb",
            )
            preview = ctx.job_dir / "models" / f"{species_id}.png"
            if preview.is_file():
                _copy(preview, pack / "previews" / f"{species_id}.png")
        plants = ctx.job_dir / "models" / "plants"
        if plants.is_dir():
            for glb in sorted(plants.glob("*.glb")):
                _copy(glb, pack / "models" / "plants" / glb.name)
            for preview in sorted(plants.glob("*.png")):
                _copy(preview, pack / "previews" / preview.name)
        for source in sorted((ctx.job_dir / "locale").rglob("*.json")):
            keys = json.loads(source.read_text(encoding="utf-8"))
            rel = source.relative_to(ctx.job_dir / "locale")
            _write(
                pack / "locale" / rel,
                _redraft_keys(keys, region["id"], draft_place_id),
            )
        manifest = {
            "draft_id": ctx.job_id,
            "draft_place_id": draft_place_id,
            "place_id": region["id"],
            "version": 1,
            "format": PACK_FORMAT,
            "files": _manifest_files(pack),
            "new_species": sorted(new_ids),
            "reused_species": reused,
        }
        validate("pack-manifest", manifest)
        _write(pack / "manifest.json", manifest)
        total = sum(entry["bytes"] for entry in manifest["files"])
        for entry in manifest["files"]:
            if entry["path"].endswith(".glb") and entry["bytes"] > MAX_GLB_BYTES:
                raise _Stopped(
                    f"pack refused: {entry['path']} is {entry['bytes']} bytes, "
                    "at most 2 MB a model"
                )
        if total > MAX_PACK_BYTES:
            raise _Stopped(
                f"pack refused: {total} bytes, at most 8 MB a pack; a person looks"
            )
        return {
            "pack_dir": "pack",
            "pack_files": len(manifest["files"]),
            "pack_bytes": total,
            "version": 1,
            "manifest": "pack/manifest.json",
        }

    def _gate(self, world: Path, ctx: Any) -> None:
        """The whole draft checker once more, on the finished round."""
        safety = ctx.job_dir / "safety.json"
        if not safety.is_file():
            raise _Stopped("no safety verdict; the safety stage must pass first")
        record = json.loads(safety.read_text(encoding="utf-8"))
        validate("safety", record)
        failed = [item for item in record["items"] if item["verdict"] != "pass"]
        if failed:
            raise _Stopped(
                f"safety failed {len(failed)} items; the safety stage must pass first"
            )
        result = check_draft(world, ctx.job_dir, "pack")
        by_check = {entry.get("check"): entry for entry in result.get("results", [])}
        for name in ("locale", "schema", "taxa", "region", "animals"):
            if by_check.get(name, {}).get("status") == "fail":
                raise _Stopped(
                    f"the pack gate failed on {name}: "
                    + "; ".join(by_check[name].get("problems", [])[:3])
                )
        models = by_check.get("models", {})
        if models.get("status") == "fail":
            rest = [
                line
                for line in models.get("problems", [])
                if "the preview sheet is missing" not in line
            ]
            if rest:
                raise _Stopped("the pack gate failed on models: " + "; ".join(rest[:3]))


class _Stopped(Exception):
    """The round is not shippable, or the pack is too big."""


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _copy(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)


def _redraft_keys(
    keys: dict[str, str], place_id: str, draft_place_id: str
) -> dict[str, str]:
    """Point the pack's region keys at its draft region id."""
    prefix = f"region.{place_id}."
    out = {}
    for key, text in keys.items():
        if key.startswith(prefix):
            key = f"region.{draft_place_id}." + key[len(prefix) :]
        out[key] = text
    return out


def _manifest_files(pack: Path) -> list[dict[str, Any]]:
    entries = []
    for path in sorted(pack.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        raw = path.read_bytes()
        entries.append(
            {
                "path": str(path.relative_to(pack)),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        )
    return entries
