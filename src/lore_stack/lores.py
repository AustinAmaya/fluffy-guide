"""Lore-lifecycle operations over a lore home directory: name validation and
copying one lore to a new one. (Freeze/reset baselines live in frozen.py.)

A lore home is a directory of `<name>.db` files; each lore is fully isolated.
"""
import re
import sqlite3
from pathlib import Path

# A lore name becomes a filename: strict allowlist, no separators, no traversal.
LORE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class LoreError(Exception):
    pass


def lore_db_path(home_dir: str | Path, name: str) -> Path:
    if not LORE_NAME_RE.match(name or ""):
        raise LoreError(f"invalid lore name {name!r}")
    return Path(home_dir) / f"{name}.db"


def list_lore_names(home_dir: str | Path) -> list[str]:
    home = Path(home_dir)
    if not home.exists():
        return []
    return [p.stem for p in sorted(home.glob("*.db"))]


def copy_lore(home_dir: str | Path, src_name: str, dst_name: str) -> None:
    """Duplicate src lore's database into a new lore via the SQLite backup API.

    The copy is independent and starts with a fresh (empty) snapshot history — the
    source's `.snapshots/` and `.frozen/` are not copied.
    """
    src = lore_db_path(home_dir, src_name)
    dst = lore_db_path(home_dir, dst_name)
    if not src.exists():
        raise LoreError(f"unknown lore {src_name!r}")
    if dst.exists():
        raise LoreError(f"lore {dst_name!r} already exists")
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dst))
    try:
        with target:
            source.backup(target)
    finally:
        source.close()
        target.close()
