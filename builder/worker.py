"""`builder worker --listen`: plan border requests, build jobs.

The builder is a factory (D17): the game runs the world, the worker
plans every new or changed border request and builds only the build
jobs the game creates. Planning (T74), building (T76), previews
(T84) and Modal (T77) are live.
"""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .config import Config
from .pipeline import StaleOwner
from .plan import plan_tile

#: How long a claim owns its job without a renewal (#155). Renewals
#: ride every status, progress and spend write, plus each item, each
#: stage and each publish, so only a silent worker loses its lease.
LEASE_TTL_MINUTES = 30

#: A document update value that deletes the field.
DELETE = object()

#: Stage-A build estimate per unfiled animal or plant (#222.2);
#: stage C calibrates it from real runs.
PLACE_BUILD_USD_PER_ITEM = 0.50


class Store(Protocol):
    """The Firestore surface the worker needs."""

    def get_request(self, tile: str) -> dict[str, Any] | None: ...
    def list_requests(self) -> list[tuple[str, dict[str, Any]]]: ...
    def list_plans(self) -> list[tuple[str, dict[str, Any]]]: ...
    def list_areas(self) -> list[tuple[str, dict[str, Any]]]: ...
    def list_packs(self) -> list[tuple[str, dict[str, Any]]]: ...
    def get_enabled(self) -> bool: ...
    def read_blob(self, path: str) -> tuple[bytes | None, int | None]: ...
    def write_blob(
        self,
        path: str,
        data: bytes,
        content_type: str = "application/json",
        *,
        if_generation_match: int | None = None,
        content_encoding: str | None = None,
    ) -> None: ...
    def list_blobs(self, prefix: str) -> list[str]: ...
    def get_plan(self, tile: str) -> dict[str, Any] | None: ...
    def set_plan(self, tile: str, doc: dict[str, Any]) -> None: ...
    def get_build_job(self, job_id: str) -> dict[str, Any] | None: ...
    def update_build_job(self, job_id: str, fields: dict[str, Any]) -> None: ...
    def claim_build_job(self, job_id: str, worker_id: str) -> bool: ...
    def renew_build_job(self, job_id: str, worker_id: str) -> bool: ...
    def update_build_job_if_owner(
        self, job_id: str, worker_id: str, fields: dict[str, Any]
    ) -> bool: ...
    def pending_build_jobs(self) -> list[tuple[str, dict[str, Any]]]: ...
    def add_alert(self, item: dict[str, Any]) -> str: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    def set_job(self, job_id: str, doc: dict[str, Any]) -> None: ...
    def update_job(self, job_id: str, fields: dict[str, Any]) -> None: ...


def default_worker_id() -> str:
    """This worker, for the job it claims: host and pid."""
    return f"{socket.gethostname()}:{os.getpid()}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def lease_fresh_iso() -> str:
    """A fresh lease: now plus the TTL, as the store holds it (#155)."""
    return (datetime.now(UTC) + timedelta(minutes=LEASE_TTL_MINUTES)).isoformat()


def lease_expired(lease_until: Any) -> bool:
    """A missing or past lease no longer owns its job."""
    return not isinstance(lease_until, str) or lease_until <= now_iso()


class _Off(Exception):
    """The off switch stopped this item; resume when enabled."""


class _Paused(Exception):
    """A timeout left spend unknown; the run pauses, resume retries (#157)."""


