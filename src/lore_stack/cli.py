"""Local CLI over the lore-stack library. Every operation is exposed here;
the visualizer and the Hermes skill stub are thin shells over the same library."""
import argparse
import json
import sys
from pathlib import Path

from lore_stack.compiler import DEFAULT_TOTAL_BUDGET, compile_context
from lore_stack.db import connect, init_db
from lore_stack.models.delta import LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.seams.extractor import FakeExtractor
from lore_stack.writeback import (
    WritebackError,
    apply_delta,
    deprecate_chunk,
    deprecate_entity,
    deprecate_fact,
    manual_edit_fact,
    restore_entity,
)


def _cmd_init_db(args) -> int:
    conn = connect(args.db)
    applied = init_db(conn)
    print(f"initialized {args.db} (migrations applied: {applied or 'none, already current'})")
    return 0


def _load_delta(path: str) -> LoreDelta:
    raw = Path(path).read_text(encoding="utf-8")
    return LoreDelta.model_validate(json.loads(raw))


def _cmd_ingest_delta(args) -> int:
    conn = connect(args.db, auto_snapshot=True)
    try:
        delta = _load_delta(args.file)
    except Exception as exc:
        print(f"invalid delta: {exc}", file=sys.stderr)
        return 1
    story_text = Path(args.story_text).read_text(encoding="utf-8") if args.story_text else None
    try:
        report = apply_delta(conn, delta, story_text=story_text, embedder=FakeEmbedder())
    except WritebackError as exc:
        print(f"writeback failed: {exc}", file=sys.stderr)
        return 1
    print(report.model_dump_json(indent=2))
    return 0


def _cmd_ingest_story(args) -> int:
    conn = connect(args.db, auto_snapshot=True)
    story_path = Path(args.file)
    story_text = story_path.read_text(encoding="utf-8")
    extractor = FakeExtractor.from_fixture_dir(Path(args.fixtures))
    story_id = args.story_id or f"story_{story_path.stem}"
    try:
        delta = extractor.extract(story_text, story_id=story_id)
        report = apply_delta(conn, delta, story_text=story_text, embedder=FakeEmbedder())
    except (KeyError, WritebackError) as exc:
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1
    print(report.model_dump_json(indent=2))
    return 0


def _cmd_compile_context(args) -> int:
    conn = connect(args.db)
    result = compile_context(
        conn, args.query, embedder=FakeEmbedder(), total_budget=args.budget
    )
    if args.json:
        payload = {
            "compile_id": result.compile_id,
            "query": result.query,
            "targets": result.targets,
            "total_tokens": result.total_tokens,
            "budget_tokens": result.budget_tokens,
            "selected": result.selected,
            "dropped": result.dropped,
            "text": result.text,
        }
        output = json.dumps(payload, indent=2)
    else:
        output = result.text
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"wrote {args.out} ({result.total_tokens}/{result.budget_tokens} tokens,"
              f" {len(result.selected)} chunks)")
    else:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0


def _cmd_inspect(args) -> int:
    conn = connect(args.db)
    if args.what == "entity":
        if not args.slug:
            print("inspect entity requires --slug", file=sys.stderr)
            return 1
        ent = conn.execute("SELECT * FROM entities WHERE slug=?", (args.slug,)).fetchone()
        if ent is None:
            print(f"no entity with slug {args.slug!r}", file=sys.stderr)
            return 1
        payload = {
            "entity": dict(ent),
            "aliases": [dict(r) for r in conn.execute(
                "SELECT alias, normalized_alias, alias_type FROM entity_aliases"
                " WHERE entity_id=? ORDER BY alias_id", (ent["entity_id"],))],
            "facts": [dict(r) for r in conn.execute(
                "SELECT * FROM facts WHERE subject_entity_id=? ORDER BY fact_id",
                (ent["entity_id"],))],
            "chunks": [dict(r) for r in conn.execute(
                "SELECT chunk_id, title, insertion_lane, status FROM lore_chunks"
                " WHERE entity_id=? ORDER BY chunk_id", (ent["entity_id"],))],
        }
    elif args.what == "conflicts":
        payload = [dict(r) for r in conn.execute(
            "SELECT * FROM adjudication_queue WHERE status='open' ORDER BY item_id")]
    elif args.what == "motifs":
        payload = [dict(r) for r in conn.execute(
            "SELECT * FROM facts WHERE status='motif' ORDER BY fact_id")]
    else:  # stories
        payload = [dict(r) for r in conn.execute(
            "SELECT story_id, title, extraction_status, created_at FROM story_runs"
            " ORDER BY rowid")]
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _cmd_edit_fact(args) -> int:
    conn = connect(args.db, auto_snapshot=True)
    try:
        fact_id = manual_edit_fact(
            conn,
            entity_id=args.entity_id,
            predicate=args.predicate,
            object_literal=args.value,
            object_entity_id=args.object_entity_id,
        )
    except WritebackError as exc:
        print(f"edit rejected: {exc}", file=sys.stderr)
        return 1
    print(fact_id)
    return 0


