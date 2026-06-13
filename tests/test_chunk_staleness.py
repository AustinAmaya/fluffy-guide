"""Chunk staleness (commitment C7): a chunk that declares the facts it derives
from is flagged stale -- and drops out of compilation -- when any source fact is
deprecated or superseded. Chunks with no fact links are never staled.
"""
from conftest import ingest_fixture
from invariant_checks import assert_invariants

from lore_stack.compiler import compile_context
from lore_stack.models.delta import ChunkInput, ClaimInput, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta, deprecate_fact


def _delta_with_linked_chunk():
    return LoreDelta(
        story_id="s1", story_title="t", story_summary="s",
        entities=[{"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
                   "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"}],
        claims=[ClaimInput(subject_slug="boxwell", predicate="profession",
                           object_literal="clockmaker", confidence=0.95, evidence_excerpt="e")],
        chunks=[ChunkInput(
            title="Boxwell the clockmaker",
            body="Boxwell is the village clockmaker, known for patient repairs.",
            activation_keys=["boxwell", "clockmaker"],
            insertion_lane="character_card",
            entity_slug="boxwell",
            derived_from=[{"subject_slug": "boxwell", "predicate": "profession"}],
        )],
    )


def test_linked_chunk_records_its_facts_and_is_live(db):
    apply_delta(db, _delta_with_linked_chunk(), embedder=FakeEmbedder())
    row = db.execute(
        "SELECT derived_from_fact_ids, stale FROM lore_chunks"
        " WHERE title='Boxwell the clockmaker'"
    ).fetchone()
    assert row["stale"] == 0
    assert row["derived_from_fact_ids"] is not None  # linked to the profession fact
    result = compile_context(db, "Tell a story about Boxwell", embedder=FakeEmbedder())
    assert "village clockmaker" in result.text  # live: compiles in
    assert_invariants(db)


def test_deprecating_a_source_fact_stales_the_chunk_and_drops_it(db):
    apply_delta(db, _delta_with_linked_chunk(), embedder=FakeEmbedder())
    fact_id = db.execute(
        "SELECT fact_id FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession'"
    ).fetchone()["fact_id"]

    deprecate_fact(db, fact_id)

    assert db.execute(
        "SELECT stale FROM lore_chunks WHERE title='Boxwell the clockmaker'"
    ).fetchone()["stale"] == 1
    # Stale -> excluded from compilation (the authored prose no longer surfaces).
    result = compile_context(db, "Tell a story about Boxwell", embedder=FakeEmbedder())
    assert "village clockmaker" not in result.text
    assert_invariants(db)


def test_unlinked_chunk_never_stales(db):
    """Fixture 1's chunks declare no derived_from; deprecating a fact leaves them live."""
    ingest_fixture(db, 1)
    fact_id = db.execute(
        "SELECT fact_id FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession'"
    ).fetchone()["fact_id"]
    deprecate_fact(db, fact_id)
    assert db.execute("SELECT COUNT(*) FROM lore_chunks WHERE stale=1").fetchone()[0] == 0
    assert_invariants(db)


def test_confirm_fresh_clears_staleness_and_restores_to_compilation(db):
    from lore_stack.writeback import confirm_chunk_fresh

    apply_delta(db, _delta_with_linked_chunk(), embedder=FakeEmbedder())
    fact_id = db.execute(
        "SELECT fact_id FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession'"
    ).fetchone()["fact_id"]
    deprecate_fact(db, fact_id)
    chunk_id = db.execute(
        "SELECT chunk_id FROM lore_chunks WHERE title='Boxwell the clockmaker'"
    ).fetchone()["chunk_id"]
    assert db.execute(
        "SELECT stale FROM lore_chunks WHERE chunk_id=?", (chunk_id,)
    ).fetchone()["stale"] == 1

    confirm_chunk_fresh(db, chunk_id)  # operator: the prose still reads true
    assert db.execute(
        "SELECT stale FROM lore_chunks WHERE chunk_id=?", (chunk_id,)
    ).fetchone()["stale"] == 0
    result = compile_context(db, "Tell a story about Boxwell", embedder=FakeEmbedder())
    assert "village clockmaker" in result.text  # live again
    assert_invariants(db)
