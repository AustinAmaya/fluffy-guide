"""Direct-to-canon (operator-authoritative) ingest: items the operator names during
extraction become canonical immediately, with manual-edit authority -- the named
value wins, no corroboration or adjudication needed. The default ingest path
(authoritative=False) is unchanged.
"""
from invariant_checks import assert_invariants

from lore_stack.models.delta import ClaimInput, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta


def _delta(story_id, claims, entities=None):
    return LoreDelta(
        story_id=story_id, story_title=story_id, story_summary="s",
        entities=entities or [
            {"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
             "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"},
        ],
        claims=claims, chunks=[],
    )


def test_authoritative_ingest_canonizes_immediately(db):
    apply_delta(db, _delta("s1", [
        ClaimInput(subject_slug="boxwell", predicate="profession",
                   object_literal="clockmaker", confidence=0.6, evidence_excerpt="e"),
    ]), embedder=FakeEmbedder(), authoritative=True)

    # One story, low confidence -- but operator-vouched -> canonical right away.
    row = db.execute(
        "SELECT status, source_claim_id FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession'"
    ).fetchone()
    assert row["status"] == "canonical"
    assert row["source_claim_id"] is not None  # provenance preserved
    # The entity upserts as canonical too.
    assert db.execute(
        "SELECT status FROM entities WHERE entity_id='ent_boxwell'"
    ).fetchone()["status"] == "canonical"
    assert_invariants(db)


def test_authoritative_single_valued_named_value_wins(db):
    apply_delta(db, _delta("s1", [
        ClaimInput(subject_slug="boxwell", predicate="profession",
                   object_literal="clockmaker", confidence=0.9, evidence_excerpt="e"),
    ]), embedder=FakeEmbedder(), authoritative=True)
    # Operator authoritatively names a new value -> it wins, the old is deprecated,
    # with NO contradiction queued (operator authority, like a manual edit).
    apply_delta(db, _delta("s2", [
        ClaimInput(subject_slug="boxwell", predicate="profession",
                   object_literal="horologist", confidence=0.9, evidence_excerpt="e"),
    ]), embedder=FakeEmbedder(), authoritative=True)

    canon = {r["object_literal"]: r["status"] for r in db.execute(
        "SELECT object_literal, status FROM facts WHERE predicate='profession'")}
    assert canon["horologist"] == "canonical"
    assert canon["clockmaker"] == "deprecated"
    assert db.execute("SELECT COUNT(*) FROM adjudication_queue").fetchone()[0] == 0
    assert_invariants(db)


def test_authoritative_multi_valued_coexists_as_canon(db):
    for i, obj in enumerate(["cedar tool case", "brass compass"]):
        apply_delta(db, _delta(f"s{i}", [
            ClaimInput(subject_slug="boxwell", predicate="carries",
                       object_literal=obj, confidence=0.9, evidence_excerpt="e"),
        ]), embedder=FakeEmbedder(), authoritative=True)
    facts = {r["object_literal"]: r["status"] for r in db.execute(
        "SELECT object_literal, status FROM facts WHERE predicate='carries'")}
    assert facts == {"cedar tool case": "canonical", "brass compass": "canonical"}
    assert_invariants(db)


def test_authoritative_unregistered_attribute_registers_and_canonizes(db):
    from lore_stack import registry

    apply_delta(db, _delta("s1", [
        ClaimInput(subject_slug="boxwell", predicate="favourite_colour",
                   object_literal="green", confidence=0.5, evidence_excerpt="e"),
    ]), embedder=FakeEmbedder(), authoritative=True)
    assert db.execute(
        "SELECT status FROM facts WHERE predicate='favourite_colour'"
    ).fetchone()["status"] == "canonical"
    # Auto-registered so the A6 invariant (canonical predicate is in the vocabulary) holds.
    assert registry.lookup(db, "favourite_colour") is not None
    assert_invariants(db)


def test_default_ingest_is_unchanged_by_the_flag(db):
    apply_delta(db, _delta("s1", [
        ClaimInput(subject_slug="boxwell", predicate="profession",
                   object_literal="clockmaker", confidence=0.95, evidence_excerpt="e"),
    ]), embedder=FakeEmbedder())
    # Without authoritative, one story stays soft (corroboration still required for canon).
    assert db.execute(
        "SELECT status FROM facts WHERE predicate='profession'"
    ).fetchone()["status"] == "soft"
    assert_invariants(db)
