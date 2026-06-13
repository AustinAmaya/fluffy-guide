"""Bounded, lane-based, budget-enforced context compiler.

Deterministic by construction: identical DB + identical query => byte-identical
output. No timestamps appear in the compiled text; ordering is fully specified
(score desc, priority desc, chunk_id asc); over-budget chunks are dropped whole,
never truncated mid-fact. Every compile writes a compiler_runs audit row.
"""
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from lore_stack.retrieval.fusion import Candidate, gather_candidates, resolve_query_targets
from lore_stack.seams.embedder import Embedder
from lore_stack.writeback.engine import token_estimate

LANE_ORDER = ["character_card", "world_info", "relationships", "open_hooks", "recent_continuity"]
LANE_HEADERS = {
    "character_card": "[CHARACTER CARD]",
    "world_info": "[WORLD INFO]",
    "relationships": "[RELATIONSHIPS]",
    "open_hooks": "[OPEN HOOKS]",
    "recent_continuity": "[RECENT CONTINUITY]",
}
DEFAULT_LANE_BUDGETS = {
    "character_card": 400,
    "world_info": 350,
    "relationships": 250,
    "open_hooks": 250,
    "recent_continuity": 450,
}
DEFAULT_TOTAL_BUDGET = 1700

# Lane that carries an entity's identity card, by entity kind.
CARD_LANE_BY_KIND = {
    "character": "character_card",
    "location": "world_info",
    "item": "world_info",
    "organization": "world_info",
    "event": "world_info",
    "concept": "world_info",
}


@dataclass
class CompiledContext:
    compile_id: str
    query: str
    text: str
    total_tokens: int
    budget_tokens: int
    targets: list[str]
    selected: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)


def _normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.split("\n")]
    out = "\n".join(lines).strip("\n")
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out + "\n" if out else ""


def _synthesize_card(conn: sqlite3.Connection, entity_id: str) -> Optional[dict]:
    """Build a deterministic identity card from an entity's active facts, for
    query targets whose extraction never authored a card chunk. Soft facts are
    marked unconfirmed; motif and deprecated facts never appear."""
    ent = conn.execute(
        "SELECT * FROM entities WHERE entity_id=? AND status != 'deprecated'", (entity_id,)
    ).fetchone()
    if ent is None:
        return None
    parts = []
    for fact in conn.execute(
        "SELECT * FROM facts WHERE subject_entity_id=? AND status IN ('canonical','soft')"
        " ORDER BY predicate, fact_id",
        (entity_id,),
    ):
        if fact["object_entity_id"]:
            obj_row = conn.execute(
                "SELECT display_name FROM entities WHERE entity_id=?",
                (fact["object_entity_id"],),
            ).fetchone()
            obj = obj_row["display_name"] if obj_row else fact["object_entity_id"]
        else:
            obj = fact["object_literal"] or ""
        marker = "" if fact["status"] == "canonical" else " [unconfirmed]"
        parts.append(f"{fact['predicate']} {obj}{marker}")
    summary = (ent["summary"] or "").strip()
    body = f"{ent['display_name']} — {summary}" if summary else ent["display_name"]
    if parts:
        body = f"{body} Established: {'; '.join(parts)}."
    return {
        "chunk_id": f"factcard_{entity_id}",
        "entity_id": entity_id,
        "title": f"{ent['display_name']} (from facts)",
        "body": body,
        "insertion_lane": CARD_LANE_BY_KIND[ent["kind"]],
        "priority": 950,
        "token_estimate": token_estimate(body),
    }


