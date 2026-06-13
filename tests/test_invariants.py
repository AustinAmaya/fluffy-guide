"""§5.2 invariants, asserted after every kind of operation."""
import sqlite3

import pytest
from conftest import ingest_fixture, load_fixture_delta
from invariant_checks import assert_invariants

from lore_stack.compiler import compile_context
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta, deprecate_entity, manual_edit_fact


def _all_counts(conn):
    tables = ["sources", "story_runs", "entities", "entity_aliases", "story_entities",
              "claims", "facts", "lore_chunks", "chunk_embeddings", "adjudication_queue",
              "compiler_runs"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def test_invariants_hold_after_every_fixture(db):
    for n in (1, 2, 3, 4, 5, 6):
        ingest_fixture(db, n)
        assert_invariants(db)


def test_invariant_5_idempotent_reapply_all_fixtures(db):
    for n in (1, 2, 3, 4, 5, 6):
        ingest_fixture(db, n)
    before = _all_counts(db)
    for n in (1, 2, 3, 4, 5, 6):
        report = ingest_fixture(db, n)
        assert report.noop
    assert _all_counts(db) == before
    assert_invariants(db)


def test_invariant_3_contradiction_never_overwrites(db):
    for n in (1, 2, 5):
        ingest_fixture(db, n)
    canon = db.execute(
        "SELECT object_literal FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession' AND status='canonical'"
    ).fetchall()
    assert [r[0] for r in canon] == ["clockmaker"]
    assert db.execute(
        "SELECT COUNT(*) FROM adjudication_queue WHERE status='open'"
    ).fetchone()[0] == 1
    assert_invariants(db)


def test_invariant_4_motif_never_promoted(db):
    ingest_fixture(db, 1)
    ingest_fixture(db, 6)
    # A second story repeats the motif: it corroborates the motif fact but must
    # never canonize it.
    repeat = load_fixture_delta(6).model_copy(update={"story_id": "story_boxwell_06b"})
    apply_delta(db, repeat, embedder=FakeEmbedder())
    rows = db.execute(
        "SELECT status FROM facts WHERE predicate='claimed_title'"
    ).fetchall()
    assert {r[0] for r in rows} == {"motif"}
    assert_invariants(db)


def test_invariant_6_check_constraints_fail_loudly(db):
    ingest_fixture(db, 1)
    before = _all_counts(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO facts (fact_id, subject_entity_id, predicate, object_literal,"
            " confidence, status, source_claim_id, created_at, updated_at)"
            " VALUES ('fct_bad', 'ent_boxwell', 'p', 'v', 0.5, 'not-a-status',"
            " (SELECT claim_id FROM claims LIMIT 1), 'now', 'now')"
        )
    db.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        # Canonical fact with no provenance at all.
        db.execute(
            "INSERT INTO facts (fact_id, subject_entity_id, predicate, object_literal,"
            " confidence, status, created_at, updated_at)"
            " VALUES ('fct_bad2', 'ent_boxwell', 'p', 'v', 0.5, 'canonical', 'now', 'now')"
        )
    db.rollback()
    assert _all_counts(db) == before
    assert_invariants(db)


def test_invariant_7_budget_never_exceeded_even_tiny(db_seeded):
    from lore_stack.writeback.engine import token_estimate

    for budget in (40, 60, 165, 1700):
        result = compile_context(
            db_seeded, "Tell another story with Boxwell", embedder=FakeEmbedder(),
            total_budget=budget,
        )
        assert result.total_tokens <= budget
        # The bound holds for the emitted text itself, not just the accounting.
        assert token_estimate(result.text) <= budget
    assert result.dropped or budget == 1700
    assert_invariants(db_seeded)


def test_invariant_8_deprecation_cascades_softly(db_seeded):
    db = db_seeded
    deprecate_entity(db, "ent_boxwell")

    ent = db.execute("SELECT status FROM entities WHERE entity_id='ent_boxwell'").fetchone()
    assert ent["status"] == "deprecated"
    live_facts = db.execute(
        "SELECT COUNT(*) FROM facts WHERE (subject_entity_id='ent_boxwell'"
        " OR object_entity_id='ent_boxwell') AND status != 'deprecated'"
    ).fetchone()[0]
    assert live_facts == 0
    live_chunks = db.execute(
        "SELECT COUNT(*) FROM lore_chunks WHERE entity_id='ent_boxwell'"
        " AND status != 'deprecated'"
    ).fetchone()[0]
    assert live_chunks == 0

    # Rows survive (soft delete), embeddings still attached to their chunks.
    assert db.execute(
        "SELECT COUNT(*) FROM facts WHERE subject_entity_id='ent_boxwell'"
    ).fetchone()[0] > 0
    orphan_embeddings = db.execute(
        "SELECT COUNT(*) FROM chunk_embeddings ce LEFT JOIN lore_chunks c USING (chunk_id)"
        " WHERE c.chunk_id IS NULL"
    ).fetchone()[0]
    assert orphan_embeddings == 0

    # Retrieval ignores the deprecated entity's own lore entirely. (Chunks owned
    # by other entities, e.g. Mirel's relationship note, may still mention him.)
    result = compile_context(db, "Tell another story with Boxwell", embedder=FakeEmbedder())
    assert "ent_boxwell" not in result.targets
    assert "Boxwell is a quiet travelling clockmaker" not in result.text
    for s in result.selected:
        owner = db.execute(
            "SELECT entity_id FROM lore_chunks WHERE chunk_id=?", (s["chunk_id"],)
        ).fetchone()[0]
        assert owner != "ent_boxwell"
    assert_invariants(db)


def test_manual_edit_provenance_invariant(db_seeded):
    fact_id = manual_edit_fact(
        db_seeded, entity_id="ent_boxwell", predicate="profession",
        object_literal="horologist",
    )
    fact = db_seeded.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
    assert fact["status"] == "canonical"
    assert fact["manual_source_id"] is not None
    assert_invariants(db_seeded)