def _cmd_deprecate(args) -> int:
    conn = connect(args.db, auto_snapshot=True)
    try:
        if args.entity_id:
            deprecate_entity(conn, args.entity_id)
        elif args.fact_id:
            deprecate_fact(conn, args.fact_id)
        elif args.chunk_id:
            deprecate_chunk(conn, args.chunk_id)
        else:
            print("deprecate requires one of --entity-id/--fact-id/--chunk-id", file=sys.stderr)
            return 1
    except WritebackError as exc:
        print(f"deprecate rejected: {exc}", file=sys.stderr)
        return 1
    print("ok (soft-deprecated; history preserved)")
    return 0


def _cmd_restore(args) -> int:
    conn = connect(args.db, auto_snapshot=True)
    try:
        restore_entity(conn, args.entity_id)
    except WritebackError as exc:
        print(f"restore rejected: {exc}", file=sys.stderr)
        return 1
    print("ok (entity restored as provisional; revive facts via edit-fact)")
    return 0


def _cmd_export(args) -> int:
    from lore_stack.visualizer.app import export_subgraph

    conn = connect(args.db)
    payload = export_subgraph(conn, entity_slug=args.entity)
    if args.format == "markdown":
        lines = ["# Lore export", ""]
        for node in payload["entities"]:
            lines.append(f"## {node['display_name']} ({node['kind']}, {node['status']})")
            for fact in node["facts"]:
                obj = fact["object_literal"] or fact["object_entity_id"]
                lines.append(f"- {fact['predicate']}: {obj} [{fact['status']}]")
            lines.append("")
        print("\n".join(lines))
    else:
        print(json.dumps(payload, indent=2, default=str))
    return 0


def _cmd_snapshot(args) -> int:
    from lore_stack import snapshots

    if args.action == "create":
        conn = connect(args.db)
        entry = snapshots.create(conn, args.db, args.label or "manual")
        conn.close()
        print(f"snapshot {entry['seq']:06d} created ({entry['operation']})")
        return 0
    if args.action == "list":
        rows = snapshots.list_snapshots(args.db)
        if not rows:
            print("(no snapshots)")
            return 0
        for e in rows:
            c = e["counts"]
            print(f"  {e['seq']:06d}  before {e['operation']:32}  "
                  f"{c['stories']}st {c['entities']}ent {c['facts']}fct "
                  f"{c['open_conflicts']}cf  {e['created_at']}")
        return 0
    # rollback
    if args.seq is None:
        print("rollback requires --seq <sequence>", file=sys.stderr)
        return 1
    try:
        info = snapshots.rollback(args.db, args.seq)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"rolled back to snapshot {info['restored_seq']:06d} "
          f"(state before {info['operation']}); a pre-rollback snapshot was saved")
    return 0


def _cmd_serve(args) -> int:
    from lore_stack.visualizer.app import create_app

    if bool(args.db) == bool(args.home):
        print("serve requires exactly one of --db (single lore) or --home (lore directory)",
              file=sys.stderr)
        return 1
    app = create_app(args.db or None, home=args.home or None)
    mode = f"home={args.home}" if args.home else f"db={args.db}"
    print(f"lore visualizer on http://127.0.0.1:{args.port} ({mode})")
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