class _BudgetOut(Exception):
    """Money or access ran out; remaining items don't start."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _SpreadMissing(ValueError):
    """A spread species in neither the library nor a live pack (#199.2)."""


class _NoHome(ValueError):
    """The tile has no home in this checkout; a made one may follow."""


def _overlay_present(made: dict[str, Any] | None, kind: str, what: str) -> bool:
    """Whether the remade tile holds the species, for the overlay (#220)."""
    if not made:
        return False
    if kind == "plant":
        plants = made.get("plants") or {}
        return any(
            entry.get("id") == what
            for side in ("cover", "water")
            for entry in plants.get(side, []) or []
            if isinstance(entry, dict)
        )
    species = made.get("species") or {}
    return any(
        what in (species.get(name) or []) for name in ("playable", "prey", "predators")
    )


def git_commit(world: Path | None) -> str:
    """The world checkout's short commit, or "unknown" without git."""
    if world is None:
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=world,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return "unknown"
    return out or "unknown"


@dataclass
class Worker:
    """Plans border requests and builds jobs."""

    store: Store
    jobs_dir: Path
    world: Path | None
    config: Config
    worker_id: str = field(default_factory=default_worker_id)
    author: str = "Animundo Builder"
    in_flight: set[tuple[str, str]] = field(default_factory=set)
    grid_dir: Path | None = None
    world_commit: str | None = None
    builder_root: Path | None = None
    species: dict[str, Any] | None = None
    chat: Any | None = None
    adapt: Any | None = None
    build: Any | None = None
    _grid_cache: Any | None = field(default=None, repr=False)
    _was_enabled: bool = field(default=True, repr=False)
    _guard_job: str | None = field(default=None, repr=False)

    # -- events ----------------------------------------------------

    def note_request(self, tile: str, data: dict[str, Any]) -> bool:
        """Queue a border request unless it is already queued."""
        return ("request", tile) not in self.in_flight

    def note_build_job(self, job_id: str, data: dict[str, Any]) -> bool:
        """Queue a build job unless it is done or already queued.

        A paused job waits for an operator resume or give-up: no
        restart or document change runs it on its own (#171.1). An
        operator action queues it so the command is obeyed.
        """
        if data.get("status") in ("published", "failed"):
            return False
        if self._job_paused(data) and not data.get("operator_action"):
            print(f"build job {job_id}: paused, waiting for an operator")
            return False
        return ("build_job", job_id) not in self.in_flight

    @staticmethod
    def _job_paused(job: dict[str, Any]) -> bool:
        """The job or any of its items waits on uncertain spend (#171.1)."""
        if job.get("error") == "paused_spend_uncertain":
            return True
        return any(
            entry.get("status") == "paused_spend_uncertain"
            for entry in job.get("progress", [])
            if isinstance(entry, dict)
        )

    # -- top-level flows ----------------------------------------------

    @staticmethod
    def _is_old_grid(tile: str) -> bool:
        """Retired EASE-grid request ids (`n_414_478` style, #195.5)."""
        parts = tile.split("_")
        return (
            len(parts) == 3
            and parts[0] in {"n", "s"}
            and parts[1].isdigit()
            and parts[2].isdigit()
        )

    def handle_request(self, tile: str) -> None:
        """Plan one border request into `plans/{tile}` (T74)."""
        if self._is_old_grid(tile):
            return
        if not self.store.get_enabled():
            print(f"request {tile}: the off switch is off, skipping")
            return
        if self.world is None or self.grid_dir is None:
            print(
                f"request {tile}: planning needs --world and --habitat-grid",
                file=sys.stderr,
            )
            return
        self.sync_world()
        request = self.store.get_request(tile)
        if request is None:
            return
        try:
            plan = plan_tile(
                tile,
                request,
                self._habitat_grid(),
                self.world,
                self.world_commit or "unknown",
                builder_root=self.builder_root,
                species=self.species,
                species_source=self._species_source(tile),
            )
        except (ValueError, RuntimeError, FileNotFoundError, ConnectionError) as error:
            print(f"request {tile}: cannot plan: {error}", file=sys.stderr)
            self.alert({"kind": "plan_failed", "tile": tile, "error": str(error)[:200]})
            return
        self.store.set_plan(tile, plan)
        blocked = f" blocked={plan['blocked']}" if plan.get("blocked") else ""
        print(f"request {tile}: planned code_only={plan['code_only']}{blocked}")

    def handle_build_job(self, job_id: str) -> None:
        """Build one job, or ship its reviewed items (T76)."""
        if not self.store.get_enabled():
            print(f"build job {job_id}: the off switch is off, skipping")
            return
        if self.world is None or self.grid_dir is None:
            print(
                f"build job {job_id}: building needs --world and --habitat-grid",
                file=sys.stderr,
            )
            return
        self.sync_world()
        job = self.store.get_build_job(job_id)
        if job is None or job.get("status") in ("published", "failed"):
            return
        try:
            if not self._operator_action(job_id, job):
                return
            job = self.store.get_build_job(job_id) or job
            if job.get("status") == "checking":
                self.review_job(job_id, job)
            else:
                self.build_job(job_id, job)
        except StaleOwner:
            print(f"build job {job_id}: another worker owns it; stopping")

    def _operator_action(self, job_id: str, job: dict[str, Any]) -> bool:
        """Run an operator resume or give-up on a paused job (#166.3).

        The console writes `operator_action` on the job doc; the
        watch re-drives the job and the command runs here, then is
        consumed. Resume clears the way and the normal path goes
        on (settling the held spend first); give-up settles the
        held spend, records it, and fails the job. True to go on.
        """
        from .jobs import make_context

        action = job.get("operator_action")
        if not action:
            return True
        if not self._claim(job_id):
            print(f"build job {job_id}: operator action waits for ownership")
            return False
        paused = [
            entry
            for entry in job.get("progress", [])
            if entry.get("status") == "paused_spend_uncertain"
        ]
        if action == "resume" and (
            paused or job.get("error") == "paused_spend_uncertain"
        ):
            print(f"build job {job_id}: operator resumes {len(paused)} paused items")
            assert self.world is not None
            ctx = make_context(self.config, job_id, self.jobs_dir / job_id, self.world)
            if self.chat is not None:
                ctx.client = self.chat
            for entry in paused:
                self._settle_superseded(job_id, {"id": entry["id"]}, ctx)
            progress = [
                {**entry, "status": "building"}
                if entry.get("status") == "paused_spend_uncertain"
                else entry
                for entry in job.get("progress", [])
            ]
            patch: dict[str, Any] = {"progress": progress, "operator_action": DELETE}
            if job.get("error") == "paused_spend_uncertain":
                patch["error"] = DELETE
            self._update(job_id, patch)
            return True
        if action == "give_up" and (
            paused or job.get("error") == "paused_spend_uncertain"
        ):
            print(f"build job {job_id}: operator gives up {len(paused)} paused items")
            assert self.world is not None
            ctx = make_context(self.config, job_id, self.jobs_dir / job_id, self.world)
            if self.chat is not None:
                ctx.client = self.chat
            uncertain = 0.0
            for entry in paused:
                self._settle_superseded(job_id, {"id": entry["id"]}, ctx)
                ref = (entry.get("uncertain") or {}).get("provider") or {}
                try:
                    uncertain += float(ref.get("reserved_usd", 0.0))
                except (TypeError, ValueError):
                    pass
            progress = [
                {**entry, "status": "failed"}
                if entry.get("status") == "paused_spend_uncertain"
                else entry
                for entry in job.get("progress", [])
            ]
            patch = {"progress": progress, "operator_action": DELETE}
            if uncertain > 0:
                try:
                    prior = float(job.get("uncertain_usd", 0.0))
                except (TypeError, ValueError):
                    prior = 0.0
                patch["uncertain_usd"] = round(prior + uncertain, 6)
            self._update(job_id, patch)
            self._fail(job_id, "gave_up")
            return False
        print(f"build job {job_id}: ignoring stale operator action {action!r}")
        self._update(job_id, {"operator_action": DELETE})
        return True

    def _claim(self, job_id: str) -> bool:
        """Own this job or stand down: one live owner, takeovers allowed.

        Follows a dead same-host owner at once (a restart's new pid);
        otherwise only an expired lease can be taken. Terminal jobs
        are never claimed.
        """
        job = self.store.get_build_job(job_id)
        if job is None or job.get("status") in ("published", "failed"):
            return False
        self._takeover_if_dead(job_id, job)
        return self.store.claim_build_job(job_id, self.worker_id)

    def _takeover_if_dead(self, job_id: str, job: dict[str, Any]) -> None:
        """Expire a dead same-host owner's lease, so the claim can take it.

        The expiry itself is conditional on the owner still holding
        the job, so two workers racing a dead lease still agree on
        one winner in the claim. A live pid, another host, or an
        unparsable id never expires: those wait out the lease.
        """
        owner = job.get("worker_id") or ""
        host, sep, pid = owner.rpartition(":")
        if not sep or host != socket.gethostname():
            return
        try:
            dead_pid = int(pid)
        except ValueError:
            return
        try:
            os.kill(dead_pid, 0)
        except OSError:
            pass
        else:
            return
        self.store.update_build_job_if_owner(
            job_id,
            owner,
            {"lease_until": "1970-01-01T00:00:00+00:00", "updated_at": now_iso()},
        )

    def _require_lease(self, job_id: str) -> None:
        """Hold the lease across this phase, or stop as stale (#155)."""
        if not self.store.renew_build_job(job_id, self.worker_id):
            raise StaleOwner(f"build job {job_id}: the lease is gone")

    def _update(self, job_id: str, fields: dict[str, Any]) -> None:
        """Write this job's doc only while owning it, renewing the lease.

        Every status, progress and spend write rides here, so a
        stale owner that resumes after takeover aborts instead of
        overwriting the new owner's work.
        """
        if not self.store.update_build_job_if_owner(
            job_id,
            self.worker_id,
            {**fields, "lease_until": lease_fresh_iso(), "updated_at": now_iso()},
        ):
            raise StaleOwner(f"build job {job_id}: owned by another worker")

    #: Job kinds the worker runs; anything else stays queued (#199.3).
    JOB_KINDS = ("code", "build", "enrich", "spread", "place")

    def build_job(self, job_id: str, job: dict[str, Any]) -> None:
        """Claim the job and build it: code directly, items each alone."""
        from .build import publish_group
        from .tiles import make_tile

        assert self.world is not None
        if self._job_paused(job) and not job.get("operator_action"):
            print(f"build job {job_id}: paused, waiting for an operator")
            return
        if job.get("kind") not in self.JOB_KINDS:
            print(f"build job {job_id}: unknown kind; staying queued")
            return
        if not self._claim(job_id):
            return
        job = self.store.get_build_job(job_id) or job
        if job.get("kind") == "spread":
            self.spread_job(job_id, job)
            return
        if job.get("kind") == "place":
            self.place_job(job_id, job)
            return
        tile = self._job_tile(job_id, job)
        if tile is None:
            return
        if job.get("kind") == "enrich":
            try:
                self._snapshot_plan(job_id, job, tile)
            except ValueError as error:
                print(f"build job {job_id}: bad enrich spec: {error}")
                self._fail(job_id, "bad_enrich")
                return
        else:
            self._snapshot_plan(job_id, job, tile)
        if job.get("kind") == "code":
            if self._tile_refused(tile, job_id, "code"):
                return
            if self.pull_world():  # a merged change reaches this tile (#99.4)
                self.world_commit = git_commit(self.world)
            made, blocked = make_tile(
                tile,
                self._habitat_grid(),
                self.world,
                builder_root=self.builder_root,
                species=self.species,
                species_source=self._species_source(tile),
            )
            if made is None:
                reason = (blocked or {}).get("reason", "no_home")
                self._history_blocked(job_id, tile, "code", reason)
                self._fail(job_id, reason)
                return
            self._require_lease(job_id)
            path = publish_group(
                tile,
                made,
                self.store,
                self.world_commit or "unknown",
                self._history_context(job_id, "code", tile),
            )
            print(f"build job {job_id}: published {path}")
            # The published copy's own version, so areas/{tile} shows it (#143.5).
            self._set_status(job_id, "published", {"version": made["version"]})
            return
        self.build_items(job_id, job, tile)

    def spread_job(self, job_id: str, job: dict[str, Any]) -> None:
        """Spread a filed species into the tiles that want it, for $0 (#199.1).

        Like an enrich job's ship step without the build: no
        research, no model, no model calls. Each listed grid
        tile is judged by code; the tiles where the species
        fits go in the pack's overlay, the rest are skipped
        with the reason or flagged for food (#220). No group
        file is written; the pack id is the job id.
        """
        from . import build as build_mod
        from . import packs as packs_mod

        assert self.world is not None
        spec = job.get("spread") or {}
        kind, what = spec.get("kind"), spec.get("what")
        if kind not in ("animal", "plant") or not what:
            self._fail(job_id, "bad_spread")
            return
        staged: list[Any] = []
        try:
            self._spread_stage(kind, str(what), staged)
        except _SpreadMissing as error:
            self._fail(job_id, str(error))
            return
        try:
            try:
                passing, made_by_tile, skipped, no_food = self._spread_tiles(
                    job_id, job, kind, str(what)
                )
            except build_mod.EnrichRemakeError as error:
                print(f"build job {job_id}: remake failed: {error}", file=sys.stderr)
                self._fail(job_id, "remake_failed")
                return
            update: dict[str, Any] = {
                "overlay": passing,
                "skipped": skipped,
                "no_food": no_food,
                "cost_usd": 0.0,
            }
            if passing:
                try:
                    overlay = packs_mod.build_tiles_overlay(
                        kind,
                        str(what),
                        self.world,
                        [made_by_tile[tile] for tile in passing],
                        passing,
                    )
                    version = packs_mod.next_pack_version(self.store, job_id)
                    pack = packs_mod.assemble_enrich_pack(
                        job_id,
                        version,
                        {"additions/tiles.json": overlay},
                        min_game=2,
                    )
                    problems = packs_mod.check_pack(pack)
                    if problems:
                        raise ValueError(problems[0])
                    update["pack"] = packs_mod.publish_pack(
                        self.store, job_id, version, pack
                    )
                except ValueError as error:
                    print(f"build job {job_id}: pack: {error}", file=sys.stderr)
                    self._fail(job_id, "bad_pack")
                    return
            self._update(job_id, update)
            print(
                f"build job {job_id}: spread {what} to {len(passing)} tiles, "
                f"{len(skipped)} skipped"
            )
            self._set_status(job_id, "published")
        finally:
            for path in staged:
                if path.is_file():
                    path.unlink()

    def _spread_stage(self, kind: str, what: str, staged: list[Any]) -> None:
        """The species file where the remake reads it (#199.2).

        The world's library first; else a live pack's copy,
        staged into the checkout for this job and removed
        after. Raises _SpreadMissing when neither has it.
        """
        import json

        assert self.world is not None
        if kind == "animal":
            candidates = [self.world / "animals" / f"{what}.json"]
        else:
            candidates = [
                self.world / "models" / "plants" / f"{what}.json",
                self.world / "models" / "trees" / f"{what}.json",
            ]
        if any(path.is_file() for path in candidates):
            return
        for _pack_id, doc in self.store.list_packs():
            if doc.get("status") != "live" or what not in (doc.get("animals") or []):
                continue
            prefix = doc.get("path") or ""
            raw, _ = self.store.read_blob(f"{prefix}manifest.json")
            if raw is None:
                continue
            try:
                files = json.loads(raw.decode("utf-8")).get("files", [])
            except ValueError:
                continue
            for entry in files:
                rel = str(entry.get("path", ""))
                target = self._spread_target(kind, what, rel)
                if target is None:
                    continue
                blob, _ = self.store.read_blob(f"{prefix}{rel}")
                if blob is None:
                    continue
                try:
                    json.loads(blob.decode("utf-8"))
                except ValueError:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
                staged.append(target)
                print(f"spread {what}: staged from {prefix}{rel}")
                return
        raise _SpreadMissing(f"no_{kind}_file")

    def _spread_target(self, kind: str, what: str, rel: str) -> Any | None:
        """Where a pack file stages in the checkout, or nothing (#199.2)."""
        assert self.world is not None
        if kind == "animal" and rel == f"animals/{what}.json":
            return self.world / "animals" / f"{what}.json"
        if kind == "plant" and rel in (
            f"plants/{what}.json",
            f"models/plants/{what}.json",
        ):
            return self.world / "models" / "plants" / f"{what}.json"
        if kind == "plant" and rel == f"models/trees/{what}.json":
            return self.world / "models" / "trees" / f"{what}.json"
        return None

    def _spread_fits(self, job_id: str, kind: str, what: str, tile: str) -> str | None:
        """None when the species fits the tile, else the skip reason (#199.1)."""
        from .grid import parse_tile
        from .species import read_cache

        assert self.world is not None
        try:
            house = self._tile_home(tile, {"items": []}, job_id)
        except _NoHome:
            return "the tile has no home"
        col, row = parse_tile(tile)
        cells = self._habitat_grid()
        labels = {cells.habitat(col, row), house.get("habitat")} - {None}
        cached = read_cache(self._species_source().cache_dir, tile)
        if cached is None:
            return "the tile has no survey to spread into"
        if kind == "plant":
            picks = {
                pick["id"]
                for pick in list(cached.get("plants", []))
                + list(cached.get("trees", []))
                if isinstance(pick, dict)
            }
            if what not in picks:
                return f"{what} is not in the tile's survey"
            return None
        entry = self._spread_entry(what)
        if entry is None:
            return f"{what} is not on the animal list"
        if labels.isdisjoint(entry.get("habitats", [])):
            return f"{what} doesn't live in {cells.habitat(col, row)}"
        present = {
            s
            for part in cached.get("parts", [])
            if isinstance(part, dict)
            for s in part.get("species", [])
        }
        if what not in present:
            return f"{what} is not in the tile's survey"
        return None

    def _spread_entry(self, what: str) -> dict[str, Any] | None:
        """The animal list's entry, or nothing off the list (#199.1)."""
        from .animal_list import find_list, load_list

        assert self.world is not None
        try:
            animals = load_list(find_list(self.world))["animals"]
        except (ValueError, KeyError):
            return None
        return next((e for e in animals if e.get("id") == what), None)

    def _spread_tiles(
        self, job_id: str, job: dict[str, Any], kind: str, what: str
    ) -> tuple[list[str], dict[str, dict[str, Any]], dict[str, str], list[str]]:
        """The listed tiles judged keyless: passing, remade, skipped, unfed."""
        import dataclasses
        import json

        from . import build as build_mod
        from .grid import group_path
        from .tiles import make_tile

        assert self.world is not None
        source = dataclasses.replace(self._species_source(), client=None)
        groups: dict[str, dict[str, Any]] = {}
        passing: list[str] = []
        made_by_tile: dict[str, dict[str, Any]] = {}
        skipped: dict[str, str] = {}
        no_food: list[str] = []
        for tile in job.get("tiles", []):
            try:
                blob_path = group_path(tile)
            except ValueError:
                skipped[tile] = "not a grid tile"
                continue
            reason = self._spread_fits(job_id, kind, what, tile)
            if reason is not None:
                skipped[tile] = reason
                continue
            if blob_path not in groups:
                raw, _ = self.store.read_blob(blob_path)
                groups[blob_path] = json.loads(raw.decode("utf-8")) if raw else {}
            old = groups[blob_path]
            made, blocked = make_tile(
                tile,
                self._habitat_grid(),
                self.world,
                builder_root=self.builder_root,
                species_source=source,
            )
            _verdict, hungry = build_mod.remake_decision(
                tile,
                (old.get("tiles") or {}).get(tile),
                (old.get("blocked") or {}).get(tile),
                made,
                blocked,
                self.world,
                what,
                spread=what,
            )
            if hungry:
                no_food.append(tile)
            elif _overlay_present(made, kind, what):
                passing.append(tile)
                assert made is not None
                made_by_tile[tile] = made
        return passing, made_by_tile, skipped, no_food

    def place_job(self, job_id: str, job: dict[str, Any]) -> None:
        """A place from one request: research, one stop, Kai's yes (#222).

        Stage A: survey the place in words, write the research
        summary with its price, and wait. A biome the world
        lacks stops the job naming its land; a no turns it
        down; a yes parks it approved under its cap for stage
        C's build. No tile: the research draws the rectangle.
        """
        status = job.get("status") or "queued"
        if status == "waiting_yes":
            self._place_answer(job_id, job)
            return
        if status == "building" and job.get("phase") == "approved":
            print(f"build job {job_id}: yes accepted; stage C builds it")
            return
        self._place_research(job_id, job)

    def _place_research(self, job_id: str, job: dict[str, Any]) -> None:
        """Survey the place, price it, and wait for yes (#222.2)."""
        import json

        from . import place as place_mod
        from .animal_list import find_list, load_list
        from .jobs import make_context
        from .ledger import CapExceeded, LedgerError
        from .pipeline import BudgetExceeded, BudgetTracker

        assert self.world is not None
        words = job.get("place")
        budget = job.get("budget_usd")
        want = job.get("want", [])
        if not isinstance(words, str) or not words.strip():
            self._fail(job_id, "bad_place")
            return
        if (
            not isinstance(budget, (int, float))
            or isinstance(budget, bool)
            or budget <= 0
        ):
            self._fail(job_id, "bad_place")
            return
        if not isinstance(want, list):
            self._fail(job_id, "bad_place")
            return
        job_dir = self.jobs_dir / job_id
        survey_path = job_dir / "survey.json"
        if survey_path.is_file():
            survey = json.loads(survey_path.read_text(encoding="utf-8"))
            spent = job.get("spent_usd", 0.0)
        else:
            self._require_lease(job_id)
            ctx = make_context(self.config, job_id, job_dir, self.world)
            if self.chat is not None:
                ctx.client = self.chat
            ctx.tracker = BudgetTracker(budget_usd=float(budget))
            animals = load_list(find_list(self.world))["animals"]
            biomes = sorted(p.stem for p in (self.world / "biomes").glob("*.json"))
            try:
                data, _searches = place_mod.ask_job_survey(
                    ctx, words, animals, biomes, [str(w) for w in want]
                )
                survey = place_mod.check_job_survey(
                    data, words, self.world, animals, [str(w) for w in want]
                )
            except place_mod.SurveyError as error:
                print(f"build job {job_id}: survey: {error}", file=sys.stderr)
                self._fail(job_id, "bad_survey")
                return
            except (BudgetExceeded, CapExceeded, LedgerError) as error:
                print(f"build job {job_id}: research: {error}", file=sys.stderr)
                self._fail(job_id, "over_budget")
                return
            place_mod.write_survey(job_dir, survey)
            spent = ctx.tracker.spent_usd
        research = {
            "tiles": survey["tiles"],
            "biome": survey["biome"],
            "centre": survey["centre"],
            "name": survey["name"],
            "wikidata": survey["wikidata"],
            "animals": survey["animals"],
            "plants": survey["plants"],
            "missing": survey["missing"],
        }
        price = round(len(survey["missing"]) * PLACE_BUILD_USD_PER_ITEM, 2)
        update: dict[str, Any] = {
            "research": research,
            "price": price,
            "spent_usd": round(float(spent), 4),
        }
        if not (self.world / "biomes" / f"{survey['biome']}.json").is_file():
            update["needed_land"] = str(survey["biome"])
            self._update(job_id, update)
            self._fail(job_id, "no_biome")
            return
        self._update(job_id, {**update, "status": "waiting_yes"})
        print(
            f"build job {job_id}: surveyed {len(survey['tiles'])} tiles, "
            f"waiting for yes"
        )

    def _place_answer(self, job_id: str, job: dict[str, Any]) -> None:
        """Kai's yes or no to a surveyed place, consumed once (#222.3)."""
        if job.get("place_no") is not None:
            self._update(job_id, {"place_no": DELETE, "place_yes": DELETE})
            self._fail(job_id, "turned_down")
            return
        yes = job.get("place_yes")
        if yes is None:
            print(f"build job {job_id}: waiting for yes")
            return
        cap = yes.get("cap") if isinstance(yes, dict) else None
        if (
            not isinstance(cap, (int, float))
            or isinstance(cap, bool)
            or not 0.01 <= cap <= 10
        ):
            print(f"build job {job_id}: place_yes needs a cap from $0.01 to $10")
            return
        self._require_lease(job_id)
        self._update(
            job_id,
            {
                "place_yes": DELETE,
                "cap_usd": float(cap),
                "phase": "approved",
                "status": "building",
            },
        )
        print(f"build job {job_id}: yes accepted at ${cap:.2f}; stage C builds it")

    def _snapshot_plan(self, job_id: str, job: dict[str, Any], tile: str) -> None:
        """Freeze the plan's approved items into the job folder.

        A re-plan (the world moved, the home shipped) must never
        strand a claimed job, so the claim's snapshot stands: the
        first write wins, and build, ship and preview read it. An
        `enrich` job has no plan document: its item is frozen from
        the job itself.
        """
        import json

        path = self.jobs_dir / job_id / "plan.json"
        if path.is_file():
            return
        if job.get("kind") == "enrich":
            snapshot = self._enrich_plan(job, tile)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
            return
        plan = self.store.get_plan(tile) or {}
        wanted = set(job.get("items", []))
        snapshot = {
            "tile": tile,
            "items": [i for i in plan.get("items", []) if i["id"] in wanted],
            "improve": [i for i in plan.get("improve", []) if i["id"] in wanted],
            "world": plan.get("world", "unknown"),
            "blocked": plan.get("blocked"),
            "snapshot_at": now_iso(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")

    def _job_plan(self, job_id: str, job: dict[str, Any], tile: str) -> dict[str, Any]:
        """The job's plan: the claim snapshot, else the live plan filtered.

        Jobs claimed before the snapshot landed fall back to the live
        plan; everything claimed since reads its frozen items. An
        `enrich` job's plan is its job's item, snapshot or live.
        """
        import json

        path = self.jobs_dir / job_id / "plan.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if job.get("kind") == "enrich":
            return self._enrich_plan(job, tile)
        plan = self.store.get_plan(tile) or {}
        wanted = set(job.get("items", []))
        return {
            "tile": tile,
            "items": [i for i in plan.get("items", []) if i["id"] in wanted],
            "improve": [i for i in plan.get("improve", []) if i["id"] in wanted],
            "world": plan.get("world", "unknown"),
            "blocked": plan.get("blocked"),
            "snapshot_at": None,
        }

    def _enrich_plan(self, job: dict[str, Any], tile: str) -> dict[str, Any]:
        """An enrich job's plan: its one item, no improve, never blocked."""
        return {
            "tile": tile,
            "items": [self._enrich_item(job)],
            "improve": [],
            "world": self.world_commit or "unknown",
            "blocked": None,
            "snapshot_at": now_iso(),
        }

    def _enrich_item(self, job: dict[str, Any]) -> dict[str, Any]:
        """The enrich job's one item, from the job's own `enrich` spec.

        `from` is the job's when given; else the list's `start_from`
        for an animal, the closest plant for a plant. An animal off
        the list keeps `from` empty and fails in its stages, like a
        build item naming one.
        """
        from .animal_list import find_list, load_list

        assert self.world is not None
        spec = job.get("enrich") or {}
        kind = spec.get("kind")
        what = spec.get("what")
        if kind not in ("animal", "plant") or not what:
            raise ValueError("an enrich job needs enrich.kind and enrich.what")
        item_id = f"enrich_{what}".replace(":", "_")
        if item_id not in set(job.get("items", [])):
            raise ValueError(f"an enrich job's items holds {item_id}")
        start = spec.get("from")
        if not start and kind == "animal":
            animals = load_list(find_list(self.world))["animals"]
            entry = next((e for e in animals if e["id"] == what), None)
            start = (entry or {}).get("start_from")
        if not start and kind == "plant":
            from .plan import closest_plant

            have = sorted(
                p.stem for p in (self.world / "models" / "plants").glob("*.json")
            )
            start = closest_plant(str(what), [], {}, have) if have else None
        return {
            "id": item_id,
            "kind": kind,
            "what": what,
            "from": start,
            "estimate_usd": float(job.get("budget_usd") or 0.0),
        }

    def build_items(self, job_id: str, job: dict[str, Any], tile: str) -> None:
        """Each approved item through the kept steps, within budget."""
        from . import build as build_mod

        assert self.world is not None
        plan = self._job_plan(job_id, job, tile)
        if plan.get("blocked"):
            self._fail(job_id, str(plan["blocked"]))
            return
        expand = build_mod.order_items(plan.get("items", []))
        improve = plan.get("improve", [])
        if not expand and not improve:
            self._fail(job_id, "no_items")
            return
        budget = float(job.get("budget_usd", 0.0))
        stopped: str | None = None
        for item in [*expand, *improve]:
            if self._item_done(job, item["id"]):
                continue
            if not self._fits_budget(item, job, budget):
                stopped = "budget"
                break
            self._require_lease(job_id)
            try:
                self.run_item(job_id, job, tile, plan, item, budget)
            except _Off:
                print(f"build job {job_id}: off switch stopped {item['id']}")
                return
            except _Paused:
                print(f"build job {job_id}: spend uncertain, {item['id']} paused")
                self._update(job_id, {"error": "paused_spend_uncertain"})
                return
            except _BudgetOut as error:
                stopped = error.code
                job = self.store.get_build_job(job_id) or job
                break
            job = self.store.get_build_job(job_id) or job
        if stopped:
            self._update(job_id, {"error": stopped})
        progress = (self.store.get_build_job(job_id) or {}).get("progress", [])
        expand_ids = {i["id"] for i in expand}
        expand_failed = [
            e for e in progress if e["id"] in expand_ids and e["status"] == "failed"
        ]
        expand_skipped = [
            e for e in progress if e["id"] in expand_ids and e["status"] == "skipped"
        ]
        if expand and len(expand_failed) + len(expand_skipped) == len(expand):
            cause = (expand_failed or expand_skipped)[0]["id"]
            self._fail(job_id, stopped or cause)
            return
        if stopped and not any(e["status"] == "checking" for e in progress):
            self._fail(job_id, stopped)
            return
        recovered = (
            {"error": None} if job.get("error") == "paused_spend_uncertain" else None
        )
        self._set_status(job_id, "checking", recovered)
        if job.get("visibility") == "private":
            self.write_preview(job_id, job, tile)

    def _settle_superseded(self, job_id: str, item: dict[str, Any], ctx: Any) -> None:
        """Count a paused attempt's unknown spend as spent, at estimate (#157).

        The resume supersedes the timed-out attempt: its held
        reservation settles at the reserved estimate, so later spend
        cannot treat the unknown as zero. Settles are idempotent; a
        missing ledger leaves its side outstanding, still counted,
        never released.
        """
        from .ledger import CombinedLedger, receipt_from_ref

        job = self.store.get_build_job(job_id) or {}
        refs: dict[str, Any] = {}
        for entry in job.get("progress", []):
            if (
                entry.get("id") == item["id"]
                and entry.get("status") == "paused_spend_uncertain"
            ):
                uncertain = entry.get("uncertain") or {}
                if isinstance(uncertain, dict):
                    refs = uncertain
        if not refs:
            return
        timed_out = ctx.open_client if refs.get("host") == "baseten" else ctx.client
        ledgers: dict[str, Any] = {}
        if timed_out is not None and timed_out.ledger is not None:
            provider = timed_out.ledger
            ledgers["provider"] = (
                provider.provider if isinstance(provider, CombinedLedger) else provider
            )
        for client in (ctx.client, ctx.open_client):
            ledger = getattr(client, "ledger", None)
            if isinstance(ledger, CombinedLedger):
                ledgers["allowance"] = ledger.allowance
                break
        for side in ("provider", "allowance"):
            ref = refs.get(side)
            ledger = ledgers.get(side)
            if not isinstance(ref, dict) or ledger is None:
                continue
            receipt = receipt_from_ref(ref)
            ledger.settle(receipt, receipt.reserved_usd)

    def run_item(
        self,
        job_id: str,
        job: dict[str, Any],
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        budget: float,
    ) -> None:
        """One item through its steps, reported, within the job budget."""
        import dataclasses

        from . import build as build_mod
        from .jobs import make_context
        from .ledger import CapExceeded, LedgerError, receipt_refs
        from .meta import MetaAuthError, SpendUncertain
        from .pipeline import BudgetExceeded, OffSwitch

        assert self.world is not None
        item_dir = self.jobs_dir / job_id / item["id"]
        item_dir.mkdir(parents=True, exist_ok=True)
        before = float((self.store.get_build_job(job_id) or {}).get("cost_usd", 0.0))
        left = max(0.0, budget - before)
        config = dataclasses.replace(self.config, job_budget_usd=left)
        ctx = make_context(config, f"{job_id}/{item['id']}", item_dir, self.world)
        if self.chat is not None:
            ctx.client = self.chat
        start = ctx.tracker.spent_usd
        self._guard_job = job_id
        try:
            self._settle_superseded(job_id, item, ctx)
            if item["kind"] == "food":
                home = self._tile_home(tile, plan, job_id)
                build_mod.build_food_item(item_dir, item, home)
            else:
                cast = None
                enrich_tiles: list[str] | None = None
                if job.get("kind") == "enrich":
                    enrich_tiles = [t for t in job.get("tiles", []) or [tile]]
                    self._write_enrich_places(job_id, item_dir, enrich_tiles, item)
                    self._label_enrich_item(item_dir, tile)
                self._run_stages(
                    ctx, job_id, tile, plan, item, item_dir, cast, enrich_tiles
                )
        except BudgetExceeded as error:
            self._close_item(
                job_id,
                item,
                item_dir,
                ctx,
                start,
                before,
                budget,
                error,
                stage="budget",
            )
            raise _BudgetOut("budget") from error
        except (CapExceeded, LedgerError, MetaAuthError) as error:
            if isinstance(error, MetaAuthError):
                code = "auth"
            elif isinstance(error, LedgerError):
                code = "ledger"
            else:
                code = "cap"
            self._close_item(
                job_id,
                item,
                item_dir,
                ctx,
                start,
                before,
                budget,
                error,
                stage=code,
            )
            raise _BudgetOut(code) from error
        except OffSwitch:
            spent = round(ctx.tracker.spent_usd - start, 6)
            self._update(job_id, {"cost_usd": before + spent})
            raise _Off()
        except SpendUncertain as error:
            self._close_item(
                job_id,
                item,
                item_dir,
                ctx,
                start,
                before,
                budget,
                error,
                status="paused_spend_uncertain",
                extra={
                    "uncertain": {
                        "host": error.host,
                        **receipt_refs(error.receipt),
                    }
                },
            )
            raise _Paused() from error
        except StaleOwner:
            raise
        except Exception as error:  # noqa: BLE001 - one item never stops the rest
            need = (
                self._failed_home_item(plan, job_id)
                if isinstance(error, _NoHome)
                else None
            )
            if need is not None:
                print(
                    f"build job {job_id}: {item['id']} skipped: needs {need}",
                    file=sys.stderr,
                )
                self._close_item(
                    job_id,
                    item,
                    item_dir,
                    ctx,
                    start,
                    before,
                    budget,
                    error,
                    status="skipped",
                    note=f"needs {need}",
                )
                return
            print(f"build job {job_id}: {item['id']} failed: {error}", file=sys.stderr)
            self._close_item(job_id, item, item_dir, ctx, start, before, budget, error)
            return
        else:
            self._close_item(job_id, item, item_dir, ctx, start, before, budget, None)
        finally:
            self._guard_job = None

    def review_job(self, job_id: str, job: dict[str, Any]) -> None:
        """Ship or drop reviewed items; publish the tile; finish (T76).

        Progress entries stay put, shipped or not: their report links
        belong to the admin console, and the game's `review` says what
        shipped. Items resolve against the claim snapshot, never the
        live plan, so a re-plan cannot strand the job.
        """
        from .build import publish_group
        from .tiles import make_tile

        assert self.world is not None
        review = job.get("review") or {}
        if not review:
            return
        if not self._claim(job_id):
            return
        if job.get("kind") == "enrich":
            self.review_enrich_job(job_id, job)
            return
        tile = self._job_tile(job_id, job)
        if tile is None:
            return
        plan = self._job_plan(job_id, job, tile)
        by_id = {i["id"]: i for i in [*plan.get("items", []), *plan.get("improve", [])]}
        if self.pull_world():  # a merged change reaches this tile (#99.4)
            self.world_commit = git_commit(self.world)
        progress = job.get("progress", [])
        pending = False
        shipped_any = False
        for entry in progress:
            verdict = (review.get(entry["id"]) or {}).get("result")
            if verdict == "not_accepted":
                print(f"build job {job_id}: dropped {entry['id']}")
            elif verdict == "shipped":
                item = by_id.get(entry["id"])
                if item is not None and self._world_has(item, plan):
                    shipped_any = True
                    print(f"build job {job_id}: shipped {entry['id']}")
                else:
                    pending = True
                    print(f"build job {job_id}: {entry['id']} waits for the world")
            else:
                pending = True
        if job.get("visibility") == "private":
            self.write_preview(job_id, job, tile)
        if pending:
            return
        if not shipped_any:
            self._fail(job_id, "nothing_shipped")
            return
        if self._tile_refused(tile, job_id, "build"):
            return
        made, blocked = make_tile(
            tile,
            self._habitat_grid(),
            self.world,
            builder_root=self.builder_root,
            species=self.species,
            species_source=self._species_source(tile),
        )
        if made is None:
            reason = (blocked or {}).get("reason", "no_home")
            self._history_blocked(job_id, tile, "build", reason)
            self._fail(job_id, reason)
            return
        self._require_lease(job_id)
        path = publish_group(
            tile,
            made,
            self.store,
            self.world_commit or "unknown",
            self._history_context(job_id, "build", tile),
        )
        print(f"build job {job_id}: published {path}")
        # The published copy's own version, so areas/{tile} shows it (#143.5).
        self._set_status(job_id, "published", {"version": made["version"]})

    def review_enrich_job(self, job_id: str, job: dict[str, Any]) -> None:
        """Overlay a shipped enrich item's passing tiles, then publish (#220).

        The ship stage's gates replace the person's review (#174):
        an item whose ship completed overlays now; `not_accepted`
        still drops it. Items reviewed the old way (a `shipped`
        verdict, in the checkout) overlay as before, into an
        overlay-only first pack version. A plant item ships its
        pack like an animal now (#188.3). Else every listed tile
        is judged by code from its cached survey (never a new
        call), the passing tiles go in the pack's overlay as a
        new pack version next to the ship's files, and the job
        carries `pack`, `overlay` and `no_food`, never `versions`.
        Tiles the thing missed for food are flagged `no_food` in
        the item's report. With per-tile place verdicts (#161.2),
        only the tiles that passed judge; the rest are listed
        with their reasons instead of failing the whole job, and
        no passed tile fails the job as `place`.
        """
        from . import build as build_mod
        from . import packs as packs_mod

        assert self.world is not None
        tile = self._job_tile(job_id, job)
        if tile is None:
            return
        if self.pull_world():
            self.world_commit = git_commit(self.world)
        plan = self._job_plan(job_id, job, tile)
        by_id = {i["id"]: i for i in plan.get("items", [])}
        review = job.get("review") or {}
        item_id = (job.get("items") or [None])[0]
        item = by_id.get(item_id or "")
        verdict = (review.get(item_id or "") or {}).get("result")
        if verdict == "not_accepted":
            print(f"build job {job_id}: dropped {item_id}")
            self._fail(job_id, "nothing_shipped")
            return
        if item is None:
            print(f"build job {job_id}: {item_id} is not in the plan")
            return
        if self._ship_completed(job_id, item_id or ""):
            pack = self._ship_pack(job_id, item_id or "")
        elif verdict == "shipped" and self._world_has(item, plan):
            pack = None
        else:
            print(f"build job {job_id}: {item_id} waits for its ship")
            return
        ship, failed, general, missing = self._place_ship(job_id, job, item)
        if general or (ship is not None and not ship):
            self._flag_place(job_id, item, failed, missing, general)
            self._fail(job_id, "place")
            return
        self._require_lease(job_id)
        try:
            passing, made_by_tile, no_food = self._remake_enrich_tiles(
                job_id, job, item, ship
            )
        except build_mod.EnrichRemakeError as error:
            print(f"build job {job_id}: remake failed: {error}", file=sys.stderr)
            self._fail(job_id, "remake_failed")
            return
        except ValueError as error:
            if "no usable cached species" not in str(error):
                raise
            print(f"build job {job_id}: remake failed: {error}", file=sys.stderr)
            self._fail(job_id, "no_cache")
            return
        enrich = job.get("enrich") or {}
        kind, what = str(enrich["kind"]), str(enrich.get("what", ""))
        self._flag_no_food(job_id, item, no_food, what)
        self._flag_place(job_id, item, failed, missing, [])
        self._history_left_out(job_id, job, ship, failed, missing)
        update: dict[str, Any] = {"overlay": passing, "no_food": no_food}
        if passing:
            try:
                overlay = packs_mod.build_tiles_overlay(
                    kind,
                    what,
                    self.world,
                    [made_by_tile[tile] for tile in passing],
                    passing,
                )
                pack_id = f"{kind}_{what}"
                files = self._overlay_pack_files(job_id, item_id or "", pack)
                files["additions/tiles.json"] = overlay
                version = packs_mod.next_pack_version(self.store, pack_id)
                packed = packs_mod.assemble_enrich_pack(
                    pack_id, version, files, min_game=2
                )
                problems = packs_mod.check_pack(packed)
                if problems:
                    raise ValueError(problems[0])
                update["pack"] = packs_mod.publish_pack(
                    self.store, pack_id, version, packed
                )
            except (ValueError, KeyError) as error:
                print(f"build job {job_id}: pack: {error}", file=sys.stderr)
                self._fail(job_id, "bad_pack")
                return
        self._update(job_id, update)
        print(f"build job {job_id}: overlaid {len(passing)} tiles")
        self._set_status(job_id, "published")

    def _overlay_pack_files(
        self, job_id: str, item_id: str, prefix: str | None
    ) -> dict[str, bytes]:
        """The ship's pack files to carry into the overlay version (#220).

        The job folder's copy first, else the published prefix's;
        nothing for an old-way item, whose first version holds
        only the overlay. Refuses loud when the ship named a pack
        that is nowhere to be found.
        """
        import json

        local = self.jobs_dir / job_id / item_id / "pack"
        if local.is_dir():
            files = {
                path.relative_to(local).as_posix(): path.read_bytes()
                for path in sorted(local.rglob("*"))
                if path.is_file()
            }
            files.pop("manifest.json", None)
            if files:
                return files
        if prefix is None:
            return {}
        raw, _ = self.store.read_blob(f"{prefix}manifest.json")
        if raw is None:
            raise ValueError(f"{prefix} holds no pack")
        files = {}
        for entry in json.loads(raw.decode("utf-8")).get("files", []):
            blob, _ = self.store.read_blob(f"{prefix}{entry['path']}")
            if blob is None:
                raise ValueError(f"{prefix}{entry['path']} is missing")
            files[str(entry["path"])] = blob
        if not files:
            raise ValueError(f"{prefix} holds no pack")
        return files

    def _ship_completed(self, job_id: str, item_id: str) -> bool:
        """Whether the item's ship stage completed (#174)."""
        import json

        state_path = self.jobs_dir / job_id / item_id / "state.json"
        if not state_path.is_file():
            return False
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except ValueError:
            return False
        return "ship" in (state.get("completed") or [])

    def _ship_pack(self, job_id: str, item_id: str) -> str | None:
        """The pack prefix the item's ship published (#176.2)."""
        import json

        state_path = self.jobs_dir / job_id / item_id / "state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        result = (state.get("results") or {}).get("ship") or {}
        return result.get("pack")

    @staticmethod
    def _needs_review(doc: dict[str, Any]) -> bool:
        """A checking job ready for its review step (#174).

        A person's verdict, or an enrich item whose ship stage
        already gated it: the checks replace the review there.
        """
        if doc.get("status") != "checking":
            return False
        return bool(doc.get("review")) or doc.get("kind") == "enrich"

    def _history_left_out(
        self,
        job_id: str,
        job: dict[str, Any],
        ship: set[str] | None,
        failed: dict[str, list[str]],
        missing: list[str],
    ) -> None:
        """Left-out tiles' history rows: refusals (#165.1).

        Nothing was written for them; the reason says which
        place checks refused.
        """
        from . import history as history_mod

        assert self.world is not None
        context = self._history_context(job_id, "enrich")
        for tile in job.get("tiles", []):
            if ship is not None and tile not in ship and tile in failed:
                reason: str | None = "; ".join(failed[tile])
            elif tile in missing:
                reason = "the intake converted no place for this tile"
            else:
                continue
            history_mod.record(
                self.store,
                history_mod.row(
                    tile,
                    "left_out",
                    job=context["job"],
                    kind=context["kind"],
                    world=self.world_commit or "unknown",
                    version=None,
                    reason=reason,
                    cost_usd=context["cost_usd"],
                ),
            )

    def _place_ship(
        self, job_id: str, job: dict[str, Any], item: dict[str, Any]
    ) -> tuple[set[str] | None, dict[str, list[str]], list[str], list[str]]:
        """Per-tile shipping from the item's place verdicts (#161.2).

        Returns the tiles that passed (None when the item
        predates place verdicts: every tile remakes as before),
        the failed tiles with their reasons, place-general
        reasons, and listed tiles with no verdict at all (the
        intake converted no place for them).
        """
        import json

        assert self.world is not None
        path = self.jobs_dir / job_id / item["id"] / "checks" / "places.json"
        if not path.is_file():
            return None, {}, [], []
        try:
            results = json.loads(path.read_text(encoding="utf-8")).get("results", [])
        except ValueError:
            results = []
        ship: set[str] = set()
        failed: dict[str, list[str]] = {}
        general: list[str] = []
        for entry in results:
            check = str(entry.get("check", ""))
            if check == "place":
                if entry.get("status") == "fail":
                    general.extend(entry.get("problems", []))
            elif check.startswith("place "):
                tile = check[len("place ") :]
                if entry.get("status") == "pass":
                    ship.add(tile)
                else:
                    failed[tile] = list(entry.get("problems", [])) or [
                        str(entry.get("reason", "its place checks failed"))
                    ]
        missing = [
            tile
            for tile in job.get("tiles", [])
            if tile not in ship and tile not in failed
        ]
        return ship, failed, general, missing

    def _flag_place(
        self,
        job_id: str,
        item: dict[str, Any],
        failed: dict[str, list[str]],
        missing: list[str],
        general: list[str],
    ) -> None:
        """Tiles their places refused into the item's report (#161.2)."""
        entries = [
            {"check": f"place {tile}", "status": "fail", "problems": reasons}
            for tile, reasons in sorted(failed.items())
        ]
        entries += [
            {
                "check": f"place {tile}",
                "status": "skip",
                "reason": "the intake converted no place for this tile",
                "problems": [],
            }
            for tile in missing
        ]
        if general:
            entries.append({"check": "place", "status": "fail", "problems": general})
        self._flag_entries(job_id, item, entries)

    def _remake_enrich_tiles(
        self,
        job_id: str,
        job: dict[str, Any],
        item: dict[str, Any],
        ship: set[str] | None = None,
    ) -> tuple[list[str], dict[str, dict[str, Any]], list[str]]:
        """The listed tiles judged keyless: passing, remade, unfed.

        With per-tile place verdicts only the passed tiles judge.
        """
        import dataclasses
        import json

        from . import build as build_mod
        from .grid import group_path
        from .tiles import make_tile

        assert self.world is not None
        source = dataclasses.replace(self._species_source(), client=None)
        groups: dict[str, dict[str, Any]] = {}
        passing: list[str] = []
        made_by_tile: dict[str, dict[str, Any]] = {}
        no_food: list[str] = []
        kind = str((job.get("enrich") or {})["kind"])
        what = str(item["what"])
        for tile in job.get("tiles", []):
            if ship is not None and tile not in ship:
                print(f"{tile} missed its place checks; left out")
                continue
            blob_path = group_path(tile)
            if blob_path not in groups:
                raw, _ = self.store.read_blob(blob_path)
                groups[blob_path] = json.loads(raw.decode("utf-8")) if raw else {}
            old = groups[blob_path]
            made, blocked = make_tile(
                tile,
                self._habitat_grid(),
                self.world,
                builder_root=self.builder_root,
                species_source=source,
            )
            _verdict, hungry = build_mod.remake_decision(
                tile,
                (old.get("tiles") or {}).get(tile),
                (old.get("blocked") or {}).get(tile),
                made,
                blocked,
                self.world,
                what,
            )
            if hungry:
                no_food.append(tile)
            elif _overlay_present(made, kind, what):
                passing.append(tile)
                assert made is not None
                made_by_tile[tile] = made
        return passing, made_by_tile, no_food

    def _flag_no_food(
        self, job_id: str, item: dict[str, Any], tiles: list[str], what: str
    ) -> None:
        """The unfed tiles into the item's report, re-uploaded."""
        self._flag_entries(
            job_id,
            item,
            [
                {
                    "check": f"no_food {tile}",
                    "status": "skip",
                    "reason": f"{what}'s food is not in this tile",
                    "problems": [],
                }
                for tile in tiles
            ],
        )

    def _flag_entries(
        self, job_id: str, item: dict[str, Any], entries: list[dict[str, Any]]
    ) -> None:
        """Remake entries into the item's report, merged, re-uploaded."""
        import json

        from . import build as build_mod
        from .report import build_report

        if not entries:
            return
        item_dir = self.jobs_dir / job_id / item["id"]
        checks = item_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        path = checks / "remake.json"
        have: list[dict[str, Any]] = []
        if path.is_file():
            try:
                have = json.loads(path.read_text(encoding="utf-8")).get("results", [])
            except ValueError:
                have = []
        seen = {entry.get("check") for entry in have}
        have.extend(entry for entry in entries if entry.get("check") not in seen)
        path.write_text(
            json.dumps(
                {
                    "ok": all(r.get("status") != "fail" for r in have),
                    "results": have,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        build_report(item_dir)
        build_mod.upload_bundle(self.store, f"review/{job_id}/{item['id']}", item_dir)

    # -- build helpers --------------------------------------------------

    def _set_status(
        self, job_id: str, status: str, extra: dict[str, Any] | None = None
    ) -> None:
        self._update(job_id, {"status": status, **(extra or {})})

    def _fail(self, job_id: str, error: str) -> None:
        print(f"build job {job_id}: failed ({error})", file=sys.stderr)
        self._update(job_id, {"status": "failed", "error": error})
        self.alert({"kind": "job_failed", "id": job_id, "error": error})

    def _history_context(
        self, job_id: str, kind: str, tile: str | None = None
    ) -> dict[str, Any]:
        """The job's half of a tile history row (#165.1, #195.2).

        With a tile, the tile's own survey spend joins the job's
        cost, so the row carries what the tile really cost.
        """
        from .ledger import FileLedger

        job = self.store.get_build_job(job_id) or {}
        cost = job.get("cost_usd", 0.0)
        total = cost if isinstance(cost, (int, float)) else 0.0
        if tile is not None:
            try:
                total += FileLedger(self.config.ledger_path).job_spent(f"survey:{tile}")
            except OSError:
                pass
        return {
            "job": job_id,
            "kind": kind,
            "cost_usd": total,
        }

    def _history_blocked(self, job_id: str, tile: str, kind: str, reason: str) -> None:
        """A blocked tile's history row; nothing was written (#165.1)."""
        from . import history as history_mod

        context = self._history_context(job_id, kind)
        history_mod.record(
            self.store,
            history_mod.row(
                tile,
                "blocked",
                job=context["job"],
                kind=context["kind"],
                world=self.world_commit or "unknown",
                version=None,
                reason=reason,
                cost_usd=context["cost_usd"],
            ),
        )

    def _job_tile(self, job_id: str, job: dict[str, Any]) -> str | None:
        """The job's tile, straight from the job (spec/02 §build job).

        Only a `build` job needs its plan; `code` makes the tile
        straight from the world, and `enrich` carries its own item.
        """
        tile = job.get("tile")
        if not tile:
            self._fail(job_id, "no_tile")
            return None
        if job.get("kind") not in ("code", "enrich"):
            if self.store.get_plan(tile) is None:
                self._fail(job_id, "no_plan")
                return None
        return tile

    def _item_done(self, job: dict[str, Any], item_id: str) -> bool:
        """An item with a finished progress entry builds no more."""
        return any(
            e["id"] == item_id and e["status"] in ("checking", "failed", "skipped")
            for e in job.get("progress", [])
        )

    def _fits_budget(
        self, item: dict[str, Any], job: dict[str, Any], budget: float
    ) -> bool:
        """An item starts only with money left; code-only food always fits."""
        if item.get("kind") == "food":
            return True
        return float(job.get("cost_usd", 0.0)) < budget

    def _label_enrich_item(self, item_dir: Path, tile: str) -> None:
        """An enrich item's report labels: its tile and kind (#150.2).

        A tile in a live place takes the place's name; otherwise
        the tile's compass name points from the nearest place,
        the way a plan would name it (#217.3). No survey answer
        is spent either way.
        """
        from . import build as build_mod
        from .enrich import place_name
        from .grid import center, parse_tile
        from .plan import plan_name

        name = ""
        if self.world is not None:
            name = place_name(tile, self.world)
        if not name:
            col, row = parse_tile(tile)
            habitat = self._habitat_grid().habitat(col, row)
            lat, lon = center(tile)
            name = plan_name(habitat, lat, lon, self.world)["en"]
        build_mod.write_state_params(
            item_dir, {"area": f"{tile} ({name})", "kind": "enrich"}
        )

    def _write_enrich_places(
        self,
        job_id: str,
        item_dir: Path,
        tiles: list[str],
        item: dict[str, Any],
    ) -> None:
        """Each target tile as its checkable place, no scaffold (#161.1).

        The converter builds the places exactly as the game does;
        the new animal or plant is seated and the food reworked,
        so the item's checks run against real tiles. A tile with
        no published doc and no code remake is left out loudly.
        The written stems go in `checks/enrich_places.json`, so
        the stages read only these files as places, never a
        build scaffold.
        """
        import json

        from .enrich import MANIFEST, ConvertError, tile_place

        assert self.world is not None
        regions = item_dir / "regions"
        regions.mkdir(parents=True, exist_ok=True)
        done: set[str] = set()
        for target in tiles:
            try:
                doc = self._tile_doc(target)
            except Exception as error:  # noqa: BLE001 - left out loudly
                print(
                    f"build job {job_id}: no place for {target}: {error}",
                    file=sys.stderr,
                )
                continue
            try:
                place = tile_place(
                    doc,
                    self.world,
                    str(item["what"]),
                    str(item["kind"]),
                    start=str(item.get("from") or ""),
                )
            except ConvertError as error:
                print(
                    f"build job {job_id}: no place for {target}: {error}",
                    file=sys.stderr,
                )
                continue
            (regions / f"tl_{target}.json").write_text(
                json.dumps(place, indent=2), encoding="utf-8"
            )
            done.add(f"tl_{target}")
        checks = item_dir / "checks"
        checks.mkdir(parents=True, exist_ok=True)
        (checks / MANIFEST).write_text(
            json.dumps({"places": sorted(done)}, indent=2), encoding="utf-8"
        )

    def _near_made_tile(self, tile: str) -> bool:
        """A made or noted tile within 3 cells (#177.6).

        Made ids come from the group blobs covering the window,
        noted tiles from the border requests with notes. Open
        sea grows from the shore outward, or where players
        wait, never alone.
        """
        import json

        from .grid import N, group_path, parse_tile, tile_id
        from .tiles import near_made_or_noted

        assert self.world is not None
        col, row = parse_tile(tile)
        made: set[str] = set()
        seen: set[str] = set()
        for dcol in range(-3, 4):
            for drow in range(-3, 4):
                ncol, nrow = (col + dcol) % N, row + drow
                if not 0 <= nrow < N:
                    continue
                path = group_path(tile_id(ncol, nrow))
                if path in seen:
                    continue
                seen.add(path)
                raw, _ = self.store.read_blob(path)
                if raw is None:
                    continue
                try:
                    group = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                made.update((group.get("tiles") or {}).keys())
        noted: set[str] = set()
        for noted_tile, doc in self.store.list_requests():
            try:
                count = int((doc or {}).get("notes", 0) or 0)
            except (TypeError, ValueError):
                continue
            if count > 0:
                noted.add(noted_tile)
        return near_made_or_noted(tile, made, noted)

    def _tile_refused(self, tile: str, job_id: str, stage: str) -> bool:
        """An open-sea tile far from everything fails before making (#177.6)."""
        from .grid import parse_tile

        col, row = parse_tile(tile)
        if self._habitat_grid().habitat(col, row) != "open_ocean":
            return False
        if self._near_made_tile(tile):
            return False
        self._history_blocked(job_id, tile, stage, "too_far")
        self._fail(job_id, "too_far")
        return True

    def _tile_doc(self, tile: str) -> dict[str, Any]:
        """The tile's doc: published first, else remade by code, keyless."""
        import dataclasses
        import json

        from .grid import group_path
        from .tiles import make_tile

        assert self.world is not None
        raw, _ = self.store.read_blob(group_path(tile))
        if raw is not None:
            try:
                group = json.loads(raw.decode("utf-8"))
            except ValueError:
                group = {}
            entry = (group.get("tiles") or {}).get(tile)
            if isinstance(entry, dict):
                return entry
        source = dataclasses.replace(self._species_source(), client=None)
        made, _blocked = make_tile(
            tile,
            self._habitat_grid(),
            self.world,
            builder_root=self.builder_root,
            species_source=source,
        )
        if made is None:
            raise ValueError(f"{tile} is unpublished and remakes blocked")
        return made

    def _run_stages(
        self,
        ctx: Any,
        job_id: str,
        tile: str,
        plan: dict[str, Any],
        item: dict[str, Any],
        item_dir: Path,
        cast: dict[str, Any] | None = None,
        enrich_tiles: list[str] | None = None,
    ) -> None:
        """Brief, scaffold and the item pipeline, staged to build."""
        from . import build as build_mod
        from .animal_list import find_list, load_list
        from .grid import center, parse_tile

        assert self.world is not None
        col, row = parse_tile(tile)
        habitat = self._habitat_grid().habitat(col, row)
        lat, lon = center(tile)
        entry = None
        if item["kind"] == "animal":
            data = load_list(find_list(self.world))
            by_id = {e["id"]: e for e in data["animals"]}
            entry = by_id.get(item["what"])
            if entry is None:
                raise ValueError(
                    f"item {item['id']} names {item['what']}, off the list"
                )
        if item["kind"] in ("plant", "animal"):
            home: dict[str, Any] | None = self._tile_home(tile, plan, job_id)
        else:
            home = None
        build_mod.prepare_item(
            item_dir, item, habitat, lat, lon, entry, home, cast, enrich_tiles
        )
        if enrich_tiles is not None:
            # An enrich item ships itself when every check passes
            # (#174): the ship stage gates and publishes its pack.
            from .stages.ship import ShipStage

            extra: list[Any] = [ShipStage(store=self.store)]
        else:
            extra = []
        pipeline = build_mod.item_pipeline(
            chat=self.chat,
            build=self.build,
            adapt=self.adapt,
            state_path=item_dir / "state.json",
            guard=self._guard,
            plant_cache=self.jobs_dir / "_plant-cache",
            extra=extra,
        )
        build_mod.run_item_stages(ctx, item_dir, pipeline)

    def _guard(self, stage: str) -> None:
        """The pipeline calls this before every stage (spec 10)."""
        from .pipeline import OffSwitch

        if not self.store.get_enabled():
            raise OffSwitch(f"off switch stopped {stage}")
        if self._guard_job is not None:
            self._require_lease(self._guard_job)

    def _close_item(
        self,
        job_id: str,
        item: dict[str, Any],
        item_dir: Path,
        ctx: Any,
        start: float,
        before: float,
        budget: float,
        error: Exception | None,
        status: str | None = None,
        note: str | None = None,
        extra: dict[str, Any] | None = None,
        stage: str = "intake",
    ) -> None:
        """Report, upload, charge and alert one finished item.

        A failed item carries its failing stage and message in
        the report and in its progress entry, even when the
        intake broke before any stage ran (#168.3). The
        pipeline's own record wins when it ran.
        """
        from . import build as build_mod
        from .report import build_report, failed_checks

        cost = round(ctx.tracker.spent_usd - start, 6)
        status = status or ("failed" if error is not None else "checking")
        failing = failed_checks(item_dir) if status == "checking" else []
        failure = ""
        if error is not None and status == "failed":
            import json

            state_path = item_dir / "state.json"
            state: dict[str, Any] = {"completed": [], "results": {}, "failed": None}
            if state_path.is_file():
                try:
                    state.update(json.loads(state_path.read_text(encoding="utf-8")))
                except ValueError:
                    pass
            recorded = state.get("failed")
            if isinstance(recorded, dict) and recorded.get("stage"):
                failure = f"{recorded['stage']}: {recorded.get('error', '')}"
            else:
                failure = f"{stage}: {error}"
                state["failed"] = {"stage": stage, "error": str(error)}
                state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        if note is not None:
            import json

            state_path = item_dir / "state.json"
            state = {"completed": [], "results": {}, "failed": None}
            if state_path.is_file():
                try:
                    state.update(json.loads(state_path.read_text(encoding="utf-8")))
                except ValueError:
                    pass
            state["skipped"] = note
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        build_mod.write_state_params(item_dir, {"budget_usd": ctx.tracker.budget_usd})
        build_report(item_dir)
        report = build_mod.upload_bundle(
            self.store, f"review/{job_id}/{item['id']}", item_dir
        )
        job = self.store.get_build_job(job_id) or {}
        progress = [e for e in job.get("progress", []) if e["id"] != item["id"]]
        entry: dict[str, Any] = {
            "id": item["id"],
            "status": status,
            "report": report,
            "cost_usd": cost,
        }
        if failure:
            entry["error"] = failure
        if failing:
            entry["checks_failed"] = failing
        if extra:
            entry.update(extra)
        progress.append(entry)
        total = round(before + cost, 6)
        self._update(job_id, {"progress": progress, "cost_usd": total})
        if budget > 0:
            for pct in (50, 80, 100):
                if before < budget * pct / 100 <= total:
                    self.alert(
                        {
                            "kind": "spending",
                            "id": job_id,
                            "pct": pct,
                            "cost_usd": total,
                        }
                    )

    def _tile_home(
        self, tile: str, plan: dict[str, Any], job_id: str
    ) -> dict[str, Any]:
        """The tile's home: the job's made one, else the world's."""
        import json

        from .grid import parse_tile
        from .tiles import (
            available_homes,
            coast_habitat,
            home_for_cell,
        )

        assert self.world is not None
        for item in plan.get("items", []):
            if item.get("kind") != "home":
                continue
            homes = self.jobs_dir / job_id / item["id"] / "homes"
            made = sorted(homes.glob("*.json"))
            if made:
                return json.loads(made[0].read_text(encoding="utf-8"))
        col, row = parse_tile(tile)
        cells = self._habitat_grid()
        homes = available_homes(self.world)
        land = cells.cell_data(col, row)[5]
        habitat = coast_habitat(
            cells.cell_data(col, row)[0],
            cells.habitat(col, row),
            land,
            homes,
            cells.cell_data(col, row)[4],
        )
        found = home_for_cell(
            cells.cell_data(col, row)[0],
            habitat,
            homes,
            cells.cell_data(col, row)[4],
        )
        if found is None:
            raise _NoHome(f"tile {tile} has no home in this checkout")
        return homes[found]

    def _failed_home_item(self, plan: dict[str, Any], job_id: str) -> str | None:
        """A failed home item the tile's home was to come from (#135.3).

        An item that needs the made home says `skipped`, not
        `failed`: it never started, the home did and broke.
        """
        if self.store is None:
            return None
        progress = {
            e["id"]: e["status"]
            for e in (self.store.get_build_job(job_id) or {}).get("progress", [])
            if isinstance(e, dict)
        }
        for item in plan.get("items", []):
            if item.get("kind") == "home" and progress.get(item["id"]) == "failed":
                return item["id"]
        return None

    def _world_has(self, item: dict[str, Any], plan: dict[str, Any]) -> bool:
        """The shipped item's files reached this checkout."""
        import json

        assert self.world is not None
        if item["kind"] == "home":
            return self._home_id_files(item)
        if item["kind"] == "animal":
            return (self.world / "animals" / f"{item['what']}.json").is_file()
        if item["kind"] == "plant":
            # The reviewed deliverable is the plant file; the built
            # .glb and preview .png are optional extras.
            return (self.world / "models" / "plants" / f"{item['what']}.json").is_file()
        home_path = self.world / "homes" / f"{item['from']}.json"
        if not home_path.is_file():
            return False
        try:
            home = json.loads(home_path.read_text(encoding="utf-8"))
        except ValueError:
            return False
        return any(
            f.get("kind") == item["what"]
            for f in home.get("food", [])
            if isinstance(f, dict)
        )

    def _home_id_files(self, item: dict[str, Any]) -> bool:
        """A home id the worker would have made exists in the world."""
        assert self.world is not None
        return any(
            path.stem.endswith(f"_{item['what']}")
            for path in (self.world / "homes").glob("*.json")
        )

    def write_preview(self, job_id: str, job: dict[str, Any], tile: str) -> None:
        """A private job's preview pack, only its judge opens (T84)."""
        from . import build as build_mod

        bundles: dict[str, dict[str, bytes]] = {}
        for entry in job.get("progress", []):
            if entry.get("status") != "checking":
                continue
            bundle: dict[str, bytes] = {}
            item_dir = self.jobs_dir / job_id / entry["id"]
            if item_dir.is_dir():
                for name in build_mod.TAKE_DIRS:
                    folder = item_dir / name
                    if folder.is_dir():
                        for path in sorted(folder.rglob("*")):
                            if path.is_file():
                                bundle[path.relative_to(item_dir).as_posix()] = (
                                    path.read_bytes()
                                )
            else:
                prefix = f"review/{job_id}/{entry['id']}/"
                for blob in self.store.list_blobs(prefix):
                    data, _ = self.store.read_blob(blob)
                    if data is not None:
                        bundle[blob.removeprefix(prefix)] = data
            if bundle:
                bundles[entry["id"]] = bundle
        if not bundles:
            return
        version = build_mod.next_preview_version(self.store, job_id)
        try:
            pack = build_mod.assemble_preview(job_id, tile, version, bundles)
        except ValueError as error:
            print(f"build job {job_id}: no preview: {error}", file=sys.stderr)
            return
        for rel, data in pack.items():
            content_type = (
                "application/json"
                if rel.endswith(".json")
                else "application/octet-stream"
            )
            self.store.write_blob(
                f"previews/{job_id}/{version}/{rel}", data, content_type
            )
        self._update(
            job_id,
            {"preview": {"pack": f"previews/{job_id}/{version}/"}},
        )
        print(f"build job {job_id}: preview {version} for its judge")

    def pull_world(self) -> bool:
        """Pull the world checkout fast-forward; True when it moved."""
        if self.world is None or not (self.world / ".git").exists():
            return False
        before = git_commit(self.world)
        try:
            proc = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=self.world,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            print(f"world pull failed: {error}", file=sys.stderr)
            return False
        if proc.returncode != 0:
            print(f"world pull failed: {proc.stderr.strip()}", file=sys.stderr)
            return False
        return git_commit(self.world) != before

    def poll(self) -> list[tuple[str, str]]:
        """One world-poll round: pull, re-plan, retry ships, resume builds."""
        queued: list[tuple[str, str]] = []
        if self.pull_world():
            self.sync_world()
            for job_id, doc in self.store.pending_build_jobs():
                if self._needs_review(doc):
                    queued.append(("build_job", job_id))
        enabled = self.store.get_enabled()
        if enabled and not self._was_enabled:
            self._reconcile(queued)
        self._was_enabled = enabled
        return queued

    def _reconcile(self, queued: list[tuple[str, str]]) -> None:
        """Re-enqueue work skipped while disabled, from the store (#158).

        Events consumed while the switch was off never come back,
        so the off-to-on transition reconciles: eligible queued
        jobs, interrupted builds, reviews that landed, and requests
        still needing a plan. Ownership stays with the handlers
        (#155); this only re-queues, deduplicated, never runs.
        """
        for job_id, doc in self.store.pending_build_jobs():
            status = doc.get("status") or "queued"
            if status in ("queued", "building") or self._needs_review(doc):
                if self._job_paused(doc) and not doc.get("operator_action"):
                    print(f"build job {job_id}: paused, waiting for an operator")
                    continue
                key = ("build_job", job_id)
                if key not in queued:
                    queued.append(key)
        for tile, _doc in self.store.list_requests():
            if self.store.get_plan(tile) is None:
                key = ("request", tile)
                if key not in queued:
                    queued.append(key)

    def sync_world(self, commit: str | None = None) -> None:
        """Re-plan every request when the world commit moved (T74).

        A new home can turn an AI plan into `code_only`. (The tile
        run on a new commit is phase 3's; the planner only re-plans.)
        """
        commit = commit if commit is not None else git_commit(self.world)
        if self.world_commit is None:
            self.world_commit = commit
            return
        if commit == self.world_commit or commit == "unknown":
            return
        print(f"world moved {self.world_commit}..{commit}: re-planning")
        self.world_commit = commit
        for tile, _doc in self.store.list_requests():
            self.handle_request(tile)

    # -- helpers ------------------------------------------------------

    def alert(self, item: dict[str, Any]) -> str:
        full = {"at": now_iso(), **item}
        return self.store.add_alert(full)

    def _species_source(self, tile: str | None = None) -> Any:
        """Tile species: the cache, and the Meta client for misses.

        With a tile, the survey spend lands under `survey:<tile>`
        so the tile's history can include it (#195.2).
        """
        from .species import source_from_config

        return source_from_config(
            self.config,
            self.builder_root,
            job_id=f"survey:{tile}" if tile else "species",
        )

    def _habitat_grid(self) -> Any:
        """The baked habitat grid, loaded once."""
        if self._grid_cache is None:
            from .habitats import load_grid

            assert self.grid_dir is not None
            self._grid_cache = load_grid(self.grid_dir)
        return self._grid_cache


@dataclass
class Listener:
    """The `--listen` loop: Firestore snapshots in, one queue, FIFO out."""

    worker: Worker
    store: Any  # FirebaseStore: watch_requests/watch_build_jobs
    events: queue.Queue = field(default_factory=queue.Queue)
    stop: threading.Event = field(default_factory=threading.Event)
    poll_seconds: float = 0

    def start(self) -> None:
        for job_id, doc in self.store.pending_build_jobs():
            if self.worker.note_build_job(job_id, doc):
                self.events.put(("build_job", job_id))
        requests_watch = self.store.watch_requests(self._on_requests)
        jobs_watch = self.store.watch_build_jobs(self._on_build_jobs)
        if self.poll_seconds > 0:
            thread = threading.Thread(target=self._poll_loop, daemon=True)
            thread.start()
        print("worker listening (Ctrl-C stops it)")
        try:
            while not self.stop.is_set():
                try:
                    kind, doc_id = self.events.get(timeout=1.0)
                except queue.Empty:
                    continue
                self._dispatch(kind, doc_id)
                self.events.task_done()
        except KeyboardInterrupt:
            pass
        finally:
            # Firestore Watches, not callbacks (#150.3).
            requests_watch.unsubscribe()
            jobs_watch.unsubscribe()

    def _poll_loop(self) -> None:
        """The world commit, every few minutes: pull, re-plan, retry."""
        while not self.stop.wait(self.poll_seconds):
            try:
                for kind, doc_id in self.worker.poll():
                    self.events.put((kind, doc_id))
            except Exception as error:  # noqa: BLE001 - the loop must live
                print(f"worker: poll failed: {error!r}", file=sys.stderr)

    def _on_requests(self, snapshots: Any, changes: Any, _read_time: Any) -> None:
        for change in changes:
            if change.type.name == "REMOVED":
                continue
            data = change.document.to_dict() or {}
            if self.worker.note_request(change.document.id, data):
                self.events.put(("request", change.document.id))

    def _on_build_jobs(self, snapshots: Any, changes: Any, _read_time: Any) -> None:
        for change in changes:
            if change.type.name == "REMOVED":
                continue
            data = change.document.to_dict() or {}
            if self.worker.note_build_job(change.document.id, data):
                self.events.put(("build_job", change.document.id))

    def _dispatch(self, kind: str, doc_id: str) -> None:
        key = (kind, doc_id)
        if key in self.worker.in_flight:
            return
        self.worker.in_flight.add(key)
        try:
            print(f"worker: {kind} {doc_id}")
            if kind == "request":
                self.worker.handle_request(doc_id)
            elif kind == "build_job":
                self.worker.handle_build_job(doc_id)
            else:
                print(f"worker: unknown kind {kind}", file=sys.stderr)
        except Exception as error:  # noqa: BLE001 - the loop must live
            print(f"worker: {kind} {doc_id} crashed: {error!r}", file=sys.stderr)
            self.worker.alert(
                {"kind": f"{kind}_failed", "id": doc_id, "error": repr(error)[:200]}
            )
        finally:
            self.worker.in_flight.discard(key)


def start_line(config: Config, store: Store, world: Path | None) -> str:
    """One line for the deploy logs: host, ledger, world, off switch.

    A `modal deploy` is checked from this line: the open-weights
    host, the ledger path with both pools' totals, the world commit
    and `config/builder.enabled`. An unreadable ledger reads
    `unknown` here; the calls it guards still refuse.
    """
    from .ledger import FileLedger, LedgerError

    try:
        meta_total = f"${FileLedger(config.ledger_path).total_spent():.2f}"
    except LedgerError:
        meta_total = "unknown"
    try:
        spent = FileLedger(config.baseten_ledger_path()).total_spent()
        baseten_total = f"${spent:.2f}"
    except LedgerError:
        baseten_total = "unknown"
    enabled = store.get_enabled()
    return (
        f"worker start: open_host={config.open_model_host}"
        f" ledger={config.ledger_path}"
        f" meta_total={meta_total} baseten_total={baseten_total}"
        f" world={git_commit(world)} enabled={str(enabled).lower()}"
    )


def listen(
    config: Config,
    jobs_dir: Path,
    world: Path | None,
    project: str | None,
    bucket: str | None = None,
    grid_dir: Path | None = None,
) -> int:
    """Connect with Application Default Credentials and listen forever."""
    from .worker_store import FirebaseStore, StoreError

    try:
        store = FirebaseStore(project, bucket)
    except StoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    worker = Worker(
        store=store,
        jobs_dir=jobs_dir,
        world=world,
        config=config,
        grid_dir=grid_dir,
    )
    print(start_line(config, store, world))
    Listener(worker=worker, store=store, poll_seconds=300).start()
    return 0
