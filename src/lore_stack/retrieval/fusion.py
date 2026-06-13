"""Deterministic fusion of keyed, semantic, graph, and recency retrieval passes.

Additive scorer (adapted from the spec's recommendation; recency uses story
insertion order, never a clock, so identical DBs always score identically):

  score = 4.0*exact_name_hit + 2.0*alias_or_key_hit + 1.5*fts_rank_score
        + 2.5*cosine + 1.5*canonical_bonus + 1.0*recency + 1.0*lane_priority
        + 0.5*graph_hit
"""
import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from lore_stack.retrieval.cosine import semantic_search
from lore_stack.retrieval.fts import ACTIVE_CHUNK_STATUSES, fts_search
from lore_stack.seams.embedder import Embedder
from lore_stack.writeback.engine import normalize

# Two unrelated 256-d hash vectors have cosine in roughly +/-0.06. A real topical
# overlap clears this comfortably; the floor keeps that noise from making an
# off-topic chunk a candidate (and from adding spurious score to a real one).
SEMANTIC_FLOOR = 0.12

# Only chunks in this lane carry an entity's *identity*. They must be earned by a
# direct hit or by the entity being a query target -- never pulled in purely by
# graph expansion from a neighbour (that would surface Boxwell's card on a query
# that only named Mirel).
IDENTITY_LANE = "character_card"


@dataclass
class Candidate:
    chunk_id: str
    row: sqlite3.Row
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


def _phrase_in(phrase: str, text_norm: str) -> bool:
    phrase = normalize(phrase)
    if not phrase:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text_norm) is not None


def resolve_query_targets(conn: sqlite3.Connection, query: str) -> list[str]:
    """Entity ids whose display name, slug words, or alias appears as a phrase in the query."""
    query_norm = normalize(query)
    targets = []
    for ent in conn.execute(
        "SELECT entity_id, slug, display_name FROM entities WHERE status != 'deprecated'"
        " ORDER BY entity_id"
    ):
        names = [ent["display_name"], ent["slug"].replace("-", " ")]
        names += [
            r["alias"]
            for r in conn.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias_id",
                (ent["entity_id"],),
            )
        ]
        if any(_phrase_in(name, query_norm) for name in names):
            targets.append(ent["entity_id"])
    return targets


def _entity_names(conn: sqlite3.Connection, entity_id: str) -> tuple[list[str], list[str]]:
    ent = conn.execute(
        "SELECT slug, display_name FROM entities WHERE entity_id=?", (entity_id,)
    ).fetchone()
    primary = [ent["display_name"], ent["slug"].replace("-", " ")] if ent else []
    aliases = [
        r["alias"]
        for r in conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias_id", (entity_id,)
        )
    ]
    return primary, aliases


