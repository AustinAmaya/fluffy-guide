"""Local lore visualizer: a JSON API over the library plus a static graph frontend.

All writes go through the same library writeback API as everything else. The two
human-authoritative carve-outs (manual edit => immediate canonical; delete =>
soft deprecate) are the ONLY paths that bypass normal canonization, and both
preserve history.
"""
import json
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request

from lore_stack.compiler import compile_context
from lore_stack.retrieval import gather_candidates
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import (
    WritebackError,
    deprecate_chunk,
    deprecate_entity,
    deprecate_fact,
    manual_edit_fact,
    restore_entity,
)

STATIC_DIR = Path(__file__).parent / "static"


def _fact_payload(conn: sqlite3.Connection, fact: sqlite3.Row) -> dict:
    payload = dict(fact)
    if fact["source_claim_id"]:
        claim = conn.execute(
            "SELECT story_id, confidence, evidence_excerpt FROM claims WHERE claim_id=?",
            (fact["source_claim_id"],),
        ).fetchone()
        if claim:
            story = conn.execute(
                "SELECT title FROM story_runs WHERE story_id=?", (claim["story_id"],)
            ).fetchone()
            payload["provenance"] = {
                "kind": "extracted",
                "story_id": claim["story_id"],
                "story_title": story["title"] if story else None,
                "claim_confidence": claim["confidence"],
                "evidence_excerpt": claim["evidence_excerpt"],
            }
    elif fact["manual_source_id"]:
        src = conn.execute(
            "SELECT created_at FROM sources WHERE source_id=?", (fact["manual_source_id"],)
        ).fetchone()
        payload["provenance"] = {
            "kind": "manual",
            "source_id": fact["manual_source_id"],
            "edited_at": src["created_at"] if src else None,
        }
    return payload


def export_subgraph(conn: sqlite3.Connection, entity_slug: str | None = None) -> dict:
    """Active entities (optionally one entity + 1-hop neighbors) with facts and edges."""
    if entity_slug:
        root = conn.execute(
            "SELECT entity_id FROM entities WHERE slug=?", (entity_slug,)
        ).fetchone()
        if root is None:
            raise WritebackError(f"unknown entity slug {entity_slug!r}")
        ids = {root["entity_id"]}
        for row in conn.execute(
            "SELECT subject_entity_id, object_entity_id FROM facts"
            " WHERE (subject_entity_id=? OR object_entity_id=?)"
            " AND object_entity_id IS NOT NULL AND status != 'deprecated'",
            (root["entity_id"], root["entity_id"]),
        ):
            ids.update({row["subject_entity_id"], row["object_entity_id"]})
        ent_rows = conn.execute(
            f"SELECT * FROM entities WHERE entity_id IN ({','.join('?' * len(ids))})"
            " AND status != 'deprecated' ORDER BY entity_id",
            tuple(sorted(ids)),
        ).fetchall()
    else:
        ent_rows = conn.execute(
            "SELECT * FROM entities WHERE status != 'deprecated' ORDER BY entity_id"
        ).fetchall()

    keep = {e["entity_id"] for e in ent_rows}
    entities = []
    for ent in ent_rows:
        facts = conn.execute(
            "SELECT * FROM facts WHERE subject_entity_id=? AND status != 'deprecated'"
            " ORDER BY fact_id",
            (ent["entity_id"],),
        ).fetchall()
        entities.append({
            **dict(ent),
            "aliases": [r["alias"] for r in conn.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias_id",
                (ent["entity_id"],))],
            "facts": [_fact_payload(conn, f) for f in facts],
        })
    edges = [
        dict(r)
        for r in conn.execute(
            "SELECT fact_id, subject_entity_id, predicate, object_entity_id, status,"
            " confidence FROM facts WHERE object_entity_id IS NOT NULL"
            " AND status != 'deprecated' ORDER BY fact_id"
        )
        if r["subject_entity_id"] in keep and r["object_entity_id"] in keep
    ]
    return {"entities": entities, "edges": edges}


