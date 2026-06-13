import sqlite3
from datetime import datetime, timezone
from importlib import resources

# Ordered list of (version, schema resource). One migration so far: the full schema.
MIGRATIONS = [("0001_initial", "schema.sql")]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def applied_versions(conn: sqlite3.Connection) -> list[str]:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if row is None:
        return []
    return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


def init_db(conn: sqlite3.Connection) -> list[str]:
    """Apply all unapplied migrations; returns versions applied in this call."""
    done = set(applied_versions(conn))
    applied = []
    for version, resource in MIGRATIONS:
        if version in done:
            continue
        sql = resources.files("lore_stack.db").joinpath(resource).read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, _now()),
        )
        applied.append(version)
    conn.commit()
    return applied
