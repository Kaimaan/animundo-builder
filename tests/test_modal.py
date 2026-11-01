"""The Modal deploy: imports with no credentials, runs the real CLI. No key."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "modal_worker.py"


def _load(monkeypatch: Any) -> Any:
    """The deploy file with every credential scrubbed from the air."""
    for name in list(os.environ):
        if name.startswith(
            (
                "MODAL_",
                "FIREBASE_",
                "GOOGLE_",
                "MODEL_",
                "BASETEN_",
                "BUILDER_",
            )
        ):
            monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("modal_worker", DEPLOY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deploy_imports_without_credentials(monkeypatch: Any) -> None:
    """#99.6: the deploy file imports with no Modal credentials."""
    module = _load(monkeypatch)
    assert module.PROJECT == "animundo-dev"
    assert module.BUCKET == "animundo-dev.firebasestorage.app"


def test_deploy_worker_command_is_real_cli(monkeypatch: Any) -> None:
    """#99.6: the deployed command line is one cli.py accepts."""
    from builder.cli import build_parser

    module = _load(monkeypatch)
    cmd = module.worker_command("/tmp/world")
    assert cmd[0] == "builder"
    args = build_parser().parse_args(cmd[1:])
    assert args.command == "worker"
    assert args.listen is True
    assert args.world == "/tmp/world"
    assert args.jobs_dir == "/jobs"
    assert args.habitat_grid == "/grids"
    assert args.project == "animundo-dev"
    assert args.storage_bucket == "animundo-dev.firebasestorage.app"


def test_code_job_pulls_before_its_tile(tmp_path: Path, monkeypatch: Any) -> None:
    """#99.4: a merged world change reaches the tile a code job makes."""
    import fake_world
    from fake_store import FakeStore
    from test_worker import _code_job, _code_plan, _flow_worker

    store = FakeStore()
    store.plans["z10_0581_0294"] = _code_plan("z10_0581_0294")
    store.build_jobs["j1"] = _code_job()
    order: list[str] = []
    flow = _flow_worker(tmp_path, store, fake_world.boreal_3x3())
    monkeypatch.setattr(flow, "pull_world", lambda: (order.append("pull"), False)[1])
    import builder.tiles

    real_make_tile = builder.tiles.make_tile

    def spying_make_tile(*args: Any, **kwargs: Any) -> Any:
        order.append("tile")
        return real_make_tile(*args, **kwargs)

    monkeypatch.setattr(builder.tiles, "make_tile", spying_make_tile)
    flow.handle_build_job("j1")
    assert order == ["pull", "tile"]
    assert store.build_jobs["j1"]["status"] == "published"


def test_deploy_sends_open_weights_to_baseten(monkeypatch: Any) -> None:
    """#133.1: the live worker spends Baseten's pool, not Meta's."""
    module = _load(monkeypatch)
    assert module.WORKER_ENV["BUILDER_OPEN_MODEL_HOST"] == "baseten"


def test_deploy_keeps_the_ledger_on_the_volume(monkeypatch: Any) -> None:
    """#133.2: the spend ledger sits on the jobs Volume."""
    module = _load(monkeypatch)
    assert module.WORKER_ENV["BUILDER_LEDGER_PATH"] == "/jobs/spend-ledger.json"


def test_deploy_keeps_the_species_cache_on_the_volume(monkeypatch: Any) -> None:
    """#131.4: the species cache sits on the jobs Volume."""
    module = _load(monkeypatch)
    assert module.WORKER_ENV["BUILDER_SPECIES_CACHE_DIR"] == "/jobs/species-cache"


def test_deploy_installs_editable_for_its_data(monkeypatch: Any) -> None:
    """#133: a wheel ships no contracts, so the image installs editable."""
    module = _load(monkeypatch)
    assert "-e" in module.PIP_INSTALL_CMD.split()


def _image_files() -> list[tuple[Path, str]]:
    """The image's named local files, out of the deploy file (#185).

    Reads `modal_worker.py` as text, so the test holds without a
    Modal account: every `add_local_file` local path resolves
    against this checkout, with its image path beside it.
    """
    import ast

    root = DEPLOY.parent.parent
    tree = ast.parse(DEPLOY.read_text(encoding="utf-8"))

    def local(node: ast.AST) -> Path:
        if isinstance(node, ast.Name) and node.id == "BUILDER":
            return root
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and isinstance(node.right.value, str)
        ):
            return local(node.left) / node.right.value
        raise AssertionError(f"add_local_file names no plain path: {ast.dump(node)}")

    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_local_file"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ):
            found.append((local(node.args[0]), node.args[1].value))
    return found


def test_deploy_names_files_that_exist() -> None:
    """#185: every file the image names exists in this checkout."""
    files = _image_files()
    assert files, "the image names no local files"
    for path, remote in files:
        assert path.is_file(), f"{remote} names {path}, which is missing"
    remotes = [remote for _, remote in files]
    assert "/grids/z10.json.gz" in remotes
    assert "/src/builder/data/seas.json.gz" in remotes
    assert not any("ease2" in remote for remote in remotes)


def test_supervise_commits_while_the_worker_runs(monkeypatch: Any) -> None:
    """#133.2: the Volume commits during the run, and once at the end."""
    module = _load(monkeypatch)
    commits: list[str] = []
    sleeps: list[int] = []

    class Proc:
        polls = 0

        def poll(self) -> int | None:
            self.polls += 1
            return None if self.polls < 3 else 0

        def wait(self) -> int:
            return 0

    module.supervise(Proc(), lambda: commits.append("commit"), 300, sleeps.append)
    assert sleeps == [300, 300]
    assert commits == ["commit", "commit", "commit"]
