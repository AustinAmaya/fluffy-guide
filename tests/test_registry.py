"""Predicate registry: alias normalization, cardinality-aware conflicts, and the
unregistered-predicate gate on auto-canonization."""
from conftest import ingest_fixture
from invariant_checks import assert_invariants

from lore_stack import registry
from lore_stack.models.delta import ClaimInput, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta, manual_edit_fact


def _delta(story_id, claims):
    return LoreDelta(
        story_id=story_id, story_title=story_id, story_summary="s",
        entities=[{"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
                   "aliases": [], "summary": "s", "confidence": 0.9,
                   "evidence_excerpt": "e"}],
        claims=claims, chunks=[],
    )


def test_seed_is_idempotent_and_present(db):
    assert registry.lookup(db, "profession").cardinality == "single"
    assert registry.lookup(db, "carries").cardinality == "multi"
    added_again = registry.seed_predicates(db)
    assert added_again == 0  # nothing new on a second seed


def test_alias_normalizes_and_corroborates(db):
    """'occupation' (an alias of 'profession') asserted in story 1 and 'profession'
    in story 2 must corroborate into one canonical fact -- not two fragments."""
    apply_delta(db, _delta("s1", [ClaimInput(
        subject_slug="boxwell", predicate="occupation", object_literal="clockmaker",
        confidence=0.95, evidence_excerpt="e")]), embedder=FakeEmbedder())
    apply_delta(db, _delta("s2", [ClaimInput(
        subject_slug="boxwell", predicate="profession", object_literal="clockmaker",
        confidence=0.95, evidence_excerpt="e")]), embedder=FakeEmbedder())

    rows = db.execute(
        "SELECT predicate, status FROM facts WHERE subject_entity_id='ent_boxwell'"
    ).fetchall()
    # Exactly one fact, stored under the canonical predicate id, promoted.
    assert len(rows) == 1
    assert rows[0]["predicate"] == "profession"
    assert rows[0]["status"] == "canonical"
    # Both claims recorded the normalized predicate.
    preds = {r["predicate"] for r in db.execute("SELECT predicate FROM claims")}
    assert preds == {"profession"}
    assert_invariants(db)


def test_multi_valued_predicate_coexists(db):
    """carries is multi-valued: two different objects coexist, each promotable."""
    for i, obj in enumerate(["cedar tool case", "brass compass"]):
        apply_delta(db, _delta(f"a{i}", [ClaimInput(
            subject_slug="boxwell", predicate="carries", object_literal=obj,
            confidence=0.95, evidence_excerpt="e")]), embedder=FakeEmbedder())
    # Corroborate the compass from a second story -> it promotes independently.
    apply_delta(db, _delta("a2", [ClaimInput(
        subject_slug="boxwell", predicate="carries", object_literal="brass compass",
        confidence=0.95, evidence_excerpt="e")]), embedder=FakeEmbedder())

    facts = {r["object_literal"]: r["status"] for r in db.execute(
        "SELECT object_literal, status FROM facts WHERE predicate='carries'")}
    assert facts == {"cedar tool case": "soft", "brass compass": "canonical"}
    # No adjudication: multi-valued predicates never contradict on a new value.
    assert db.execute("SELECT COUNT(*) FROM adjudication_queue").fetchone()[0] == 0
    assert_invariants(db)


def test_unregistered_predicate_never_canonizes(db):
    """An unknown predicate may form soft facts but can never auto-promote."""
    for i in range(2):  # two distinct stories, same value, high confidence
        apply_delta(db, _delta(f"u{i}", [ClaimInput(
            subject_slug="boxwell", predicate="favourite_colour", object_literal="green",
            confidence=0.99, evidence_excerpt="e")]), embedder=FakeEmbedder())
    row = db.execute(
        "SELECT status FROM facts WHERE predicate='favourite_colour'"
    ).fetchone()
    assert row["status"] == "soft"  # corroborated but NOT canonical (unregistered)
    assert registry.lookup(db, "favourite_colour") is None
    assert_invariants(db)


def test_manual_edit_auto_registers_new_predicate(db):
    ingest_fixture(db, 1)
    assert registry.lookup(db, "hometown") is None
    manual_edit_fact(db, entity_id="ent_boxwell", predicate="hometown",
                     object_literal="Harrowgate")
    info = registry.lookup(db, "hometown")
    assert info is not None and info.registered_by == "operator"
    # The canonical manual fact satisfies the registered-predicate invariant.
    assert_invariants(db)


def test_manual_edit_normalizes_known_alias(db):
    ingest_fixture(db, 1)
    # 'occupation' is an alias of 'profession'; the manual fact lands on 'profession'.
    manual_edit_fact(db, entity_id="ent_boxwell", predicate="occupation",
                     object_literal="horologist")
    canon = db.execute(
        "SELECT predicate FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND object_literal='horologist' AND status='canonical'"
    ).fetchone()
    assert canon["predicate"] == "profession"
    assert_invariants(db)