def _cmd_lores(args) -> int:
    from lore_stack.visualizer.app import LORE_NAME_RE

    home = Path(args.home)
    if args.action == "create":
        if not args.name or not LORE_NAME_RE.match(args.name):
            print("lore name must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}", file=sys.stderr)
            return 1
        home.mkdir(parents=True, exist_ok=True)
        path = home / f"{args.name}.db"
        if path.exists():
            print(f"lore {args.name!r} already exists", file=sys.stderr)
            return 1
        conn = connect(path)
        init_db(conn)
        conn.close()
        print(f"created lore {args.name!r} at {path}")
        return 0
    # list
    if not home.exists():
        print("[]")
        return 0
    print(json.dumps([p.stem for p in sorted(home.glob("*.db"))], indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="lore-stack", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create the schema from empty in one command")
    p.add_argument("--db", required=True)
    p.set_defaults(func=_cmd_init_db)

    p = sub.add_parser("ingest-delta", help="validate and write back a LoreDelta JSON file")
    p.add_argument("--db", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--story-text", default=None, help="optional story text file")
    p.set_defaults(func=_cmd_ingest_delta)

    p = sub.add_parser("ingest-story", help="extract (FakeExtractor) + write back a story file")
    p.add_argument("--db", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--fixtures", required=True, help="dir of <name>.md + <name>.delta.json pairs")
    p.add_argument("--story-id", default=None)
    p.set_defaults(func=_cmd_ingest_story)

    p = sub.add_parser("compile-context", help="compile a bounded context block for a query")
    p.add_argument("--db", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--budget", type=int, default=DEFAULT_TOTAL_BUDGET)
    p.add_argument("--out", default=None)
    p.add_argument("--json", action="store_true", help="emit full audit JSON instead of text")
    p.set_defaults(func=_cmd_compile_context)

    p = sub.add_parser("inspect", help="inspect entities, conflicts, motifs, or stories")
    p.add_argument("what", choices=["entity", "conflicts", "motifs", "stories"])
    p.add_argument("--db", required=True)
    p.add_argument("--slug", default=None)
    p.set_defaults(func=_cmd_inspect)

    p = sub.add_parser("edit-fact", help="authoritative manual edit (canonical, bypasses adjudication)")
    p.add_argument("--db", required=True)
    p.add_argument("--entity-id", required=True)
    p.add_argument("--predicate", required=True)
    p.add_argument("--value", default=None)
    p.add_argument("--object-entity-id", default=None)
    p.set_defaults(func=_cmd_edit_fact)

    p = sub.add_parser("deprecate", help="soft-delete an entity, fact, or chunk")
    p.add_argument("--db", required=True)
    p.add_argument("--entity-id", default=None)
    p.add_argument("--fact-id", default=None)
    p.add_argument("--chunk-id", default=None)
    p.set_defaults(func=_cmd_deprecate)

    p = sub.add_parser("restore", help="reverse a soft delete of an entity")
    p.add_argument("--db", required=True)
    p.add_argument("--entity-id", required=True)
    p.set_defaults(func=_cmd_restore)

    p = sub.add_parser("export", help="export the lore subgraph as JSON or markdown")
    p.add_argument("--db", required=True)
    p.add_argument("--entity", default=None, help="restrict to one entity slug + neighbors")
    p.add_argument("--format", choices=["json", "markdown"], default="json")
    p.set_defaults(func=_cmd_export)

    p = sub.add_parser("serve", help="run the local lore visualizer web app")
    p.add_argument("--db", default=None, help="serve a single lore database")
    p.add_argument("--home", default=None, help="serve a directory of lores (switchable in the UI)")
    p.add_argument("--port", type=int, default=8377)
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("lores", help="list or create lores in a lore home directory")
    p.add_argument("action", choices=["list", "create"])
    p.add_argument("--home", required=True)
    p.add_argument("--name", default=None)
    p.set_defaults(func=_cmd_lores)

    p = sub.add_parser("snapshot", help="manage point-in-time snapshots of a lore")
    p.add_argument("action", choices=["create", "list", "rollback"])
    p.add_argument("--db", required=True)
    p.add_argument("--seq", type=int, default=None, help="snapshot sequence for rollback")
    p.add_argument("--label", default=None, help="label for a manual 'create'")
    p.set_defaults(func=_cmd_snapshot)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
