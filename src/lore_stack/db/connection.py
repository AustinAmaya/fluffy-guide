import sqlite3
from pathlib import Path


class LoreConnection(sqlite3.Connection):
    """A sqlite connection that remembers its file path and whether mutating
    library operations should auto-snapshot before writing. The base
    sqlite3.Connection cannot carry attributes; a subclass can."""

    lore_path: str | None = None
    auto_snapshot: bool = False


def connect(db_path: str | Path, *, auto_snapshot: bool = False) -> LoreConnection:
    """Open the lore DB with foreign keys enforced on this connection.

    auto_snapshot=True makes mutating writeback operations capture a snapshot of
    the prior state first (used by the CLI and web API; off by default so tests
    and read paths pay nothing). Pure reads never snapshot regardless.
    """
    conn = sqlite3.connect(str(db_path), factory=LoreConnection)
    conn.row_factory = sqlite3.Row
    conn.lore_path = str(db_path)
    conn.auto_snapshot = auto_snapshot
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
