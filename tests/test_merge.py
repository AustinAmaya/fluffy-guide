"""Aggressive merge suggestions: near-duplicate soft-fact values open a
merge_suggestion (never auto-merge); the operator resolves by keeping one."""
import pytest
from invariant_checks import assert_invariants

from lore_stack.models.delta import ClaimInput, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta, resolve_merge_suggestion
from lore_stack.writeback.engine import MERGE_THRESHOLD


def _carry(story_id, obj):
    return LoreDelta(
        story_id=story_id, story_title="t", story_summary="s",
        entities=[{"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
                   "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"}],
        claims=[ClaimInput(subject_slug="boxwell", predicate="carries", object_literal=obj,
                           confidence=0.9, evidence_excerpt="e")],
        chunks=[],
    )


def test_near_duplicate_values_open_a_merge_suggestion(db):
    apply_delta(db, _carry("c1", "cedar tool case"), embedder=FakeEmbedder())
    report = apply_delta(db, _carry("c2", "a cedar case of tools"), embedder=FakeEmbedder())

    assert len(report.merge_suggestions_opened) == 1
    item = db.execute(
        "SELECT * FROM adjudication_queue WHERE item_kind='merge_suggestion'"
    ).fetchone()
    assert item is not None and item["status"] == "open"
    import json
    payload = json.loads(item["payload_json"])
    assert payload["cosine"] >= MERGE_THRESHOLD
    assert {payload["fact_a_text"], payload["fact_b_text"]} == {
        "cedar tool case", "a cedar case of tools"}
    # Both facts still exist (nothing auto-merged).
    assert db.execute(
        "SELECT COUNT(*) FROM facts WHERE predicate='carries' AND status='soft'"
    ).fetchone()[0] == 2
    assert_invariants(db)


def test_distinct_values_do_not_suggest(db):
    apply_delta(db, _carry("d1", "cedar tool case"), embedder=FakeEmbedder())
    report = apply_delta(db, _carry("d2", "a brass compass"), embedder=FakeEmbedder())
    assert report.merge_suggestions_opened == []
    assert db.execute(
        "SELECT COUNT(*) FROM adjudication_queue WHERE item_kind='merge_suggestion'"
    ).fetchone()[0] == 0
    assert_invariants(db)


def test_no_suggestions_without_embedder(db):
    apply_delta(db, _carry("n1", "cedar tool case"))  # no embedder
    report = apply_delta(db, _carry("n2", "a cedar case of tools"))
    assert report.merge_suggestions_opened == []
    assert_invariants(db)


def test_resolve_merge_keeps_one_deprecates_other(db):
    apply_delta(db, _carry("r1", "cedar tool case"), embedder=FakeEmbedder())
    apply_delta(db, _carry("r2", "a cedar case of tools"), embedder=FakeEmbedder())
    import json
    item = db.execute(
        "SELECT item_id, payload_json FROM adjudication_queue WHERE item_kind='merge_suggestion'"
    ).fetchone()
    payload = json.loads(item["payload_json"])
    keep = payload["fact_a"]
    drop = payload["fact_b"]

    resolve_merge_suggestion(db, item["item_id"], keep)

    assert db.execute("SELECT status FROM facts WHERE fact_id=?", (keep,)).fetchone()[0] == "soft"
    assert db.execute("SELECT status FROM facts WHERE fact_id=?", (drop,)).fetchone()[0] == "deprecated"
    resolved = db.execute(
        "SELECT status, payload_json FROM adjudication_queue WHERE item_id=?", (item["item_id"],)
    ).fetchone()
    assert resolved["status"] == "resolved"
    assert json.loads(resolved["payload_json"])["resolution"] == {"kept": keep, "merged": drop}
    assert_invariants(db)


def test_resolve_rejects_bad_inputs(db):
    apply_delta(db, _carry("b1", "cedar tool case"), embedder=FakeEmbedder())
    apply_delta(db, _carry("b2", "a cedar case of tools"), embedder=FakeEmbedder())
    item_id = db.execute(
        "SELECT item_id FROM adjudication_queue WHERE item_kind='merge_suggestion'"
    ).fetchone()[0]
    from lore_stack.writeback import WritebackError

    with pytest.raises(WritebackError):
        resolve_merge_suggestion(db, item_id, "fct_not_in_pair")
    with pytest.raises(WritebackError):
        resolve_merge_suggestion(db, "mrg_nope", "fct_whatever")
