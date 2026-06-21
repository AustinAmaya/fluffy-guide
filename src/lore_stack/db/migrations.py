import sqlite3
from datetime import datetime, timezone
from importlib import resources

# Ordered list of (version, schema resource).
MIGRATIONS = [
    ("0001_initial", "schema.sql"),
    ("0002_predicates", "migration_0002_predicates.sql"),
    ("0003_staging", "migration_0003_staging.sql"),
    ("0004_merge_suggestions", "migration_0004_merge_suggestions.sql"),
    ("0005_supersession", "migration_0005_supersession.sql"),
    ("0006_chunk_staleness", "migration_0006_chunk_staleness.sql"),
    ("0007_entity_exclusions", "migration_0007_entity_exclusions.sql"),
    ("0008_entity_merge", "migration_0008_entity_merge.sql"),
]


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
    # Seed the predicate registry (idempotent; preserves operator-added entries).
    from lore_stack.registry import seed_predicates

    seed_predicates(conn)
    return applied
