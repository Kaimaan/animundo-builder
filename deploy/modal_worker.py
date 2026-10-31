"""The builder's worker on Modal (T77, #99 shape A + #101).

One always-on container runs `builder worker --listen` unchanged: it
catches up on start (pending build jobs) and follows Firestore
snapshots, so planning, building, previews and review ship/pass
through the same code as on a laptop. A daily restart covers Modal's
24 h function limit; the atomic claim makes an overlap safe.

The world is cloned fresh at container start (it is public, so no
key), and the worker pulls it before every tile it makes (#99.4):
a merged world change reaches the next tile within one pull.
Baked habitat grids ride in the image; job folders and the spend
ledger live on the `animundo-jobs` Volume, committed every few
minutes while the worker runs.

Kai deploys (the Modal account and secrets are his)::

    modal deploy deploy/modal_worker.py

Restart notes: the daily 03:17 cron restarts the container by
itself. To stop the old container by hand, `modal container
stop` needs `-y` when there is no terminal; without it the
command asks, hears nothing, and stops nothing (#172.3).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Any

import modal

BUILDER = pathlib.Path(__file__).resolve().parent.parent
WORLD_URL = "https://github.com/Kaimaan/animundo-world.git"
PROJECT = "animundo-dev"
BUCKET = "animundo-dev.firebasestorage.app"

#: The live worker's environment, baked into the image: open-weights
#: calls go to Baseten (its $100 pool, not the Meta credit), and the
#: spend ledger sits on the jobs Volume so it survives restarts.
WORKER_ENV = {
    "BUILDER_OPEN_MODEL_HOST": "baseten",
    "BUILDER_LEDGER_PATH": "/jobs/spend-ledger.json",
    "BUILDER_SPECIES_CACHE_DIR": "/jobs/species-cache",
}

#: The builder installs editable, not as a wheel: the wheel ships
#: only the `builder/` package, but the worker reads the contracts,
#: schemas, prompts and `exclusions.json` from the checkout next to
#: it. The image is immutable, so editable changes nothing at run
#: time; without it every plan and build fails on a missing schema.
PIP_INSTALL_CMD = "pip install --no-cache-dir -e /src/builder"

#: Volume commits while the worker runs: the ledger and job folders
#: stay durable between daily restarts, not only at the very end.
COMMIT_EVERY_SECONDS = 300

image = (
    modal.Image.debian_slim(python_version="3.12")
    # The same libraries and Blender as the world's CI
    # (animundo-world/.github/workflows/check.yml).
    .apt_install(
        "curl",
        "xz-utils",
        "git",
        "libxi6",
        "libxkbcommon0",
        "libxrender1",
        "libxxf86vm1",
        "libxfixes3",
        "libsm6",
        "libgl1",
        "libegl1",
    )
    .run_commands(
        # download.blender.org can refuse cloud IPs; fall back to mirrors.
        "for u in https://download.blender.org/release"
        " https://mirrors.ocf.berkeley.edu/blender/release"
        " https://ftp.halifax.rwth-aachen.de/blender/release; do"
        " curl -fsSL -A 'Mozilla/5.0' -o /tmp/b.tar.xz"
        " $u/Blender5.2/blender-5.2.2-linux-x64.tar.xz"
        " && echo got $u && break; echo failed $u; done",
        "test -s /tmp/b.tar.xz",
        "mkdir -p /opt/blender"
        " && tar -xJf /tmp/b.tar.xz -C /opt/blender --strip-components=1"
        " && rm /tmp/b.tar.xz",
        "ln -s /opt/blender/blender /usr/local/bin/blender",
        "blender --version | head -1",
    )
    # The builder, installed from this checkout. No keys, no job
    # folders (764M locally), no venv, no history, no raw data:
    # the baked grids below are all the worker reads.
    .add_local_dir(
        BUILDER,
        "/src/builder",
        copy=True,
        ignore=[
            ".env",
            ".env.*",
            ".venv",
            "jobs",
            "data",
            ".git",
            "**/__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "**/node_modules",
        ],
    )
    .run_commands(PIP_INSTALL_CMD)
    # .env() is a build step: it must come before the add_local_*
    # calls without copy, or the deploy fails (#134).
    .env(WORKER_ENV)
    .add_local_file(
        BUILDER / "data" / "habitat-grid" / "ease2_n25.json.gz",
        "/grids/ease2_n25.json.gz",
    )
    .add_local_file(
        BUILDER / "data" / "habitat-grid" / "ease2_s25.json.gz",
        "/grids/ease2_s25.json.gz",
    )
)

app = modal.App("animundo-worker")

# Job folders outlive a container: resume and reports read them.
jobs = modal.Volume.from_name("animundo-jobs", create_if_missing=True)
secrets = [
    modal.Secret.from_name("animundo-builder"),  # MODEL_API_KEY, BASETEN_API_KEY
    modal.Secret.from_name("animundo-firebase"),  # FIREBASE_SERVICE_ACCOUNT_JSON
]


def worker_command(world: str) -> list[str]:
    """The worker exactly as the container runs it (T77, #99.2)."""
    return [
        "builder",
        "worker",
        "--listen",
        "--world",
        world,
        "--jobs-dir",
        "/jobs",
        "--habitat-grid",
        "/grids",
        "--project",
        PROJECT,
        "--storage-bucket",
        BUCKET,
    ]


def _credentials() -> None:
    """Application Default Credentials from the secret, as a file.

    The JSON is popped from the environment so a Blender child
    process never inherits it.
    """
    path = pathlib.Path("/tmp/sa.json")
    path.write_text(os.environ.pop("FIREBASE_SERVICE_ACCOUNT_JSON"))
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)


def _world() -> str:
    """A fresh writable world: shallow clone at container start."""
    target = "/tmp/world"
    subprocess.run(
        ["git", "clone", "--depth", "1", WORLD_URL, target],
        check=True,
        capture_output=True,
        text=True,
    )
    return target


def supervise(proc: subprocess.Popen, commit: Any, every: int, sleep: Any) -> None:
    """Commit the Volume while the worker runs, and once at the end.

    `proc` is the running worker, `commit` the Volume's commit,
    `sleep` a seconds-sleeper. A crash still commits on the way out.
    """
    try:
        while proc.poll() is None:
            sleep(every)
            commit()
    finally:
        proc.wait()
        commit()


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/jobs": jobs},
    cpu=0.25,
    memory=(1024, 4096),  # billed 0.25 core + 1 GiB idle; Blender bursts
    timeout=24 * 3600,
    schedule=modal.Cron("17 3 * * *"),  # daily restart, off the :00 rush
    max_containers=1,
)
def listen() -> None:
    import time

    _credentials()
    proc = subprocess.Popen(worker_command(_world()))
    supervise(proc, jobs.commit, COMMIT_EVERY_SECONDS, time.sleep)
