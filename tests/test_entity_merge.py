"""Operator-initiated entity merge: propose -> a queued suggestion; resolve folds
the duplicate into the survivor (facts/relationships/aliases/story mentions
re-pointed; the duplicate soft-deprecated) while the invariant suite stays satisfied.
'dismiss' just clears it. This is the manual counterpart to value-merge suggestions."""
import pytest
from invariant_checks import assert_invariants

from lore_stack.db import connect, init_db
from lore_stack.models.delta import LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import (
    WritebackError,
    apply_delta,
    propose_entity_merge,
    resolve_entity_merge,
)


def _ent(slug, name, kind="organization"):
    return {"slug": slug, "display_name": name, "kind": kind, "aliases": [],
            "summary": name, "confidence": 0.9, "evidence_excerpt": name}


def _delta(story_id, entities, claims):
    return LoreDelta.model_validate({
        "story_id": story_id, "story_title": story_id, "story_summary": "s",
        "entities": entities, "claims": claims, "chunks": [], "open_questions": []})


def _two_duplicate_teams(tmp_path):
    conn = connect(tmp_path / "lore.db")
    init_db(conn)
    apply_delta(conn, _delta("s1",
        [_ent("the-super-kitties", "The Super Kitties"), _ent("ginny", "Ginny", "character")],
        [{"subject_slug": "ginny", "predicate": "linked_to", "object_slug": "the-super-kitties",
          "confidence": 0.9, "evidence_excerpt": "x"}]), embedder=FakeEmbedder())
    apply_delta(conn, _delta("s2",
        [_ent("superkitties", "SuperKitties"), _ent("buddy", "Buddy", "character")],
        [{"subject_slug": "buddy", "predicate": "linked_to", "object_slug": "superkitties",
          "confidence": 0.9, "evidence_excerpt": "x"}]), embedder=FakeEmbedder())
    return conn


def test_propose_then_resolve_folds_duplicate(tmp_path):
    conn = _two_duplicate_teams(tmp_path)
    keep, drop = "ent_the-super-kitties", "ent_superkitties"

    item = propose_entity_merge(conn, [keep, drop])
    assert conn.execute("SELECT status FROM adjudication_queue WHERE item_id=?",
                        (item,)).fetchone()["status"] == "open"
    assert propose_entity_merge(conn, [drop, keep]) == item  # idempotent regardless of order

    resolve_entity_merge(conn, item, keep)

    # the duplicate is soft-deprecated; the survivor lives on
    assert conn.execute("SELECT status FROM entities WHERE entity_id=?", (drop,)).fetchone()["status"] == "deprecated"
    assert conn.execute("SELECT status FROM entities WHERE entity_id=?", (keep,)).fetchone()["status"] != "deprecated"
    # the duplicate's relationship (buddy -> SuperKitties) now points at the survivor
    row = conn.execute(
        "SELECT object_entity_id FROM facts WHERE subject_entity_id='ent_buddy'"
        " AND predicate='linked_to' AND status!='deprecated'").fetchone()
    assert row["object_entity_id"] == keep
    # nothing live still references the duplicate
    assert conn.execute(
        "SELECT COUNT(*) FROM facts WHERE (subject_entity_id=? OR object_entity_id=?)"
        " AND status!='deprecated'", (drop, drop)).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM story_entities WHERE entity_id=?", (drop,)).fetchone()[0] == 0
    # the survivor, now corroborated by two stories, is canonical
    assert conn.execute("SELECT status FROM entities WHERE entity_id=?", (keep,)).fetchone()["status"] == "canonical"
    assert conn.execute(
        "SELECT status FROM adjudication_queue WHERE item_id=?", (item,)).fetchone()["status"] == "resolved"
    assert_invariants(conn)


def test_dismiss_leaves_both(tmp_path):
    conn = _two_duplicate_teams(tmp_path)
    item = propose_entity_merge(conn, ["ent_the-super-kitties", "ent_superkitties"])
    resolve_entity_merge(conn, item, "dismiss")
    assert conn.execute("SELECT status FROM adjudication_queue WHERE item_id=?",
                        (item,)).fetchone()["status"] == "dismissed"
    for eid in ("ent_the-super-kitties", "ent_superkitties"):
        assert conn.execute("SELECT status FROM entities WHERE entity_id=?", (eid,)).fetchone()["status"] != "deprecated"
    assert_invariants(conn)


def test_propose_needs_two_known_entities(tmp_path):
    conn = _two_duplicate_teams(tmp_path)
    with pytest.raises(WritebackError):
        propose_entity_merge(conn, ["ent_the-super-kitties"])
    with pytest.raises(WritebackError):
        propose_entity_merge(conn, ["ent_the-super-kitties", "ent_nope"])