def create_app(db_path: str | Path) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    db_path = str(db_path)

    def conn() -> sqlite3.Connection:
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        return c

    @app.get("/")
    def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/entities")
    def entities():
        c = conn()
        rows = c.execute(
            "SELECT * FROM entities WHERE status != 'deprecated' ORDER BY entity_id"
        ).fetchall()
        out = [
            {**dict(r), "aliases": [a["alias"] for a in c.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias_id",
                (r["entity_id"],))]}
            for r in rows
        ]
        c.close()
        return jsonify(out)

    @app.get("/api/graph")
    def graph():
        c = conn()
        out = export_subgraph(c)
        c.close()
        return jsonify(out)

    @app.get("/api/facts")
    def facts():
        entity_id = request.args.get("entity")
        if not entity_id:
            return jsonify({"error": "entity query parameter required"}), 400
        c = conn()
        rows = c.execute(
            "SELECT * FROM facts WHERE subject_entity_id=? ORDER BY fact_id", (entity_id,)
        ).fetchall()
        out = [_fact_payload(c, r) for r in rows]  # includes deprecated history + motifs
        c.close()
        return jsonify(out)

    @app.get("/api/conflicts")
    def conflicts():
        c = conn()
        rows = c.execute(
            "SELECT * FROM adjudication_queue WHERE status='open' ORDER BY item_id"
        ).fetchall()
        out = [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]
        c.close()
        return jsonify(out)

    @app.get("/api/motifs")
    def motifs():
        c = conn()
        out = [_fact_payload(c, r) for r in c.execute(
            "SELECT * FROM facts WHERE status='motif' ORDER BY fact_id")]
        c.close()
        return jsonify(out)

    @app.get("/api/retrieval")
    def retrieval():
        query = request.args.get("q", "")
        if not query.strip():
            return jsonify({"error": "q query parameter required"}), 400
        c = conn()
        cands = gather_candidates(c, query, embedder=FakeEmbedder())
        out = [
            {"chunk_id": cd.chunk_id, "title": cd.row["title"],
             "lane": cd.row["insertion_lane"], "score": round(cd.score, 6),
             "reasons": cd.reasons}
            for cd in cands
        ]
        c.close()
        return jsonify(out)

    @app.post("/api/query_context")
    def query_context():
        body = request.get_json(silent=True) or {}
        query = body.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return jsonify({"error": "query is required"}), 400
        kwargs = {}
        if isinstance(body.get("budget"), int) and body["budget"] > 0:
            kwargs["total_budget"] = body["budget"]
        c = conn()
        result = compile_context(c, query, embedder=FakeEmbedder(), **kwargs)
        c.close()
        return jsonify({
            "compile_id": result.compile_id,
            "query": result.query,
            "targets": result.targets,
            "text": result.text,
            "total_tokens": result.total_tokens,
            "budget_tokens": result.budget_tokens,
            "selected": result.selected,
            "dropped": result.dropped,
        })

    @app.post("/api/entity/<entity_id>/edit")
    def edit_entity(entity_id):
        body = request.get_json(silent=True) or {}
        predicate = body.get("predicate")
        if not isinstance(predicate, str) or not predicate.strip():
            return jsonify({"error": "predicate is required"}), 400
        value = body.get("value")
        object_entity_id = body.get("object_entity_id")
        c = conn()
        try:
            fact_id = manual_edit_fact(
                c, entity_id=entity_id, predicate=predicate.strip(),
                object_literal=value, object_entity_id=object_entity_id,
            )
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        fact = c.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        out = _fact_payload(c, fact)
        c.close()
        return jsonify(out)

    @app.post("/api/entity/<entity_id>/deprecate")
    def deprecate_entity_route(entity_id):
        c = conn()
        try:
            deprecate_entity(c, entity_id)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "entity_id": entity_id, "soft": True})

    @app.post("/api/entity/<entity_id>/restore")
    def restore_entity_route(entity_id):
        c = conn()
        try:
            restore_entity(c, entity_id)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "entity_id": entity_id})

    @app.post("/api/fact/<fact_id>/deprecate")
    def deprecate_fact_route(fact_id):
        c = conn()
        try:
            deprecate_fact(c, fact_id)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "fact_id": fact_id, "soft": True})

    @app.post("/api/chunk/<chunk_id>/deprecate")
    def deprecate_chunk_route(chunk_id):
        c = conn()
        try:
            deprecate_chunk(c, chunk_id)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "chunk_id": chunk_id, "soft": True})

    @app.get("/api/export")
    def export():
        c = conn()
        try:
            out = export_subgraph(c, entity_slug=request.args.get("entity"))
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify(out)

    return app
