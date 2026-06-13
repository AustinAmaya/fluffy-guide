"""Frozen lore baselines: a pristine, complete snapshot of a lore — both its
database AND its snapshot history — that the operator can hard-reset to.

Unlike rollback (which preserves history and is itself undoable), a frozen reset
is a *full* restore: lore content and the entire snapshot history revert to the
baseline, discarding everything since the freeze. The frozen baseline is itself
the recovery point. Used for the demo seed lores so the operator can play freely
and snap back to pristine.

Layout: <home>/.frozen/<name>/lore.db  +  <home>/.frozen/<name>/snapshots/
"""
import shutil
import sqlite3
from pathlib import Path

from lore_stack.lores import LORE_NAME_RE, LoreError, lore_db_path


def frozen_dir(home_dir: str | Path, name: str) -> Path:
    if not LORE_NAME_RE.match(name or ""):
        raise LoreError(f"invalid lore name {name!r}")
    return Path(home_dir) / ".frozen" / name


def _live_snapshots_dir(home_dir: str | Path, name: str) -> Path:
    return Path(home_dir) / ".snapshots" / name


def has_frozen(home_dir: str | Path, name: str) -> bool:
    try:
        return (frozen_dir(home_dir, name) / "lore.db").exists()
    except LoreError:
        return False


def _copy_db(src: Path, dst: Path) -> None:
    """Copy a SQLite DB via the online backup API (safe with open readers)."""
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dst))
    try:
        with target:
            source.backup(target)
    finally:
        source.close()
        target.close()


def freeze(home_dir: str | Path, name: str) -> None:
    """Capture the lore's current DB and snapshot history as its frozen baseline,
    overwriting any prior baseline."""
    src_db = lore_db_path(home_dir, name)
    if not src_db.exists():
        raise LoreError(f"unknown lore {name!r}")
    fdir = frozen_dir(home_dir, name)
    if fdir.exists():
        shutil.rmtree(fdir)
    fdir.mkdir(parents=True)
    _copy_db(src_db, fdir / "lore.db")
    live_snaps = _live_snapshots_dir(home_dir, name)
    if live_snaps.exists():
        shutil.copytree(live_snaps, fdir / "snapshots")


def reset(home_dir: str | Path, name: str) -> None:
    """Full hard restore from the frozen baseline: the live DB and the entire
    snapshot history revert to the baseline, discarding everything since."""
    fdir = frozen_dir(home_dir, name)
    baseline_db = fdir / "lore.db"
    if not baseline_db.exists():
        raise LoreError(f"lore {name!r} has no frozen baseline")
    _copy_db(baseline_db, lore_db_path(home_dir, name))
    # Replace the live snapshot history wholesale with the frozen one.
    live_snaps = _live_snapshots_dir(home_dir, name)
    if live_snaps.exists():
        shutil.rmtree(live_snaps)
    baseline_snaps = fdir / "snapshots"
    if baseline_snaps.exists():
        shutil.copytree(baseline_snaps, live_snaps)
