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

from lore_stack import snapshots, staging
from lore_stack.db import connect, init_db

from lore_stack.compiler import compile_context
from lore_stack.retrieval import gather_candidates
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import (
    WritebackError,
    confirm_chunk_fresh,
    deprecate_chunk,
    deprecate_entity,
    deprecate_fact,
    manual_edit_fact,
    resolve_contradiction,
    resolve_merge_suggestion,
    resolve_supersession,
    restore_entity,
)

from lore_stack import frozen  # noqa: E402
from lore_stack.lores import LORE_NAME_RE, LoreError, copy_lore  # noqa: E402

STATIC_DIR = Path(__file__).parent / "static"


class LoreSelectionError(Exception):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


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
        story_count = conn.execute(
            "SELECT COUNT(DISTINCT story_id) FROM story_entities WHERE entity_id=?",
            (ent["entity_id"],),
        ).fetchone()[0]
        entities.append({
            **dict(ent),
            "aliases": [r["alias"] for r in conn.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias_id",
                (ent["entity_id"],))],
            "facts": [_fact_payload(conn, f) for f in facts],
            # how many distinct stories corroborate this entity -- the "core" signal the
            # graph's strength slider thresholds on (recurrence spreads; confidence doesn't).
            "story_count": story_count,
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


def create_app(db_path: str | Path | None = None, *, home: str | Path | None = None) -> Flask:
    """Serve one lore database (db_path) or a directory of them (home).

    In home mode every lore is an independent <name>.db file; API requests
    select one via the ?lore=<name> query parameter, and /api/lores lists,
    creates, and describes them.
    """
    if (db_path is None) == (home is None):
        raise ValueError("create_app requires exactly one of db_path or home")
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    home_dir = Path(home) if home is not None else None
    if home_dir is not None:
        home_dir.mkdir(parents=True, exist_ok=True)

    @app.errorhandler(LoreSelectionError)
    def _lore_selection_error(exc):
        return jsonify({"error": exc.message}), exc.status

    def _open(path: Path | str, *, auto_snapshot: bool = False) -> sqlite3.Connection:
        return connect(path, auto_snapshot=auto_snapshot)

    def _lore_path() -> Path:
        """Resolve and validate the target lore's db path for the current request."""
        if home_dir is None:
            return Path(db_path)
        name = request.args.get("lore", "")
        if not LORE_NAME_RE.match(name):
            raise LoreSelectionError(
                "a valid ?lore=<name> query parameter is required in multi-lore mode", 400
            )
        path = home_dir / f"{name}.db"
        if not path.exists():
            raise LoreSelectionError(f"unknown lore {name!r}", 404)
        return path

    def conn(*, auto_snapshot: bool = False) -> sqlite3.Connection:
        return _open(_lore_path(), auto_snapshot=auto_snapshot)

    @app.get("/")
    def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/lores")
    def list_lores():
        if home_dir is None:
            return jsonify({"error": "server is running in single-database mode"}), 404
        out = []
        for path in sorted(home_dir.glob("*.db")):
            entry = {"name": path.stem, "entities": 0, "stories": 0, "open_conflicts": 0,
                     "has_frozen": frozen.has_frozen(home_dir, path.stem)}
            c = _open(path)
            try:
                entry["entities"] = c.execute(
                    "SELECT COUNT(*) FROM entities WHERE status != 'deprecated'"
                ).fetchone()[0]
                entry["stories"] = c.execute("SELECT COUNT(*) FROM story_runs").fetchone()[0]
                entry["open_conflicts"] = c.execute(
                    "SELECT COUNT(*) FROM adjudication_queue WHERE status='open'"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                entry["uninitialized"] = True
            c.close()
            out.append(entry)
        return jsonify(out)

    @app.post("/api/lores")
    def create_lore():
        """Create a lore: empty by default, or a copy of an existing lore when the
        body carries `copy_from`."""
        if home_dir is None:
            return jsonify({"error": "server is running in single-database mode"}), 404
        body = request.get_json(silent=True) or {}
        name = body.get("name", "")
        copy_from = body.get("copy_from")
        if not isinstance(name, str) or not LORE_NAME_RE.match(name):
            return jsonify({"error": "lore name must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}"}), 400
        path = home_dir / f"{name}.db"
        if path.exists():
            return jsonify({"error": f"lore {name!r} already exists"}), 409
        if copy_from:
            try:
                copy_lore(home_dir, copy_from, name)
            except LoreError as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify({"ok": True, "name": name, "copied_from": copy_from})
        c = _open(path)
        init_db(c)
        c.close()
        return jsonify({"ok": True, "name": name})

    @app.post("/api/lores/<name>/reset")
    def reset_lore_route(name):
        if home_dir is None:
            return jsonify({"error": "server is running in single-database mode"}), 404
        try:
            frozen.reset(home_dir, name)
        except LoreError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "name": name})

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
        c = conn(auto_snapshot=True)
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
        c = conn(auto_snapshot=True)
        try:
            deprecate_entity(c, entity_id)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "entity_id": entity_id, "soft": True})

    @app.post("/api/entity/<entity_id>/restore")
    def restore_entity_route(entity_id):
        c = conn(auto_snapshot=True)
        try:
            restore_entity(c, entity_id)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "entity_id": entity_id})

    @app.post("/api/fact/<fact_id>/deprecate")
    def deprecate_fact_route(fact_id):
        c = conn(auto_snapshot=True)
        try:
            deprecate_fact(c, fact_id)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "fact_id": fact_id, "soft": True})

    @app.post("/api/chunk/<chunk_id>/deprecate")
    def deprecate_chunk_route(chunk_id):
        c = conn(auto_snapshot=True)
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

    @app.post("/api/conflicts/<item_id>/resolve")
    def resolve_conflict_route(item_id):
        body = request.get_json(silent=True) or {}
        decision = body.get("decision", "")
        c = conn(auto_snapshot=True)
        try:
            # Same resolve action drives contradictions and supersessions; dispatch
            # on the item's kind (both share the keep_existing/accept_proposed vocab).
            kind = c.execute(
                "SELECT item_kind FROM adjudication_queue WHERE item_id=?", (item_id,)
            ).fetchone()
            if kind is not None and kind["item_kind"] == "supersession":
                resolve_supersession(c, item_id, decision)
            else:
                resolve_contradiction(c, item_id, decision)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "item_id": item_id, "decision": decision})

    @app.post("/api/merge/<item_id>/resolve")
    def resolve_merge_route(item_id):
        body = request.get_json(silent=True) or {}
        keep = body.get("keep")
        if not keep:
            return jsonify({"error": "keep (fact_id) is required"}), 400
        c = conn(auto_snapshot=True)
        try:
            resolve_merge_suggestion(c, item_id, keep)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "item_id": item_id, "kept": keep})

    @app.get("/api/stale-chunks")
    def stale_chunks():
        c = conn()
        rows = c.execute(
            "SELECT chunk_id, title, insertion_lane, entity_id, derived_from_fact_ids"
            " FROM lore_chunks WHERE stale=1 AND status IN ('provisional','canonical')"
            " ORDER BY chunk_id"
        ).fetchall()
        out = [dict(r) for r in rows]
        c.close()
        return jsonify(out)

    @app.post("/api/chunk/<chunk_id>/confirm")
    def confirm_chunk_route(chunk_id):
        c = conn(auto_snapshot=True)
        try:
            confirm_chunk_fresh(c, chunk_id)
        except WritebackError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "chunk_id": chunk_id})

    @app.get("/api/staged")
    def list_staged_route():
        c = conn()
        out = staging.list_staged(c, status=request.args.get("status", "pending"))
        c.close()
        return jsonify(out)

    @app.get("/api/staged/<staging_id>")
    def get_staged_route(staging_id):
        c = conn()
        out = staging.get_staged(c, staging_id)
        c.close()
        if out is None:
            return jsonify({"error": f"unknown stage {staging_id}"}), 404
        return jsonify(out)

    @app.post("/api/staged/<staging_id>/apply")
    def apply_staged_route(staging_id):
        body = request.get_json(silent=True) or {}
        selection = body.get("selection")  # None -> apply everything
        c = conn(auto_snapshot=True)
        try:
            report = staging.apply_staged(
                c, staging_id, selection=selection, embedder=FakeEmbedder()
            )
        except staging.StagingError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify(report.model_dump())

    @app.post("/api/staged/<staging_id>/discard")
    def discard_staged_route(staging_id):
        c = conn()
        try:
            staging.discard_staged(c, staging_id)
        except staging.StagingError as exc:
            c.close()
            return jsonify({"error": str(exc)}), 400
        c.close()
        return jsonify({"ok": True, "staging_id": staging_id})

    @app.get("/api/snapshots")
    def list_snapshots_route():
        return jsonify(snapshots.list_snapshots(_lore_path()))

    @app.get("/api/snapshots/<int:seq>/preview")
    def preview_snapshot_route(seq):
        """Read-only view of a snapshot's lore graph, without restoring it."""
        try:
            snap_path = snapshots.snapshot_file(_lore_path(), seq)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        c = connect(snap_path)  # SELECT-only; never mutated, never auto-snapshots
        try:
            out = export_subgraph(c)
        finally:
            c.close()
        return jsonify(out)

    @app.post("/api/snapshots/<int:seq>/rollback")
    def rollback_route(seq):
        try:
            info = snapshots.rollback(_lore_path(), seq)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify({"ok": True, **info})

    return app
