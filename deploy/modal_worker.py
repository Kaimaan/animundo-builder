"""The builder's worker on Modal (T77, #99 shape A + #101).

One always-on container runs `builder worker --listen` unchanged: it
catches up on start (pending build jobs) and follows Firestore
snapshots, so planning, building, previews and review ship/pass
through the same code as on a laptop. A daily restart covers Modal's
24 h function limit; the atomic claim makes an overlap safe.

The world is cloned fresh at container start (it is public, so no
key), and the worker pulls it before every tile it makes (#99.4):
a merged world change reaches the next tile within one pull.
Baked habitat grids ride in the image; job folders live on the
`animundo-jobs` Volume, committed after each round.

Kai deploys (the Modal account and secrets are his)::

    modal deploy deploy/modal_worker.py
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import modal

BUILDER = pathlib.Path(__file__).resolve().parent.parent
WORLD_URL = "https://github.com/Kaimaan/animundo-world.git"
PROJECT = "animundo-dev"
BUCKET = "animundo-dev.firebasestorage.app"

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
    .run_commands("pip install --no-cache-dir /src/builder")
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
    _credentials()
    subprocess.run(worker_command(_world()), check=False)
    jobs.commit()
