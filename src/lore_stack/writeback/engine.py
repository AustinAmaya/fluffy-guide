"""Writeback + conservative canonization engine.

Policy (fixed by spec):
- First mention => provisional entity, candidate claims, soft facts.
- Corroboration across >=2 distinct stories at confidence >=0.9 => canonical.
- Contradiction of a canonical fact => open adjudication item, canon unchanged.
- Motif claims => facts with status='motif', never auto-promoted.
- Manual (operator) edits => immediately canonical with a 'manual' source,
  bypassing adjudication; the prior value is preserved as deprecated history.
- All deletes are soft: status flips, rows survive.
- Re-applying a delta with an already-seen checksum is a no-op.

All IDs are content-derived so identical inputs yield identical DB states
(timestamps aside).
"""
import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from lore_stack.models.delta import ClaimInput, LoreDelta, WritebackReport
from lore_stack.seams.embedder import Embedder, pack_vector

PROMOTION_CONFIDENCE = 0.9
SOFT_FACT_CONFIDENCE = 0.7


class WritebackError(Exception):
    """Raised when a delta cannot be applied; the DB is left untouched."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "unnamed"


def token_estimate(text: str) -> int:
    return math.ceil(len(text) / 4)


def _short_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def delta_checksum(delta: LoreDelta) -> str:
    return hashlib.sha256(delta.model_dump_json().encode("utf-8")).hexdigest()


def resolve_entity(conn: sqlite3.Connection, name_or_slug: str) -> Optional[str]:
    """Resolve a name, slug, or alias to an existing entity_id (aliases never fork)."""
    slug = slugify(name_or_slug)
    row = conn.execute("SELECT entity_id FROM entities WHERE slug = ?", (slug,)).fetchone()
    if row:
        return row["entity_id"]
    for norm in dict.fromkeys(
        (normalize(name_or_slug), normalize(name_or_slug.replace("-", " ")))
    ):
        row = conn.execute(
            "SELECT entity_id FROM entity_aliases WHERE normalized_alias = ?", (norm,)
        ).fetchone()
        if row:
            return row["entity_id"]
    return None


def _add_alias(conn: sqlite3.Connection, entity_id: str, alias: str, alias_type: str) -> int:
    """Insert an alias unless its normalized form already maps somewhere. Returns rows added."""
    norm = normalize(alias)
    if not norm:
        return 0
    cur = conn.execute(
        "INSERT OR IGNORE INTO entity_aliases (entity_id, alias, normalized_alias, alias_type)"
        " VALUES (?, ?, ?, ?)",
        (entity_id, alias, norm, alias_type),
    )
    return cur.rowcount


def _object_norm(object_entity_id: Optional[str], object_literal: Optional[str]) -> str:
    return object_entity_id if object_entity_id is not None else normalize(object_literal or "")


def apply_delta(
    conn: sqlite3.Connection,
    delta: LoreDelta,
    *,
    story_text: Optional[str] = None,
    source_kind: str = "story",
    source_uri: Optional[str] = None,
    embedder: Optional[Embedder] = None,
) -> WritebackReport:
    """Apply one validated LoreDelta inside a single transaction.

    Any failure rolls back completely: no partial writes.
    """
    checksum = delta_checksum(delta)
    existing = conn.execute(
        "SELECT source_id FROM sources WHERE checksum = ?", (checksum,)
    ).fetchone()
    if existing:
        return WritebackReport(story_id=delta.story_id, noop=True)

    report = WritebackReport(story_id=delta.story_id)
    now = _now()
    try:
        with conn:
            _apply_inner(conn, delta, checksum, story_text, source_kind, source_uri,
                         embedder, report, now)
    except sqlite3.IntegrityError as exc:
        raise WritebackError(f"delta violates a database constraint: {exc}") from exc
    return report


def _apply_inner(conn, delta, checksum, story_text, source_kind, source_uri,
                 embedder, report, now) -> None:
    source_id = f"src_{checksum[:12]}"
    conn.execute(
        "INSERT INTO sources (source_id, source_kind, uri, checksum, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (source_id, source_kind, source_uri, checksum, now),
    )

    story_row = conn.execute(
        "SELECT story_id FROM story_runs WHERE story_id = ?", (delta.story_id,)
    ).fetchone()
    if story_row:
        raise WritebackError(
            f"story_id {delta.story_id!r} already exists with different content"
        )
    conn.execute(
        "INSERT INTO story_runs (story_id, source_id, title, prompt_text, story_text,"
        " model_provider, model_name, extractor_model, extraction_status, extraction_json,"
        " created_at) VALUES (?, ?, ?, NULL, ?, NULL, NULL, NULL, 'ok', ?, ?)",
        (delta.story_id, source_id, delta.story_title,
         story_text if story_text is not None else delta.story_summary,
         delta.model_dump_json(), now),
    )

    # --- entities ---
    for upsert in delta.entities:
        entity_id = None
        for candidate in [upsert.slug, upsert.display_name, *upsert.aliases]:
            entity_id = resolve_entity(conn, candidate)
            if entity_id:
                break
        if entity_id is None:
            slug = slugify(upsert.slug)
            entity_id = f"ent_{slug}"
            conn.execute(
                "INSERT INTO entities (entity_id, kind, slug, display_name, status, summary,"
                " description, canonical_confidence, created_from_story_id, created_at,"
                " updated_at) VALUES (?, ?, ?, ?, 'provisional', ?, NULL, ?, ?, ?, ?)",
                (entity_id, upsert.kind, slug, upsert.display_name, upsert.summary,
                 upsert.confidence, delta.story_id, now, now),
            )
            report.entities_created.append(entity_id)
        else:
            report.entities_resolved.append(entity_id)
        report.aliases_added += _add_alias(conn, entity_id, upsert.display_name, "primary")
        for alias in upsert.aliases:
            report.aliases_added += _add_alias(conn, entity_id, alias, "surface")
        conn.execute(
            "INSERT OR IGNORE INTO story_entities (story_id, entity_id, role, mention_count,"
            " salience) VALUES (?, ?, 'primary', 1, ?)",
            (delta.story_id, entity_id, upsert.confidence),
        )
        # Entity promotion: corroborated across >=2 distinct stories.
        n_stories = conn.execute(
            "SELECT COUNT(DISTINCT story_id) FROM story_entities WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()[0]
        status = conn.execute(
            "SELECT status FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()["status"]
        if n_stories >= 2 and status == "provisional":
            conn.execute(
                "UPDATE entities SET status='canonical', updated_at=? WHERE entity_id=?",
                (now, entity_id),
            )
            report.entities_promoted.append(entity_id)

    # --- claims -> facts ---
    for idx, claim in enumerate(delta.claims):
        _apply_claim(conn, delta.story_id, idx, claim, report, now)

    # --- chunks ---
    for idx, chunk in enumerate(delta.chunks):
        entity_id = resolve_entity(conn, chunk.entity_slug) if chunk.entity_slug else None
        scope = "entity" if entity_id else "story"
        chunk_id = f"chk_{_short_hash(delta.story_id, str(idx), chunk.title)}"
        conn.execute(
            "INSERT INTO lore_chunks (chunk_id, scope, entity_id, story_id, title, body,"
            " activation_keys_json, retrieval_mode, insertion_lane, group_key, priority,"
            " token_estimate, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'provisional', ?, ?)",
            (chunk_id, scope, entity_id, delta.story_id, chunk.title, chunk.body,
             json.dumps(chunk.activation_keys), chunk.retrieval_mode, chunk.insertion_lane,
             chunk.priority, token_estimate(chunk.body), now, now),
        )
        report.chunks_created.append(chunk_id)
        if embedder is not None:
            vector = embedder.embed([f"{chunk.title}\n{chunk.body}"])[0]
            conn.execute(
                "INSERT INTO chunk_embeddings (chunk_id, model, dimensions, vector_blob,"
                " norm, created_at) VALUES (?, ?, ?, ?, 1.0, ?)",
                (chunk_id, getattr(embedder, "model_name", "unknown"), len(vector),
                 pack_vector(vector), now),
            )


def _apply_claim(conn, story_id: str, idx: int, claim: ClaimInput,
                 report: WritebackReport, now: str) -> None:
    claim_id = f"clm_{_short_hash(story_id, str(idx))}"
    subject_id = resolve_entity(conn, claim.subject_slug)
    object_entity_id = None
    unresolved_object = False
    if claim.object_slug is not None:
        object_entity_id = resolve_entity(conn, claim.object_slug)
        unresolved_object = object_entity_id is None

    def write_claim(canon_state: str, subject: Optional[str]) -> None:
        conn.execute(
            "INSERT INTO claims (claim_id, story_id, subject_entity_id, predicate,"
            " object_entity_id, object_literal, confidence, canon_state, evidence_excerpt,"
            " extractor_payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (claim_id, story_id, subject, claim.predicate, object_entity_id,
             claim.object_literal, claim.confidence, canon_state, claim.evidence_excerpt,
             claim.model_dump_json(), now),
        )
        report.claims_written += 1

    # Alias-only / unresolved references: store the claim, touch no facts.
    if subject_id is None or unresolved_object:
        write_claim("needs_review", subject_id)
        return
    if claim.canonicality_hint == "uncertain":
        write_claim("candidate", subject_id)
        return

    obj_norm = _object_norm(object_entity_id, claim.object_literal)

    if claim.canonicality_hint == "motif":
        write_claim("accepted", subject_id)
        existing = _find_fact(conn, subject_id, claim.predicate, obj_norm, ("motif",))
        if existing:
            conn.execute(
                "UPDATE facts SET last_supported_story_id=?, confidence=MAX(confidence, ?),"
                " updated_at=? WHERE fact_id=?",
                (story_id, claim.confidence, now, existing["fact_id"]),
            )
        else:
            _insert_fact(conn, subject_id, claim, object_entity_id, "motif",
                         story_id, claim_id, now, report)
        return

    active = conn.execute(
        "SELECT * FROM facts WHERE subject_entity_id=? AND predicate=?"
        " AND status IN ('canonical','soft') ORDER BY fact_id",
        (subject_id, claim.predicate),
    ).fetchall()
    match = next(
        (f for f in active if _object_norm(f["object_entity_id"], f["object_literal"]) == obj_norm),
        None,
    )
    others = [f for f in active if f is not match]

    if match is not None:
        write_claim("accepted", subject_id)
        new_conf = max(match["confidence"], claim.confidence)
        conn.execute(
            "UPDATE facts SET last_supported_story_id=?, confidence=?, updated_at=?"
            " WHERE fact_id=?",
            (story_id, new_conf, now, match["fact_id"]),
        )
        corroborated = (
            match["first_supported_story_id"] is not None
            and match["first_supported_story_id"] != story_id
        )
        has_active_sibling = any(f["status"] in ("canonical", "soft") for f in others)
        if (
            match["status"] == "soft"
            and claim.canonicality_hint == "candidate"
            and corroborated
            and new_conf >= PROMOTION_CONFIDENCE
            and not has_active_sibling
        ):
            conn.execute(
                "UPDATE facts SET status='canonical', updated_at=? WHERE fact_id=?",
                (now, match["fact_id"]),
            )
            report.facts_promoted.append(match["fact_id"])
        return

    canonical_sibling = next((f for f in others if f["status"] == "canonical"), None)
    if canonical_sibling is not None:
        # Contradiction of canon: open adjudication, never overwrite.
        write_claim("needs_review", subject_id)
        item_id = f"adj_{_short_hash(claim_id)}"
        payload = {
            "claim_id": claim_id,
            "subject_entity_id": subject_id,
            "predicate": claim.predicate,
            "proposed_object_entity_id": object_entity_id,
            "proposed_object_literal": claim.object_literal,
            "existing_fact_id": canonical_sibling["fact_id"],
            "existing_object_entity_id": canonical_sibling["object_entity_id"],
            "existing_object_literal": canonical_sibling["object_literal"],
            "story_id": story_id,
        }
        conn.execute(
            "INSERT INTO adjudication_queue (item_id, item_kind, reason, payload_json,"
            " status, created_at) VALUES (?, 'claim', ?, ?, 'open', ?)",
            (item_id,
             f"claim contradicts canonical fact {canonical_sibling['fact_id']}"
             f" on predicate {claim.predicate!r}",
             json.dumps(payload), now),
        )
        report.adjudications_opened.append(item_id)
        return

    # No exact match, no canonical sibling: soft facts may coexist.
    write_claim("candidate", subject_id)
    if claim.confidence >= SOFT_FACT_CONFIDENCE:
        _insert_fact(conn, subject_id, claim, object_entity_id, "soft",
                     story_id, claim_id, now, report)


def _find_fact(conn, subject_id, predicate, obj_norm, statuses):
    rows = conn.execute(
        f"SELECT * FROM facts WHERE subject_entity_id=? AND predicate=?"
        f" AND status IN ({','.join('?' * len(statuses))}) ORDER BY fact_id",
        (subject_id, predicate, *statuses),
    ).fetchall()
    for row in rows:
        if _object_norm(row["object_entity_id"], row["object_literal"]) == obj_norm:
            return row
    return None


def _insert_fact(conn, subject_id, claim: ClaimInput, object_entity_id, status,
                 story_id, claim_id, now, report) -> str:
    obj_norm = _object_norm(object_entity_id, claim.object_literal)
    fact_id = f"fct_{_short_hash(subject_id, claim.predicate, obj_norm, claim_id)}"
    conn.execute(
        "INSERT INTO facts (fact_id, subject_entity_id, predicate, object_entity_id,"
        " object_literal, confidence, status, first_supported_story_id,"
        " last_supported_story_id, source_claim_id, manual_source_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
        (fact_id, subject_id, claim.predicate, object_entity_id, claim.object_literal,
         claim.confidence, status, story_id, story_id, claim_id, now, now),
    )
    report.facts_created.append(fact_id)
    return fact_id


# --- human-authoritative paths (the only sanctioned invariant carve-outs) ---

def manual_edit_fact(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    predicate: str,
    object_literal: Optional[str] = None,
    object_entity_id: Optional[str] = None,
    uri: Optional[str] = None,
) -> str:
    """Operator edit: immediately canonical, 'manual' source, bypasses adjudication.

    Prior active facts on the same (entity, predicate) are preserved as deprecated history.
    """
    if (object_literal is None) == (object_entity_id is None):
        raise WritebackError("manual edit must set exactly one of object_literal or object_entity_id")
    if not predicate or not predicate.strip():
        raise WritebackError("manual edit requires a non-empty predicate")
    row = conn.execute("SELECT entity_id FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    if row is None:
        raise WritebackError(f"unknown entity {entity_id!r}")
    if object_entity_id is not None:
        if conn.execute("SELECT 1 FROM entities WHERE entity_id=?", (object_entity_id,)).fetchone() is None:
            raise WritebackError(f"unknown object entity {object_entity_id!r}")
    now = _now()
    with conn:
        n = conn.execute("SELECT COUNT(*) FROM sources WHERE source_kind='manual'").fetchone()[0]
        source_id = f"src_manual_{n + 1:06d}"
        conn.execute(
            "INSERT INTO sources (source_id, source_kind, uri, checksum, created_at)"
            " VALUES (?, 'manual', ?, NULL, ?)",
            (source_id, uri, now),
        )
        conn.execute(
            "UPDATE facts SET status='deprecated', updated_at=? WHERE subject_entity_id=?"
            " AND predicate=? AND status IN ('canonical','soft')",
            (now, entity_id, predicate),
        )
        fact_id = f"fct_{_short_hash('manual', source_id, entity_id, predicate)}"
        conn.execute(
            "INSERT INTO facts (fact_id, subject_entity_id, predicate, object_entity_id,"
            " object_literal, confidence, status, first_supported_story_id,"
            " last_supported_story_id, source_claim_id, manual_source_id, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, 1.0, 'canonical', NULL, NULL, NULL, ?, ?, ?)",
            (fact_id, entity_id, predicate, object_entity_id, object_literal,
             source_id, now, now),
        )
    return fact_id


def deprecate_fact(conn: sqlite3.Connection, fact_id: str) -> None:
    row = conn.execute("SELECT fact_id FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
    if row is None:
        raise WritebackError(f"unknown fact {fact_id!r}")
    now = _now()
    with conn:
        conn.execute(
            "UPDATE facts SET status='deprecated', updated_at=? WHERE fact_id=?", (now, fact_id)
        )


def deprecate_chunk(conn: sqlite3.Connection, chunk_id: str) -> None:
    row = conn.execute("SELECT chunk_id FROM lore_chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
    if row is None:
        raise WritebackError(f"unknown chunk {chunk_id!r}")
    now = _now()
    with conn:
        conn.execute(
            "UPDATE lore_chunks SET status='deprecated', updated_at=? WHERE chunk_id=?",
            (now, chunk_id),
        )


def deprecate_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    """Soft-delete an entity: status flips cascade to its facts and chunks; rows survive.

    Embeddings stay attached to their (now deprecated) chunks; retrieval filters on
    chunk status, so they can never surface.
    """
    row = conn.execute("SELECT entity_id FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    if row is None:
        raise WritebackError(f"unknown entity {entity_id!r}")
    now = _now()
    with conn:
        conn.execute(
            "UPDATE entities SET status='deprecated', updated_at=? WHERE entity_id=?",
            (now, entity_id),
        )
        conn.execute(
            "UPDATE facts SET status='deprecated', updated_at=?"
            " WHERE (subject_entity_id=? OR object_entity_id=?) AND status != 'deprecated'",
            (now, entity_id, entity_id),
        )
        conn.execute(
            "UPDATE lore_chunks SET status='deprecated', updated_at=?"
            " WHERE entity_id=? AND status != 'deprecated'",
            (now, entity_id),
        )
