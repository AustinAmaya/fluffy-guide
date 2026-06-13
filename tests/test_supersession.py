"""Supersession: a corroborated new value on a single-valued `state` predicate
(lives_in -- you can move) opens a supersession proposal, not a contradiction.
Resolving it canonizes the new value, deprecates the old, and records lineage.
`permanent` single-valued predicates (profession) still open plain contradictions.
"""
import json

from invariant_checks import assert_invariants

from lore_stack.models.delta import ClaimInput, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta, resolve_supersession


def _home_delta(story_id, place):
    return LoreDelta(
        story_id=story_id, story_title=story_id, story_summary="s",
        entities=[
            {"slug": "wren", "display_name": "Wren", "kind": "character",
             "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"},
            {"slug": place, "display_name": place, "kind": "location",
             "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"},
        ],
        claims=[ClaimInput(subject_slug="wren", predicate="lives_in", object_slug=place,
                           confidence=0.95, evidence_excerpt="e")],
        chunks=[],
    )


def _canon_home(db):
    row = db.execute(
        "SELECT e.slug FROM facts f JOIN entities e ON e.entity_id = f.object_entity_id"
        " WHERE f.subject_entity_id='ent_wren' AND f.predicate='lives_in'"
        " AND f.status='canonical'"
    ).fetchone()
    return row["slug"] if row else None


def test_state_predicate_opens_supersession_not_contradiction(db):
    # Corroborate Wren lives_in the-hollow across two stories -> canonical.
    apply_delta(db, _home_delta("s1", "the-hollow"), embedder=FakeEmbedder())
    apply_delta(db, _home_delta("s2", "the-hollow"), embedder=FakeEmbedder())
    assert _canon_home(db) == "the-hollow"

    # A third story: she now lives at the-coast. A supersession proposal opens
    # (NOT a contradiction), and canon is untouched until the operator resolves it.
    report = apply_delta(db, _home_delta("s3", "the-coast"), embedder=FakeEmbedder())
    assert len(report.adjudications_opened) == 1
    item = db.execute(
        "SELECT item_kind FROM adjudication_queue WHERE status='open'"
    ).fetchone()
    assert item["item_kind"] == "supersession"
    assert _canon_home(db) == "the-hollow"  # canon still the old home
    assert_invariants(db)


def test_resolve_supersession_accept_moves_canon_with_lineage(db):
    for sid, place in [("s1", "the-hollow"), ("s2", "the-hollow"), ("s3", "the-coast")]:
        apply_delta(db, _home_delta(sid, place), embedder=FakeEmbedder())
    item_id = db.execute(
        "SELECT item_id FROM adjudication_queue WHERE item_kind='supersession'"
    ).fetchone()["item_id"]

    resolve_supersession(db, item_id, "accept_proposed")

    assert _canon_home(db) == "the-coast"  # new home is canon
    old = db.execute(
        "SELECT f.status FROM facts f JOIN entities e ON e.entity_id=f.object_entity_id"
        " WHERE f.subject_entity_id='ent_wren' AND f.predicate='lives_in'"
        " AND e.slug='the-hollow'"
    ).fetchone()
    assert old["status"] == "deprecated"  # old home is history
    resolved = db.execute(
        "SELECT status, payload_json FROM adjudication_queue WHERE item_id=?", (item_id,)
    ).fetchone()
    assert resolved["status"] == "resolved"
    res = json.loads(resolved["payload_json"])["resolution"]
    assert res["decision"] == "accept_proposed" and res["superseded_fact_id"]
    assert_invariants(db)


def test_resolve_supersession_keep_existing_dismisses(db):
    for sid, place in [("s1", "the-hollow"), ("s2", "the-hollow"), ("s3", "the-coast")]:
        apply_delta(db, _home_delta(sid, place), embedder=FakeEmbedder())
    item_id = db.execute(
        "SELECT item_id FROM adjudication_queue WHERE item_kind='supersession'"
    ).fetchone()["item_id"]

    resolve_supersession(db, item_id, "keep_existing")
    assert _canon_home(db) == "the-hollow"  # canon unchanged
    assert db.execute(
        "SELECT status FROM adjudication_queue WHERE item_id=?", (item_id,)
    ).fetchone()["status"] == "dismissed"
    assert_invariants(db)


def test_permanent_predicate_still_opens_contradiction(db):
    """profession (single + permanent) keeps opening a plain contradiction -- the
    fork is on persistence, not cardinality."""
    def prof(story_id, value):
        return LoreDelta(
            story_id=story_id, story_title=story_id, story_summary="s",
            entities=[{"slug": "wren", "display_name": "Wren", "kind": "character",
                       "aliases": [], "summary": "s", "confidence": 0.9,
                       "evidence_excerpt": "e"}],
            claims=[ClaimInput(subject_slug="wren", predicate="profession",
                               object_literal=value, confidence=0.95, evidence_excerpt="e")],
            chunks=[],
        )
    apply_delta(db, prof("p1", "herbalist"), embedder=FakeEmbedder())
    apply_delta(db, prof("p2", "herbalist"), embedder=FakeEmbedder())  # -> canonical
    apply_delta(db, prof("p3", "baker"), embedder=FakeEmbedder())      # contradiction
    item = db.execute(
        "SELECT item_kind FROM adjudication_queue WHERE status='open'"
    ).fetchone()
    assert item["item_kind"] == "claim"
    assert_invariants(db)
