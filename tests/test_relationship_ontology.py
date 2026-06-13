"""The closed relationship set (the 11 child-legible edges).

Relationships (entity-object claims, range='entity') are a closed, fixed
vocabulary: an off-vocabulary edge is rejected at writeback, an operator edit
cannot mint a new edge type, aliases normalize to the canonical id, and the
re-authored seeds carry the right edge directions. Attributes (range='text')
stay an open vocabulary -- that half is covered by test_registry.py.
"""
import json
from pathlib import Path

import pytest
from conftest import ingest_fixture
from invariant_checks import assert_invariants

from lore_stack.models.delta import ClaimInput, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import WritebackError, apply_delta, manual_edit_fact

LORES = Path(__file__).parents[1] / "examples" / "lores"


def _two_entity_delta(story_id, claims):
    return LoreDelta(
        story_id=story_id, story_title=story_id, story_summary="s",
        entities=[
            {"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
             "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"},
            {"slug": "mirel", "display_name": "Mirel", "kind": "character",
             "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"},
        ],
        claims=claims, chunks=[],
    )


def test_unknown_relationship_predicate_is_rejected_rest_applies(db):
    """An entity-object claim whose predicate is off the closed set is rejected
    (no fact), while the rest of the delta -- the entities and a valid attribute
    claim -- still applies."""
    report = apply_delta(db, _two_entity_delta("s1", [
        ClaimInput(subject_slug="mirel", predicate="enchants", object_slug="boxwell",
                   confidence=0.95, evidence_excerpt="e"),
        ClaimInput(subject_slug="boxwell", predicate="profession",
                   object_literal="clockmaker", confidence=0.95, evidence_excerpt="e"),
    ]), embedder=FakeEmbedder())

    # The relationship claim was rejected: stored 'rejected', formed no fact.
    assert len(report.claims_rejected) == 1
    rej = db.execute(
        "SELECT canon_state, predicate FROM claims WHERE canon_state='rejected'"
    ).fetchone()
    assert rej["predicate"] == "enchants"
    assert db.execute("SELECT COUNT(*) FROM facts WHERE predicate='enchants'").fetchone()[0] == 0
    # The rest of the delta applied: both entities exist; the attribute is a soft fact.
    assert {r["slug"] for r in db.execute("SELECT slug FROM entities")} == {"boxwell", "mirel"}
    assert db.execute(
        "SELECT status FROM facts WHERE predicate='profession'"
    ).fetchone()["status"] == "soft"
    assert_invariants(db)


def test_attribute_predicate_with_entity_object_is_rejected(db):
    """A text-attribute predicate misused with an entity object (profession -> an
    entity, not a literal) is a range mismatch: rejected, no fact."""
    report = apply_delta(db, _two_entity_delta("s1", [
        ClaimInput(subject_slug="boxwell", predicate="profession", object_slug="mirel",
                   confidence=0.95, evidence_excerpt="e"),
    ]), embedder=FakeEmbedder())
    assert len(report.claims_rejected) == 1
    assert db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    assert_invariants(db)


def test_relationship_alias_normalizes_to_canonical(db):
    """'resents' normalizes to the canonical 'against' edge and 'trusts' to
    'friends_with' -- neither is rejected, and both land under the canonical id."""
    apply_delta(db, _two_entity_delta("s1", [
        ClaimInput(subject_slug="mirel", predicate="resents", object_slug="boxwell",
                   confidence=0.95, evidence_excerpt="e"),
        ClaimInput(subject_slug="boxwell", predicate="trusts", object_slug="mirel",
                   confidence=0.95, evidence_excerpt="e"),
    ]), embedder=FakeEmbedder())
    preds = {r["predicate"] for r in db.execute(
        "SELECT predicate FROM facts WHERE object_entity_id IS NOT NULL")}
    assert preds == {"against", "friends_with"}
    assert db.execute(
        "SELECT COUNT(*) FROM claims WHERE canon_state='rejected'"
    ).fetchone()[0] == 0
    assert_invariants(db)


def test_relationship_manual_edit_requires_registered_predicate(db):
    """An operator edit cannot mint a new relationship edge type, but may use a
    registered one."""
    ingest_fixture(db, 1)  # Boxwell + the Brambled Inn
    with pytest.raises(WritebackError):
        manual_edit_fact(db, entity_id="ent_boxwell", predicate="enchants",
                         object_entity_id="ent_the-brambled-inn")
    fact_id = manual_edit_fact(db, entity_id="ent_boxwell", predicate="visits",
                               object_entity_id="ent_the-brambled-inn")
    row = db.execute("SELECT predicate, status FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
    assert row["predicate"] == "visits" and row["status"] == "canonical"
    assert_invariants(db)


def test_conformed_seed_mentors_points_teacher_to_student(db):
    """The re-authored harrow-hollow seed carries 'old-gregor mentors boxwell'
    (teacher -> student), and no legacy edge spellings survive in facts."""
    for f in sorted((LORES / "harrow-hollow").glob("*.delta.json")):
        apply_delta(db, LoreDelta.model_validate(json.loads(f.read_text(encoding="utf-8"))),
                    embedder=FakeEmbedder())
    mentors = db.execute(
        "SELECT subject_entity_id, object_entity_id FROM facts WHERE predicate='mentors'"
    ).fetchone()
    assert mentors["subject_entity_id"] == "ent_old-gregor"
    assert mentors["object_entity_id"] == "ent_boxwell"
    legacy = db.execute(
        "SELECT COUNT(*) FROM facts WHERE predicate IN"
        " ('taught_by', 'keeps', 'trusts', 'resides_in', 'apprentices_to', 'located_in')"
    ).fetchone()[0]
    assert legacy == 0
    assert_invariants(db)
