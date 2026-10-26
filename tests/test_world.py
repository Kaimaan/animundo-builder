"""World overlay: a checkout view the checkers can run against."""

from __future__ import annotations

from pathlib import Path

from builder.world import overlay, run_checker


def _world(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    (world / "animals").mkdir(parents=True)
    (world / "animals" / "fox.json").write_text('{"latin": "Vulpes vulpes"}')
    (world / ".git").mkdir(parents=True)
    (world / ".git" / "huge").write_text("not copied")
    (world / "tools").mkdir(parents=True)
    (world / "tools" / "echo.py").write_text(
        "import sys\nprint('args:', ' '.join(sys.argv[1:]))\n"
    )
    return world


def test_overlay_shows_base_and_overrides(tmp_path: Path) -> None:
    world = _world(tmp_path)
    with overlay(world, {"animals/zz_new.json": '{"latin": "X y"}'}) as root:
        assert (root / "animals" / "fox.json").is_symlink()
        assert (root / "animals" / "zz_new.json").is_file()
        assert not (root / "animals" / "zz_new.json").is_symlink()
        assert not (root / ".git").exists()
    assert not (world / "animals" / "zz_new.json").exists()
    assert not root.exists()


def test_run_checker(tmp_path: Path) -> None:
    world = _world(tmp_path)
    proc = run_checker(world, "echo.py", "--root", "somewhere")
    assert proc.returncode == 0
    assert proc.stdout.strip() == "args: --root somewhere"