def compile_context(
    conn: sqlite3.Connection,
    query: str,
    *,
    embedder: Optional[Embedder] = None,
    total_budget: int = DEFAULT_TOTAL_BUDGET,
    lane_budgets: Optional[dict[str, int]] = None,
) -> CompiledContext:
    lane_budgets = dict(lane_budgets or DEFAULT_LANE_BUDGETS)
    targets = resolve_query_targets(conn, query)
    candidates = gather_candidates(conn, query, embedder=embedder)

    by_lane: dict[str, list[Candidate]] = {lane: [] for lane in LANE_ORDER}
    for cand in candidates:
        by_lane[cand.row["insertion_lane"]].append(cand)
    # Every query target deserves identity representation: if no authored chunk
    # in its card lane is bound to it, synthesize a card from its facts.
    for target in targets:
        lane_rows = conn.execute(
            "SELECT kind FROM entities WHERE entity_id=?", (target,)
        ).fetchone()
        card_lane = CARD_LANE_BY_KIND[lane_rows["kind"]] if lane_rows else None
        has_card = card_lane and any(
            c.row["entity_id"] == target for c in by_lane[card_lane]
        )
        if card_lane and not has_card:
            synth = _synthesize_card(conn, target)
            if synth is not None:
                by_lane[card_lane].append(
                    Candidate(chunk_id=synth["chunk_id"], row=synth, score=5.0,
                              reasons=["target_without_card", "synthesized_from_facts"])
                )
    # Score decides what is a candidate at all; within a lane, packing under
    # budget pressure is priority-first (invariant: dropped by priority).
    for lane in LANE_ORDER:
        by_lane[lane].sort(key=lambda c: (-c.row["priority"], -c.score, c.chunk_id))

    selected: list[dict] = []
    dropped: list[dict] = []
    chosen: dict[str, list[Candidate]] = {lane: [] for lane in LANE_ORDER}
    total_tokens = 0
    any_content = False
    for lane in LANE_ORDER:
        lane_tokens = 0
        header_cost = token_estimate(LANE_HEADERS[lane])
        for cand in by_lane[lane]:
            cost = cand.row["token_estimate"]
            # Joiner accounting: opening a lane pays its header + newline (and a
            # lane separator / the final trailing newline); each further chunk in
            # a lane pays its joining newline. Per-piece ceil(len/4) sums to at
            # least the estimate of the assembled text, so the bound is real.
            if not chosen[lane]:
                extra = header_cost + 1 + 1  # header, its newline, separator/trailer
                if not any_content:
                    extra += 1  # final trailing newline, charged once
            else:
                extra = 1  # "\n" between bodies
            if lane_tokens + cost > lane_budgets.get(lane, 0):
                dropped.append(_trace(cand, lane, "lane_budget_exceeded"))
                continue
            if total_tokens + cost + extra > total_budget:
                dropped.append(_trace(cand, lane, "total_budget_exceeded"))
                continue
            chosen[lane].append(cand)
            lane_tokens += cost
            total_tokens += cost + extra
            any_content = True
            selected.append(_trace(cand, lane, "included"))

    parts = []
    for lane in LANE_ORDER:
        if not chosen[lane]:
            continue
        bodies = "\n".join(c.row["body"].strip() for c in chosen[lane])
        parts.append(f"{LANE_HEADERS[lane]}\n{bodies}")
    text = _normalize_text("\n\n".join(parts))

    n = conn.execute("SELECT COUNT(*) FROM compiler_runs").fetchone()[0]
    compile_id = f"cmp_{n + 1:06d}"
    with conn:
        conn.execute(
            "INSERT INTO compiler_runs (compile_id, query_text, target_entity_id,"
            " compiled_context_text, selected_chunk_ids_json, budget_tokens, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (compile_id, query, targets[0] if targets else None, text,
             json.dumps([s["chunk_id"] for s in selected]), total_budget,
             datetime.now(timezone.utc).isoformat()),
        )
    return CompiledContext(
        compile_id=compile_id,
        query=query,
        text=text,
        total_tokens=total_tokens,
        budget_tokens=total_budget,
        targets=targets,
        selected=selected,
        dropped=dropped,
    )


def _trace(cand: Candidate, lane: str, disposition: str) -> dict:
    return {
        "chunk_id": cand.chunk_id,
        "lane": lane,
        "title": cand.row["title"],
        "score": round(cand.score, 6),
        "reasons": cand.reasons,
        "token_estimate": cand.row["token_estimate"],
        "disposition": disposition,
    }
