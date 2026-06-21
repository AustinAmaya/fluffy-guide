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
from lore_stack.snapshots import maybe_snapshot

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


def exclusion_key(text: str) -> str:
    """Normalize a name or slug to an entity-exclusion key. Lowercases, strips an
    'ent-'/'ent_' prefix the extractor sometimes prepends to slugs, and collapses
    whitespace/underscores to hyphens, so 'ent-bear', 'Bear', and 'bear' all map to
    'bear'. Matching on this key makes exclusions robust to slug spelling."""
    s = (text or "").strip().lower()
    for p in ("ent-", "ent_"):
        if s.startswith(p):
            s = s[len(p):]
            break
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^a-z0-9-]+", "", s)
    return s.strip("-")


def load_exclusions(conn: sqlite3.Connection) -> set:
    """The set of normalized exclusion keys configured for this lore (empty if the
    db predates migration 0007)."""
    try:
        return {r[0] for r in conn.execute("SELECT name FROM entity_exclusions")}
    except sqlite3.OperationalError:
        return set()


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
    # Prefixes keep entity references and literals in disjoint namespaces, so a
    # literal that happens to look like an internal id can never corroborate
    # (or contradict) an entity-object fact.
    if object_entity_id is not None:
        return f"ent:{object_entity_id}"
    return f"lit:{normalize(object_literal or '')}"


def apply_delta(
    conn: sqlite3.Connection,
    delta: LoreDelta,
    *,
    story_text: Optional[str] = None,
    source_kind: str = "story",
    source_uri: Optional[str] = None,
    embedder: Optional[Embedder] = None,
    reviewed: bool = False,
    authoritative: bool = False,
) -> WritebackReport:
    """Apply one validated LoreDelta inside a single transaction.

    Any failure rolls back completely: no partial writes.

    reviewed=True is the human-approved path (workstream D): the operator vetted
    every claim, so confidence stops being a gate -- approved claims always form
    soft facts, and promotion to canonical needs only corroboration across >=2
    distinct stories (a count), not a confidence threshold. The default
    (reviewed=False) keeps the legacy 0.7/0.9 thresholds for the direct-ingest
    path used by fixtures and tests.

    authoritative=True is the operator-vouched DIRECT-TO-CANON path (the live
    'ingest-delta --canon' flow): every claim is written as a canonical fact
    immediately, with the same authority as a manual edit -- the named value wins
    (single-valued predicates deprecate competing values), entities upsert as
    canonical, and no soft/corroboration/adjudication/supersession applies. Use it
    only for items the operator explicitly named during extraction.
    """
    checksum = delta_checksum(delta)
    existing = conn.execute(
        "SELECT source_id FROM sources WHERE checksum = ?", (checksum,)
    ).fetchone()
    if existing:
        return WritebackReport(story_id=delta.story_id, noop=True)

    maybe_snapshot(conn, f"ingest {delta.story_id}")
    report = WritebackReport(story_id=delta.story_id)
    now = _now()
    try:
        with conn:
            _apply_inner(conn, delta, checksum, story_text, source_kind, source_uri,
                         embedder, report, now, reviewed, authoritative)
    except sqlite3.IntegrityError as exc:
        raise WritebackError(f"delta violates a database constraint: {exc}") from exc
    return report


