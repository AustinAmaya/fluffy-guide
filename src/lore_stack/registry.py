"""The predicate registry: a controlled vocabulary over predicates.

Turns free-text extractor predicates into governed ontology terms. Each
predicate declares cardinality (single vs multi-valued), a persistence class,
and accepted aliases. Writeback consults the registry to normalize spellings,
decide whether contradictions are possible, and gate auto-canonization.

LLM proposes (claims), code disposes (this registry decides the rules).

Two vocabularies, governed differently (see the ontology spec):
- **Relationships** (`range='entity'`, a graph edge to another entity) are a
  CLOSED, fixed set — the 11 child-legible predicates seeded in predicates.json.
  An entity-object claim whose predicate is not a registered relationship is
  rejected at writeback; relationships are never auto-registered. Use
  `is_registered_relationship` to gate them.
- **Attributes** (`range='text'`, a literal fact about one entity — profession,
  species, ...) are an OPEN vocabulary: unregistered ones still form soft facts
  (they just can't auto-canonize), and operator edits auto-register them.
"""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from typing import Optional

from lore_stack.writeback.engine import normalize


@dataclass(frozen=True)
class PredicateInfo:
    predicate_id: str
    cardinality: str       # 'single' | 'multi'
    persistence: str       # 'permanent' | 'state' | 'episodic'
    range: str             # 'text' | 'entity'
    symmetry: str          # 'directed' | 'symmetric'
    registered_by: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed_predicates(conn: sqlite3.Connection) -> int:
    """Populate the registry from the shipped seed file, idempotently. Existing
    rows (including operator-registered ones) are never overwritten."""
    raw = resources.files("lore_stack.db").joinpath("predicates.json").read_text(encoding="utf-8")
    seed = json.loads(raw)
    added = 0
    now = _now()
    for entry in seed:
        pid = entry["predicate_id"]
        cur = conn.execute(
            "INSERT OR IGNORE INTO predicates (predicate_id, aliases_json, domain_json,"
            " range, cardinality, persistence, symmetry, inverse_of, registered_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'seed', ?)",
            (pid, json.dumps(entry.get("aliases", [])), json.dumps(entry.get("domain", [])),
             entry.get("range", "text"), entry["cardinality"], entry["persistence"],
             entry.get("symmetry", "directed"), entry.get("inverse_of"), now),
        )
        if cur.rowcount:
            added += 1
        # Aliases: the canonical id is itself an alias; plus declared synonyms.
        for alias in [pid, *entry.get("aliases", [])]:
            conn.execute(
                "INSERT OR IGNORE INTO predicate_aliases (normalized_alias, predicate_id)"
                " VALUES (?, ?)",
                (normalize(alias), pid),
            )
    conn.commit()
    return added


def _registry_present(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='predicates'"
    ).fetchone() is not None


def lookup(conn: sqlite3.Connection, raw_predicate: str) -> Optional[PredicateInfo]:
    """Resolve a raw predicate (by id or alias) to its registry entry, or None.

    Returns None if the registry tables don't exist yet (a pre-0002 database):
    the system then treats every predicate as unregistered, which is the
    conservative behavior (no normalization, no auto-canonization) until the
    migration runs.
    """
    if not _registry_present(conn):
        return None
    norm = normalize(raw_predicate)
    row = conn.execute(
        "SELECT p.* FROM predicate_aliases a JOIN predicates p"
        " ON p.predicate_id = a.predicate_id WHERE a.normalized_alias = ?",
        (norm,),
    ).fetchone()
    if row is None:
        # Fall back to a direct id match (covers ids never aliased for any reason).
        row = conn.execute(
            "SELECT * FROM predicates WHERE predicate_id = ?", (norm,)
        ).fetchone()
    if row is None:
        return None
    return PredicateInfo(
        predicate_id=row["predicate_id"],
        cardinality=row["cardinality"],
        persistence=row["persistence"],
        range=row["range"],
        symmetry=row["symmetry"],
        registered_by=row["registered_by"],
    )


def is_registered_relationship(conn: sqlite3.Connection, raw_predicate: str) -> bool:
    """True iff the predicate resolves (by id or alias) to a registered
    relationship — one whose `range` is 'entity'. This is the closed-set guard:
    only these predicates may be the edge of an entity-object claim or a
    relationship manual-edit. Attributes (`range='text'`) return False here and
    are governed by the open-vocabulary rules instead."""
    info = lookup(conn, raw_predicate)
    return info is not None and info.range == "entity"


def ensure_registered(
    conn: sqlite3.Connection,
    predicate: str,
    *,
    registered_by: str,
    cardinality: str = "single",
    persistence: str = "permanent",
    range_: str = "text",
) -> PredicateInfo:
    """Register a predicate if absent (operator/extractor authored), returning its
    info. Used by the manual-edit path: an operator using a new predicate is
    defining it. No-op if already registered."""
    existing = lookup(conn, predicate)
    if existing is not None:
        return existing
    pid = normalize(predicate)
    if not _registry_present(conn):
        # No registry to write to (pre-0002); report the would-be registration
        # without persisting. Manual edits still succeed; the predicate becomes
        # governed once the migration runs.
        return PredicateInfo(pid, cardinality, persistence, range_, "directed", registered_by)
    now = _now()
    conn.execute(
        "INSERT INTO predicates (predicate_id, aliases_json, domain_json, range,"
        " cardinality, persistence, symmetry, inverse_of, registered_by, created_at)"
        " VALUES (?, '[]', '[]', ?, ?, ?, 'directed', NULL, ?, ?)",
        (pid, range_, cardinality, persistence, registered_by, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO predicate_aliases (normalized_alias, predicate_id)"
        " VALUES (?, ?)",
        (pid, pid),
    )
    return PredicateInfo(pid, cardinality, persistence, range_, "directed", registered_by)