def gather_candidates(
    conn: sqlite3.Connection,
    query: str,
    *,
    embedder: Optional[Embedder] = None,
) -> list[Candidate]:
    """Run all retrieval passes and fuse into one deterministic scored candidate list."""
    query_norm = normalize(query)
    targets = resolve_query_targets(conn, query)
    one_hop: set[str] = set()
    for target in targets:
        for row in conn.execute(
            "SELECT subject_entity_id, object_entity_id FROM facts"
            " WHERE (subject_entity_id=? OR object_entity_id=?)"
            " AND object_entity_id IS NOT NULL AND status IN ('canonical','soft')",
            (target, target),
        ):
            one_hop.update({row["subject_entity_id"], row["object_entity_id"]})
    one_hop -= set(targets)

    fts_scores = dict(fts_search(conn, query))
    cosine_scores: dict[str, float] = {}
    if embedder is not None:
        qvec = embedder.embed([query])[0]
        cosine_scores = dict(
            semantic_search(conn, qvec, model=getattr(embedder, "model_name", "unknown"))
        )
    # The noise floor is tuned for the 256-d FakeEmbedder; a live embedder with a
    # tighter cosine distribution can raise it by declaring `semantic_floor`. The
    # fake has no such attribute, so the default (and the gate's output) is unchanged.
    semantic_floor = getattr(embedder, "semantic_floor", SEMANTIC_FLOOR)

    max_story_seq = conn.execute("SELECT COALESCE(MAX(rowid), 1) FROM story_runs").fetchone()[0]
    story_seq = {
        r["story_id"]: r["rowid"]
        for r in conn.execute("SELECT rowid, story_id FROM story_runs")
    }

    candidates: dict[str, Candidate] = {}
    placeholders = ",".join("?" * len(ACTIVE_CHUNK_STATUSES))
    for row in conn.execute(
        f"SELECT * FROM lore_chunks WHERE status IN ({placeholders}) AND stale = 0"
        " ORDER BY chunk_id",
        ACTIVE_CHUNK_STATUSES,
    ):
        cand = Candidate(chunk_id=row["chunk_id"], row=row)
        mode = row["retrieval_mode"]

        exact_hit = alias_hit = 0.0
        if row["entity_id"]:
            primary, aliases = _entity_names(conn, row["entity_id"])
            if any(_phrase_in(n, query_norm) for n in primary):
                exact_hit = 1.0
                cand.reasons.append("exact_name")
            elif any(_phrase_in(a, query_norm) for a in aliases):
                alias_hit = 1.0
                cand.reasons.append("alias")
        if alias_hit == 0.0 and exact_hit == 0.0:
            keys = json.loads(row["activation_keys_json"])
            if any(_phrase_in(k, query_norm) for k in keys):
                alias_hit = 1.0
                cand.reasons.append("activation_key")

        fts_score = fts_scores.get(row["chunk_id"], 0.0)
        if fts_score:
            cand.reasons.append("fts")
        cosine = cosine_scores.get(row["chunk_id"], 0.0)
        if cosine < semantic_floor:
            cosine = 0.0  # below the noise floor -> not a semantic signal at all
        if cosine > 0:
            cand.reasons.append("semantic")

        if mode == "key":
            cosine = 0.0
        elif mode == "semantic":
            exact_hit = alias_hit = fts_score = 0.0

        direct_hit = bool(exact_hit or alias_hit or fts_score or cosine > 0.0)
        is_target = row["entity_id"] in targets

        graph_hit = 1.0 if row["entity_id"] in one_hop else 0.0
        # Graph expansion never qualifies an identity card for a non-target entity.
        if row["insertion_lane"] == IDENTITY_LANE and not is_target:
            graph_hit = 0.0
        if graph_hit:
            cand.reasons.append("graph_1hop")
        pinned_to_target = mode == "pinned" and is_target
        if pinned_to_target:
            cand.reasons.append("pinned")

        canonical_bonus = 1.0 if row["status"] == "canonical" else 0.0
        recency = (
            story_seq.get(row["story_id"], 0) / max_story_seq if row["story_id"] else 0.5
        )
        lane_priority = min(row["priority"], 1000) / 1000.0

        cand.score = (
            4.0 * exact_hit
            + 2.0 * alias_hit
            + 1.5 * fts_score
            + 2.5 * cosine
            + 1.5 * canonical_bonus
            + 1.0 * recency
            + 1.0 * lane_priority
            + 0.5 * graph_hit
            + (4.0 if pinned_to_target else 0.0)
        )

        # An identity card must be earned directly or by being a query target;
        # other lanes may still be pulled in by graph expansion alone.
        if row["insertion_lane"] == IDENTITY_LANE:
            relevant = direct_hit or is_target
        else:
            relevant = direct_hit or graph_hit or pinned_to_target
        # Lore-continuity fallback: when the query names no known entity but the
        # lore is non-empty, offer its recent continuity unconditionally as
        # connective tissue for a new story (the lore is the unit of connection).
        # An empty lore has no such chunks, so it still returns nothing.
        if not targets and row["insertion_lane"] == "recent_continuity":
            relevant = True
            if "lore_continuity" not in cand.reasons:
                cand.reasons.append("lore_continuity")
        if relevant:
            candidates[row["chunk_id"]] = cand

    return sorted(
        candidates.values(),
        key=lambda c: (-c.score, -c.row["priority"], c.chunk_id),
    )