def _apply_inner(conn, delta, checksum, story_text, source_kind, source_uri,
                 embedder, report, now, reviewed=False, authoritative=False) -> None:
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

    # --- operator-configured exclusions ---
    # Entities the consumer owns outside the lore (e.g. protagonists authored in a
    # SOUL document) are dropped here -- together with any claim that references them
    # and any chunk bound to them -- before a single row is written. Matching is on
    # the normalized exclusion key, so the extractor's slug spelling doesn't matter.
    excl = load_exclusions(conn)
    if excl:
        def _is_excluded(u) -> bool:
            return any(exclusion_key(c) in excl for c in (u.slug, u.display_name, *u.aliases))

        def _ref_excluded(slug) -> bool:
            return slug is not None and exclusion_key(slug) in excl

        entities = []
        for u in delta.entities:
            if _is_excluded(u):
                report.entities_excluded.append(u.slug)
            else:
                entities.append(u)
        claims = []
        for c in delta.claims:
            if _ref_excluded(c.subject_slug) or _ref_excluded(c.object_slug):
                report.claims_excluded += 1
            else:
                claims.append(c)
        chunks = []
        for ch in delta.chunks:
            if _ref_excluded(ch.entity_slug):
                report.chunks_excluded += 1
            else:
                chunks.append(ch)
    else:
        entities, claims, chunks = delta.entities, delta.claims, delta.chunks

    # --- entities ---
    for upsert in entities:
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
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                (entity_id, upsert.kind, slug, upsert.display_name,
                 "canonical" if authoritative else "provisional", upsert.summary,
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
        # Entity promotion: corroborated across >=2 distinct stories -- or
        # immediately when the operator vouched for the delta (authoritative).
        n_stories = conn.execute(
            "SELECT COUNT(DISTINCT story_id) FROM story_entities WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()[0]
        status = conn.execute(
            "SELECT status FROM entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()["status"]
        if status == "provisional" and (authoritative or n_stories >= 2):
            conn.execute(
                "UPDATE entities SET status='canonical', updated_at=? WHERE entity_id=?",
                (now, entity_id),
            )
            report.entities_promoted.append(entity_id)

    # --- claims -> facts ---
    for idx, claim in enumerate(claims):
        _apply_claim(conn, delta.story_id, idx, claim, report, now, reviewed, embedder,
                     authoritative)

    # --- chunks ---
    for idx, chunk in enumerate(chunks):
        entity_id = resolve_entity(conn, chunk.entity_slug) if chunk.entity_slug else None
        scope = "entity" if entity_id else "story"
        chunk_id = f"chk_{_short_hash(delta.story_id, str(idx), chunk.title)}"
        derived = _resolve_derived_facts(conn, chunk.derived_from)
        conn.execute(
            "INSERT INTO lore_chunks (chunk_id, scope, entity_id, story_id, title, body,"
            " activation_keys_json, retrieval_mode, insertion_lane, group_key, priority,"
            " token_estimate, status, derived_from_fact_ids, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'provisional', ?, ?, ?)",
            (chunk_id, scope, entity_id, delta.story_id, chunk.title, chunk.body,
             json.dumps(chunk.activation_keys), chunk.retrieval_mode, chunk.insertion_lane,
             chunk.priority, token_estimate(chunk.body),
             json.dumps(derived) if derived else None, now, now),
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
                 report: WritebackReport, now: str, reviewed: bool = False,
                 embedder: Optional[Embedder] = None, authoritative: bool = False) -> None:
    from lore_stack import registry  # lazy import: breaks the engine<->registry cycle

    claim_id = f"clm_{_short_hash(story_id, str(idx))}"
    subject_id = resolve_entity(conn, claim.subject_slug)
    object_entity_id = None
    unresolved_object = False
    if claim.object_slug is not None:
        object_entity_id = resolve_entity(conn, claim.object_slug)
        unresolved_object = object_entity_id is None

    # Registry normalization: map the extractor's spelling onto the controlled
    # vocabulary so synonyms corroborate instead of fragmenting. Unregistered
    # predicates are stored but can never auto-canonize (registered gate below).
    pred_info = registry.lookup(conn, claim.predicate)
    predicate = pred_info.predicate_id if pred_info else normalize(claim.predicate)
    single_valued = pred_info is None or pred_info.cardinality == "single"

    def write_claim(canon_state: str, subject: Optional[str]) -> None:
        conn.execute(
            "INSERT INTO claims (claim_id, story_id, subject_entity_id, predicate,"
            " object_entity_id, object_literal, confidence, canon_state, evidence_excerpt,"
            " extractor_payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (claim_id, story_id, subject, predicate, object_entity_id,
             claim.object_literal, claim.confidence, canon_state, claim.evidence_excerpt,
             claim.model_dump_json(), now),
        )
        report.claims_written += 1

    # Closed relationship set: a relationship (entity-object) claim may only use a
    # registered relationship predicate (range='entity'). An off-vocabulary
    # predicate -- or a text-attribute predicate misused with an entity object --
    # is rejected outright: the claim is stored 'rejected', no fact forms, and the
    # rest of the delta still applies. Attributes (object_literal) stay an open
    # vocabulary and are never rejected here.
    if claim.object_slug is not None and (pred_info is None or pred_info.range != "entity"):
        write_claim("rejected", subject_id)
        report.claims_rejected.append(claim_id)
        return

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
        existing = _find_fact(conn, subject_id, predicate, obj_norm, ("motif",))
        if existing:
            conn.execute(
                "UPDATE facts SET last_supported_story_id=?, confidence=MAX(confidence, ?),"
                " updated_at=? WHERE fact_id=?",
                (story_id, claim.confidence, now, existing["fact_id"]),
            )
        else:
            _insert_fact(conn, subject_id, claim, object_entity_id, "motif",
                         story_id, claim_id, now, report, predicate)
        return

    active = conn.execute(
        "SELECT * FROM facts WHERE subject_entity_id=? AND predicate=?"
        " AND status IN ('canonical','soft') ORDER BY fact_id",
        (subject_id, predicate),
    ).fetchall()
    match = next(
        (f for f in active if _object_norm(f["object_entity_id"], f["object_literal"]) == obj_norm),
        None,
    )
    others = [f for f in active if f is not match]

    if authoritative:
        # Operator-vouched, direct-to-canon: write this value as canonical now, with
        # the same authority as a manual edit. The named value wins -- a single-valued
        # predicate deprecates competing active values; multi-valued values coexist as
        # canonical. No soft/corroboration/adjudication/supersession. ensure_registered
        # keeps the A6 invariant (canonical facts have a registered predicate) for
        # attributes; relationships are already registered (rejected above otherwise).
        registry.ensure_registered(
            conn, predicate, registered_by="operator",
            range_="entity" if object_entity_id is not None else "text",
        )
        write_claim("accepted", subject_id)
        if single_valued:
            for f in others:
                conn.execute(
                    "UPDATE facts SET status='deprecated', updated_at=? WHERE fact_id=?",
                    (now, f["fact_id"]),
                )
                _stale_chunks_for_facts(conn, [f["fact_id"]], now)
        if match is not None:
            if match["status"] != "canonical":
                report.facts_promoted.append(match["fact_id"])
            conn.execute(
                "UPDATE facts SET status='canonical', last_supported_story_id=?,"
                " confidence=MAX(confidence, ?), updated_at=? WHERE fact_id=?",
                (story_id, claim.confidence, now, match["fact_id"]),
            )
        else:
            _insert_fact(conn, subject_id, claim, object_entity_id, "canonical",
                         story_id, claim_id, now, report, predicate)
        return

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
        # A competing active value blocks promotion only for single-valued
        # predicates; a multi-valued attribute (carries) promotes each value on
        # its own corroboration.
        has_active_sibling = any(f["status"] in ("canonical", "soft") for f in others)
        blocks_promotion = single_valued and has_active_sibling
        # Episodic facts (visits) are story-anchored: they feed continuity/hooks and
        # never harden into permanent canon, even when corroborated. (Manual edits
        # stay authoritative -- this gate is only on the corroboration path.)
        episodic = pred_info is not None and pred_info.persistence == "episodic"
        if (
            match["status"] == "soft"
            and claim.canonicality_hint == "candidate"
            and corroborated
            and (reviewed or new_conf >= PROMOTION_CONFIDENCE)  # reviewed: count-only
            and pred_info is not None  # only registered predicates auto-canonize
            and not blocks_promotion
            and not episodic
        ):
            conn.execute(
                "UPDATE facts SET status='canonical', updated_at=? WHERE fact_id=?",
                (now, match["fact_id"]),
            )
            report.facts_promoted.append(match["fact_id"])
        return

    canonical_sibling = next((f for f in others if f["status"] == "canonical"), None)
    if canonical_sibling is not None and single_valued:
        # A new value against a canonical single-valued fact. Fork on persistence:
        # a `state` predicate (lives_in -- you can move) opens a SUPERSESSION
        # proposal (accepting canonizes the new value and deprecates the old); a
        # `permanent` predicate (profession, species) opens a CONTRADICTION (canon
        # never moves on its own). Either way canon is never overwritten here.
        # (Multi-valued predicates fall through and coexist.)
        write_claim("needs_review", subject_id)
        item_id = f"adj_{_short_hash(claim_id)}"
        is_supersession = pred_info is not None and pred_info.persistence == "state"
        payload = {
            "claim_id": claim_id,
            "subject_entity_id": subject_id,
            "predicate": predicate,
            "proposed_object_entity_id": object_entity_id,
            "proposed_object_literal": claim.object_literal,
            "existing_fact_id": canonical_sibling["fact_id"],
            "existing_object_entity_id": canonical_sibling["object_entity_id"],
            "existing_object_literal": canonical_sibling["object_literal"],
            "story_id": story_id,
        }
        reason = (
            f"story proposes a new value for {predicate!r}, superseding canonical"
            f" fact {canonical_sibling['fact_id']}"
            if is_supersession
            else f"claim contradicts canonical fact {canonical_sibling['fact_id']}"
            f" on predicate {predicate!r}"
        )
        conn.execute(
            "INSERT INTO adjudication_queue (item_id, item_kind, reason, payload_json,"
            " status, created_at) VALUES (?, ?, ?, ?, 'open', ?)",
            (item_id, "supersession" if is_supersession else "claim", reason,
             json.dumps(payload), now),
        )
        report.adjudications_opened.append(item_id)
        return

    # No exact match (and for multi-valued predicates, no blocking conflict):
    # soft facts coexist. A reviewed (human-approved) claim always forms a soft
    # fact; the legacy path requires the confidence floor.
    write_claim("candidate", subject_id)
    if reviewed or claim.confidence >= SOFT_FACT_CONFIDENCE:
        new_fact_id = _insert_fact(conn, subject_id, claim, object_entity_id, "soft",
                                   story_id, claim_id, now, report, predicate)
        _suggest_merges(conn, subject_id, predicate, new_fact_id, object_entity_id,
                        claim.object_literal, embedder, now, report)


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
                 story_id, claim_id, now, report, predicate: str) -> str:
    obj_norm = _object_norm(object_entity_id, claim.object_literal)
    fact_id = f"fct_{_short_hash(subject_id, predicate, obj_norm, claim_id)}"
    conn.execute(
        "INSERT INTO facts (fact_id, subject_entity_id, predicate, object_entity_id,"
        " object_literal, confidence, status, first_supported_story_id,"
        " last_supported_story_id, source_claim_id, manual_source_id, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
        (fact_id, subject_id, predicate, object_entity_id, claim.object_literal,
         claim.confidence, status, story_id, story_id, claim_id, now, now),
    )
    report.facts_created.append(fact_id)
    return fact_id


MERGE_THRESHOLD = 0.5  # aggressive, per the bedtime-lore "keep it small" mandate


def _fact_object_text(conn, object_entity_id: Optional[str], object_literal: Optional[str]) -> str:
    if object_entity_id is not None:
        row = conn.execute(
            "SELECT display_name FROM entities WHERE entity_id=?", (object_entity_id,)
        ).fetchone()
        return row["display_name"] if row else object_entity_id
    return object_literal or ""


def _suggest_merges(conn, subject_id, predicate, new_fact_id, object_entity_id,
                    object_literal, embedder, now, report) -> None:
    """Open a merge_suggestion when the just-created soft fact's object is
    embedding-similar to an existing active value on the same (subject, predicate).
    Deterministic (cosine over content-derived vectors); never auto-merges."""
    if embedder is None:
        return
    siblings = conn.execute(
        "SELECT * FROM facts WHERE subject_entity_id=? AND predicate=?"
        " AND status IN ('canonical','soft') AND fact_id != ? ORDER BY fact_id",
        (subject_id, predicate, new_fact_id),
    ).fetchall()
    if not siblings:
        return
    new_text = _fact_object_text(conn, object_entity_id, object_literal)
    new_vec = embedder.embed([new_text])[0]
    best = None
    for sib in siblings:
        # An existing suggestion for this pair? Don't duplicate it.
        sib_text = _fact_object_text(conn, sib["object_entity_id"], sib["object_literal"])
        sib_vec = embedder.embed([sib_text])[0]
        cosine = sum(a * b for a, b in zip(new_vec, sib_vec))
        if cosine >= MERGE_THRESHOLD and (best is None or cosine > best[0]):
            best = (cosine, sib, sib_text)
    if best is None:
        return
    cosine, sib, sib_text = best
    item_id = f"mrg_{_short_hash(new_fact_id, sib['fact_id'])}"
    if conn.execute(
        "SELECT 1 FROM adjudication_queue WHERE item_id=?", (item_id,)
    ).fetchone():
        return
    payload = {
        "kind": "merge_suggestion",
        "fact_a": new_fact_id,
        "fact_a_text": new_text,
        "fact_b": sib["fact_id"],
        "fact_b_text": sib_text,
        "subject_entity_id": subject_id,
        "predicate": predicate,
        "cosine": round(cosine, 6),
    }
    conn.execute(
        "INSERT INTO adjudication_queue (item_id, item_kind, reason, payload_json,"
        " status, created_at) VALUES (?, 'merge_suggestion', ?, ?, 'open', ?)",
        (item_id,
         f"possible duplicate values of {predicate!r}: {new_text!r} ~ {sib_text!r}"
         f" (cosine {cosine:.2f})",
         json.dumps(payload), now),
    )
    report.merge_suggestions_opened.append(item_id)


def _resolve_derived_facts(conn, refs) -> list[str]:
    """Resolve each (subject_slug, predicate) chunk ref to the current active fact
    ids on that pair, so the chunk can be flagged stale if any is later deprecated.
    Chunks are applied after claims, so facts from the same delta already exist."""
    from lore_stack import registry  # lazy import: breaks the engine<->registry cycle

    fact_ids: list[str] = []
    for ref in refs:
        subject_id = resolve_entity(conn, ref.subject_slug)
        if subject_id is None:
            continue
        pred_info = registry.lookup(conn, ref.predicate)
        predicate = pred_info.predicate_id if pred_info else normalize(ref.predicate)
        for row in conn.execute(
            "SELECT fact_id FROM facts WHERE subject_entity_id=? AND predicate=?"
            " AND status IN ('canonical','soft') ORDER BY fact_id",
            (subject_id, predicate),
        ):
            fact_ids.append(row["fact_id"])
    return fact_ids


def _stale_chunks_for_facts(conn, fact_ids, now) -> None:
    """Flag active chunks stale when any fact they derive from is deprecated or
    superseded. Stale chunks drop out of compilation (retrieval excludes them) but
    survive for the operator to rewrite-or-confirm."""
    fact_set = {fid for fid in fact_ids if fid}
    if not fact_set:
        return
    for row in conn.execute(
        "SELECT chunk_id, derived_from_fact_ids FROM lore_chunks"
        " WHERE derived_from_fact_ids IS NOT NULL AND stale = 0"
    ).fetchall():
        if set(json.loads(row["derived_from_fact_ids"])) & fact_set:
            conn.execute(
                "UPDATE lore_chunks SET stale=1, updated_at=? WHERE chunk_id=?",
                (now, row["chunk_id"]),
            )


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
    from lore_stack import registry  # lazy import: breaks the engine<->registry cycle

    if (object_literal is None) == (object_entity_id is None):
        raise WritebackError("manual edit must set exactly one of object_literal or object_entity_id")
    if not predicate or not predicate.strip():
        raise WritebackError("manual edit requires a non-empty predicate")
    row = conn.execute("SELECT status FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    if row is None:
        raise WritebackError(f"unknown entity {entity_id!r}")
    if row["status"] == "deprecated":
        raise WritebackError(f"entity {entity_id!r} is deprecated; restore it before editing")
    if object_entity_id is not None:
        obj = conn.execute(
            "SELECT status FROM entities WHERE entity_id=?", (object_entity_id,)
        ).fetchone()
        if obj is None or obj["status"] == "deprecated":
            raise WritebackError(f"unknown or deprecated object entity {object_entity_id!r}")
    # The operator is authoritative for ATTRIBUTES: an edit using a new text
    # predicate *defines* it (auto-registered below). RELATIONSHIPS are a closed
    # set, though -- an operator edit may only use a registered relationship
    # predicate (range='entity'); it cannot mint a new edge type, and it cannot
    # attach an entity object to a text predicate. Resolve known spellings to their
    # canonical id for the label and deprecation.
    range_ = "entity" if object_entity_id is not None else "text"
    existing_pred = registry.lookup(conn, predicate)
    if range_ == "entity":
        if existing_pred is None or existing_pred.range != "entity":
            raise WritebackError(
                f"unknown relationship predicate {predicate!r}: relationships are a"
                " closed, fixed set -- add it to db/predicates.json to use it"
            )
        predicate = existing_pred.predicate_id
    else:
        predicate = existing_pred.predicate_id if existing_pred else normalize(predicate)

    maybe_snapshot(conn, f"edit {entity_id}.{predicate}")
    now = _now()
    with conn:
        registry.ensure_registered(
            conn, predicate, registered_by="operator", range_=range_
        )
        n = conn.execute("SELECT COUNT(*) FROM sources WHERE source_kind='manual'").fetchone()[0]
        source_id = f"src_manual_{n + 1:06d}"
        conn.execute(
            "INSERT INTO sources (source_id, source_kind, uri, checksum, created_at)"
            " VALUES (?, 'manual', ?, NULL, ?)",
            (source_id, uri, now),
        )
        deprecated = [r["fact_id"] for r in conn.execute(
            "SELECT fact_id FROM facts WHERE subject_entity_id=? AND predicate=?"
            " AND status IN ('canonical','soft')", (entity_id, predicate))]
        conn.execute(
            "UPDATE facts SET status='deprecated', updated_at=? WHERE subject_entity_id=?"
            " AND predicate=? AND status IN ('canonical','soft')",
            (now, entity_id, predicate),
        )
        _stale_chunks_for_facts(conn, deprecated, now)
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


def resolve_merge_suggestion(conn: sqlite3.Connection, item_id: str, keep_fact_id: str) -> None:
    """Operator resolution of a merge_suggestion: keep one value, fold the other
    into deprecated history. Records which fact survived on the (now resolved)
    adjudication item for lineage. Dismissing instead is a plain status flip."""
    item = conn.execute(
        "SELECT payload_json, status, item_kind FROM adjudication_queue WHERE item_id=?",
        (item_id,),
    ).fetchone()
    if item is None or item["item_kind"] != "merge_suggestion":
        raise WritebackError(f"no merge suggestion {item_id!r}")
    if item["status"] != "open":
        raise WritebackError(f"merge suggestion {item_id!r} is already {item['status']}")
    payload = json.loads(item["payload_json"])
    pair = {payload["fact_a"], payload["fact_b"]}
    if keep_fact_id not in pair:
        raise WritebackError(f"{keep_fact_id!r} is not one of this suggestion's facts")
    drop_fact_id = (pair - {keep_fact_id}).pop()
    maybe_snapshot(conn, f"merge {drop_fact_id}->{keep_fact_id}")
    now = _now()
    with conn:
        conn.execute(
            "UPDATE facts SET status='deprecated', updated_at=? WHERE fact_id=? AND status != 'deprecated'",
            (now, drop_fact_id),
        )
        _stale_chunks_for_facts(conn, [drop_fact_id], now)
        payload["resolution"] = {"kept": keep_fact_id, "merged": drop_fact_id}
        conn.execute(
            "UPDATE adjudication_queue SET status='resolved', payload_json=? WHERE item_id=?",
            (json.dumps(payload), item_id),
        )


def resolve_contradiction(conn: sqlite3.Connection, item_id: str, decision: str) -> None:
    """Operator resolution of a contradiction (item_kind='claim').

    'keep_existing' dismisses the item, canon unchanged. 'accept_proposed' makes
    the proposed value canonical via the authoritative manual-edit path (deprecate
    the existing canonical fact, write the proposed value as canonical with a
    'manual' source) and marks the item resolved.
    """
    if decision not in ("keep_existing", "accept_proposed"):
        raise WritebackError("decision must be 'keep_existing' or 'accept_proposed'")
    item = conn.execute(
        "SELECT payload_json, status, item_kind FROM adjudication_queue WHERE item_id=?",
        (item_id,),
    ).fetchone()
    if item is None or item["item_kind"] != "claim":
        raise WritebackError(f"no contradiction {item_id!r}")
    if item["status"] != "open":
        raise WritebackError(f"contradiction {item_id!r} is already {item['status']}")
    payload = json.loads(item["payload_json"])
    now = _now()

    if decision == "keep_existing":
        maybe_snapshot(conn, f"keep existing ({item_id})")
        payload["resolution"] = {"decision": "keep_existing"}
        with conn:
            conn.execute(
                "UPDATE adjudication_queue SET status='dismissed', payload_json=?"
                " WHERE item_id=?",
                (json.dumps(payload), item_id),
            )
        return

    # accept_proposed: the proposed value wins, by operator authority.
    fact_id = manual_edit_fact(
        conn,
        entity_id=payload["subject_entity_id"],
        predicate=payload["predicate"],
        object_literal=payload.get("proposed_object_literal"),
        object_entity_id=payload.get("proposed_object_entity_id"),
    )
    payload["resolution"] = {"decision": "accept_proposed", "new_fact_id": fact_id}
    with conn:
        conn.execute(
            "UPDATE adjudication_queue SET status='resolved', payload_json=?"
            " WHERE item_id=?",
            (json.dumps(payload), item_id),
        )


def resolve_supersession(conn: sqlite3.Connection, item_id: str, decision: str) -> None:
    """Operator resolution of a supersession proposal (item_kind='supersession').

    'accept_proposed' makes the new value canonical via the authoritative
    manual-edit path (which deprecates the prior canonical fact) and records
    `superseded_by` lineage on the item. 'keep_existing' dismisses it, canon
    unchanged. Same decision vocabulary as resolve_contradiction, so the
    visualizer's one resolve action drives both.
    """
    if decision not in ("keep_existing", "accept_proposed"):
        raise WritebackError("decision must be 'keep_existing' or 'accept_proposed'")
    item = conn.execute(
        "SELECT payload_json, status, item_kind FROM adjudication_queue WHERE item_id=?",
        (item_id,),
    ).fetchone()
    if item is None or item["item_kind"] != "supersession":
        raise WritebackError(f"no supersession {item_id!r}")
    if item["status"] != "open":
        raise WritebackError(f"supersession {item_id!r} is already {item['status']}")
    payload = json.loads(item["payload_json"])
    now = _now()

    if decision == "keep_existing":
        maybe_snapshot(conn, f"keep existing ({item_id})")
        payload["resolution"] = {"decision": "keep_existing"}
        with conn:
            conn.execute(
                "UPDATE adjudication_queue SET status='dismissed', payload_json=?"
                " WHERE item_id=?",
                (json.dumps(payload), item_id),
            )
        return

    # accept_proposed: the new value supersedes the old (deprecated by the edit).
    new_fact_id = manual_edit_fact(
        conn,
        entity_id=payload["subject_entity_id"],
        predicate=payload["predicate"],
        object_literal=payload.get("proposed_object_literal"),
        object_entity_id=payload.get("proposed_object_entity_id"),
    )
    payload["resolution"] = {
        "decision": "accept_proposed",
        "new_fact_id": new_fact_id,
        "superseded_fact_id": payload.get("existing_fact_id"),
        "superseded_by": new_fact_id,
    }
    with conn:
        conn.execute(
            "UPDATE adjudication_queue SET status='resolved', payload_json=?"
            " WHERE item_id=?",
            (json.dumps(payload), item_id),
        )


def _merge_entity_into(conn, keep_id: str, drop_id: str, now: str) -> None:
    """Fold drop_id into keep_id: re-point its facts, chunks, story-appearances, and
    aliases onto keep, drop any relationship that becomes a self-loop, then
    soft-deprecate the now-empty duplicate (recoverable, history preserved)."""
    conn.execute("UPDATE facts SET subject_entity_id=?, updated_at=? WHERE subject_entity_id=?",
                 (keep_id, now, drop_id))
    conn.execute("UPDATE facts SET object_entity_id=?, updated_at=? WHERE object_entity_id=?",
                 (keep_id, now, drop_id))
    # a relationship between the two merged entities is now keep->keep: drop it.
    conn.execute(
        "UPDATE facts SET status='deprecated', updated_at=? WHERE subject_entity_id=?"
        " AND object_entity_id=? AND status != 'deprecated'", (now, keep_id, keep_id))
    conn.execute("UPDATE lore_chunks SET entity_id=?, updated_at=? WHERE entity_id=?",
                 (keep_id, now, drop_id))
    # story appearances: re-point, dedup on the (story_id, entity_id) primary key.
    conn.execute(
        "INSERT OR IGNORE INTO story_entities (story_id, entity_id, role, mention_count, salience)"
        " SELECT story_id, ?, role, mention_count, salience FROM story_entities WHERE entity_id=?",
        (keep_id, drop_id))
    conn.execute("DELETE FROM story_entities WHERE entity_id=?", (drop_id,))
    # aliases are globally unique-by-normalized, so they re-point without collision;
    # the duplicate's names become surface aliases of the survivor.
    conn.execute("UPDATE entity_aliases SET entity_id=?, alias_type='surface' WHERE entity_id=?",
                 (keep_id, drop_id))
    conn.execute("UPDATE entities SET status='deprecated', updated_at=? WHERE entity_id=?",
                 (now, drop_id))
    # the survivor may now be corroborated across >=2 stories -> canonical.
    n = conn.execute(
        "SELECT COUNT(DISTINCT story_id) FROM story_entities WHERE entity_id=?", (keep_id,)
    ).fetchone()[0]
    if n >= 2:
        conn.execute("UPDATE entities SET status='canonical', updated_at=? WHERE entity_id=?"
                     " AND status='provisional'", (now, keep_id))


def propose_entity_merge(conn: sqlite3.Connection, entity_ids) -> str:
    """Operator-initiated: queue a suggestion to merge >=2 entities into one. Writes
    nothing to the lore until resolved; idempotent for the same set."""
    ids = [e for e in dict.fromkeys(entity_ids) if e]
    if len(ids) < 2:
        raise WritebackError("an entity merge needs at least two distinct entities")
    rows = conn.execute(
        f"SELECT entity_id, display_name FROM entities WHERE entity_id IN"
        f" ({','.join('?' * len(ids))}) AND status != 'deprecated'", ids).fetchall()
    found = {r["entity_id"]: r["display_name"] for r in rows}
    missing = [e for e in ids if e not in found]
    if missing:
        raise WritebackError(f"unknown or deprecated entities: {missing}")
    item_id = f"emrg_{_short_hash(*sorted(ids))}"
    existing = conn.execute(
        "SELECT status FROM adjudication_queue WHERE item_id=?", (item_id,)).fetchone()
    if existing and existing["status"] == "open":
        return item_id
    maybe_snapshot(conn, f"propose merge {','.join(ids)}")
    now = _now()
    with conn:
        payload = {"kind": "entity_merge", "entity_ids": ids,
                   "entity_names": [found[e] for e in ids]}
        conn.execute(
            "INSERT OR REPLACE INTO adjudication_queue (item_id, item_kind, reason,"
            " payload_json, status, created_at) VALUES (?, 'entity_merge', ?, ?, 'open', ?)",
            (item_id, f"operator-proposed merge of {', '.join(found[e] for e in ids)}",
             json.dumps(payload), now))
    return item_id


def resolve_entity_merge(conn: sqlite3.Connection, item_id: str, keep_entity_id: str) -> None:
    """Resolve an entity-merge item: fold the others into `keep_entity_id`. A
    `keep_entity_id` of 'dismiss'/'keep_existing' (or not one of the candidates)
    dismisses it — the entities were distinct after all."""
    item = conn.execute(
        "SELECT payload_json, status, item_kind FROM adjudication_queue WHERE item_id=?",
        (item_id,)).fetchone()
    if item is None or item["item_kind"] != "entity_merge":
        raise WritebackError(f"no entity merge {item_id!r}")
    if item["status"] != "open":
        raise WritebackError(f"entity merge {item_id!r} is already {item['status']}")
    payload = json.loads(item["payload_json"])
    ids = payload["entity_ids"]
    now = _now()
    if keep_entity_id in ("dismiss", "keep_existing") or keep_entity_id not in ids:
        if keep_entity_id not in ("dismiss", "keep_existing"):
            raise WritebackError(f"{keep_entity_id!r} is not one of this merge's entities")
        maybe_snapshot(conn, f"dismiss merge {item_id}")
        with conn:
            payload["resolution"] = {"decision": "dismissed"}
            conn.execute("UPDATE adjudication_queue SET status='dismissed', payload_json=?"
                         " WHERE item_id=?", (json.dumps(payload), item_id))
        return
    keep_row = conn.execute(
        "SELECT status FROM entities WHERE entity_id=?", (keep_entity_id,)).fetchone()
    if keep_row is None or keep_row["status"] == "deprecated":
        raise WritebackError(f"survivor {keep_entity_id!r} is unknown or deprecated")
    maybe_snapshot(conn, f"merge into {keep_entity_id}")
    with conn:
        for drop_id in [e for e in ids if e != keep_entity_id]:
            if conn.execute("SELECT status FROM entities WHERE entity_id=? AND status!='deprecated'",
                            (drop_id,)).fetchone():
                _merge_entity_into(conn, keep_entity_id, drop_id, now)
        payload["resolution"] = {"kept": keep_entity_id,
                                 "merged": [e for e in ids if e != keep_entity_id]}
        conn.execute("UPDATE adjudication_queue SET status='resolved', payload_json=?"
                     " WHERE item_id=?", (json.dumps(payload), item_id))


def deprecate_fact(conn: sqlite3.Connection, fact_id: str) -> None:
    row = conn.execute("SELECT fact_id FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
    if row is None:
        raise WritebackError(f"unknown fact {fact_id!r}")
    maybe_snapshot(conn, f"deprecate fact {fact_id}")
    now = _now()
    with conn:
        conn.execute(
            "UPDATE facts SET status='deprecated', updated_at=? WHERE fact_id=?", (now, fact_id)
        )
        _stale_chunks_for_facts(conn, [fact_id], now)


def deprecate_chunk(conn: sqlite3.Connection, chunk_id: str) -> None:
    row = conn.execute("SELECT chunk_id FROM lore_chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
    if row is None:
        raise WritebackError(f"unknown chunk {chunk_id!r}")
    maybe_snapshot(conn, f"deprecate chunk {chunk_id}")
    now = _now()
    with conn:
        conn.execute(
            "UPDATE lore_chunks SET status='deprecated', updated_at=? WHERE chunk_id=?",
            (now, chunk_id),
        )


def confirm_chunk_fresh(conn: sqlite3.Connection, chunk_id: str) -> None:
    """Operator confirms a stale chunk's prose still reads true despite the fact
    change: clear the stale flag so it returns to compilation. (The alternative is
    to deprecate it and author a replacement.)"""
    row = conn.execute("SELECT chunk_id FROM lore_chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
    if row is None:
        raise WritebackError(f"unknown chunk {chunk_id!r}")
    maybe_snapshot(conn, f"confirm fresh {chunk_id}")
    now = _now()
    with conn:
        conn.execute(
            "UPDATE lore_chunks SET stale=0, updated_at=? WHERE chunk_id=?", (now, chunk_id)
        )


def restore_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    """Reverse a soft delete conservatively: the entity returns as provisional
    (corroboration can re-promote it) and its owned chunks as provisional. Facts
    stay deprecated history — reviving them wholesale could resurrect values that
    were individually superseded (e.g. by a manual edit); the operator re-asserts
    specific facts via manual_edit_fact, which is authoritative."""
    row = conn.execute(
        "SELECT status FROM entities WHERE entity_id=?", (entity_id,)
    ).fetchone()
    if row is None:
        raise WritebackError(f"unknown entity {entity_id!r}")
    if row["status"] != "deprecated":
        raise WritebackError(f"entity {entity_id!r} is not deprecated")
    maybe_snapshot(conn, f"restore {entity_id}")
    now = _now()
    with conn:
        conn.execute(
            "UPDATE entities SET status='provisional', updated_at=? WHERE entity_id=?",
            (now, entity_id),
        )
        conn.execute(
            "UPDATE lore_chunks SET status='provisional', updated_at=?"
            " WHERE entity_id=? AND status='deprecated'",
            (now, entity_id),
        )


def deprecate_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    """Soft-delete an entity: status flips cascade to its facts and chunks; rows survive.

    Embeddings stay attached to their (now deprecated) chunks; retrieval filters on
    chunk status, so they can never surface.
    """
    row = conn.execute("SELECT entity_id FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    if row is None:
        raise WritebackError(f"unknown entity {entity_id!r}")
    maybe_snapshot(conn, f"deprecate {entity_id}")
    now = _now()
    with conn:
        conn.execute(
            "UPDATE entities SET status='deprecated', updated_at=? WHERE entity_id=?",
            (now, entity_id),
        )
        deprecated = [r["fact_id"] for r in conn.execute(
            "SELECT fact_id FROM facts WHERE (subject_entity_id=? OR object_entity_id=?)"
            " AND status != 'deprecated'", (entity_id, entity_id))]
        conn.execute(
            "UPDATE facts SET status='deprecated', updated_at=?"
            " WHERE (subject_entity_id=? OR object_entity_id=?) AND status != 'deprecated'",
            (now, entity_id, entity_id),
        )
        _stale_chunks_for_facts(conn, deprecated, now)
        conn.execute(
            "UPDATE lore_chunks SET status='deprecated', updated_at=?"
            " WHERE entity_id=? AND status != 'deprecated'",
            (now, entity_id),
        )
