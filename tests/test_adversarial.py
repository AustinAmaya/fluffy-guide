"""Adversarial inputs: malformed, oversized, contradictory, hostile. Graceful
failure, no partial writes, ever."""
import json

import pytest
from conftest import ADVERSARIAL, ingest_fixture, load_fixture_delta
from invariant_checks import assert_invariants
from pydantic import ValidationError

from lore_stack.cli import main
from lore_stack.models.delta import ClaimInput, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import WritebackError, apply_delta


def _counts(conn):
    tables = ["sources", "story_runs", "entities", "entity_aliases", "story_entities",
              "claims", "facts", "lore_chunks", "adjudication_queue"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


@pytest.mark.parametrize("fixture", ["malformed.json", "missing_fields.json", "unknown_fields.json"])
def test_bad_delta_files_fail_cleanly_via_cli(tmp_path, fixture, capsys):
    db_path = tmp_path / "lore.db"
    assert main(["init-db", "--db", str(db_path)]) == 0
    capsys.readouterr()

    rc = main(["ingest-delta", "--db", str(db_path), "--file", str(ADVERSARIAL / fixture)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid delta" in captured.err

    from lore_stack.db import connect
    conn = connect(db_path)
    assert _counts(conn) == {t: 0 for t in _counts(conn)}
    conn.close()


def test_oversized_delta_rejected_at_validation():
    claims = [
        {
            "subject_slug": "boxwell",
            "predicate": f"p{i}",
            "object_literal": "v",
            "confidence": 0.5,
            "evidence_excerpt": "e",
        }
        for i in range(1000)
    ]
    with pytest.raises(ValidationError):
        LoreDelta(
            story_id="story_huge", story_title="t", story_summary="s",
            entities=[], claims=claims, chunks=[],
        )


def test_claim_with_both_or_neither_object_rejected():
    base = dict(subject_slug="s", predicate="p", confidence=0.5, evidence_excerpt="e")
    with pytest.raises(ValidationError):
        ClaimInput(**base, object_slug="a", object_literal="b")
    with pytest.raises(ValidationError):
        ClaimInput(**base)


def test_duplicate_story_id_rolls_back_completely(db):
    ingest_fixture(db, 1)
    before = _counts(db)
    # Different content, same story_id: must fail with zero net writes.
    dup = load_fixture_delta(2).model_copy(update={"story_id": "story_boxwell_01"})
    with pytest.raises(WritebackError):
        apply_delta(db, dup, embedder=FakeEmbedder())
    assert _counts(db) == before
    assert_invariants(db)


def test_double_contradiction_in_one_delta_is_graceful(db_after_c):
    db = db_after_c
    contradiction = LoreDelta(
        story_id="story_hostile",
        story_title="Hostile",
        story_summary="Contradicts profession and carries at once.",
        entities=[],
        claims=[
            ClaimInput(subject_slug="boxwell", predicate="profession",
                       object_literal="baker", confidence=0.95, evidence_excerpt="e"),
            ClaimInput(subject_slug="boxwell", predicate="carries",
                       object_literal="a wicker basket", confidence=0.95, evidence_excerpt="e"),
        ],
        chunks=[],
    )
    report = apply_delta(db, contradiction, embedder=FakeEmbedder())
    # profession is single-valued: baker contradicts canonical clockmaker -> 1 adjudication.
    # carries is multi-valued: a wicker basket coexists with the cedar tool case,
    # no conflict (the registry's cardinality fix for implicit commitment C1).
    assert len(report.adjudications_opened) == 1
    assert report.facts_created == ["fct_38794998ada1"]  # the new soft 'carries' fact

    canon = {
        r["predicate"]: r["object_literal"]
        for r in db.execute(
            "SELECT predicate, object_literal FROM facts"
            " WHERE subject_entity_id='ent_boxwell' AND status='canonical'"
        )
    }
    assert canon == {"profession": "clockmaker", "carries": "cedar tool case"}

    # The wicker basket is a coexisting soft fact, not a contradiction.
    basket = db.execute(
        "SELECT status FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='carries' AND object_literal='a wicker basket'"
    ).fetchone()
    assert basket["status"] == "soft"
    assert_invariants(db)


def test_literal_shaped_like_entity_id_cannot_corroborate(db_after_c):
    """A literal object that textually equals an internal entity id must not
    corroborate (or contradict) an entity-reference fact."""
    db = db_after_c
    ingest_fixture(db, 4)  # creates soft fact: mirel --friends_with--> ent_boxwell (entity object)
    sneaky = LoreDelta(
        story_id="story_sneaky",
        story_title="Sneaky literal",
        story_summary="Asserts a literal that looks like an entity id.",
        entities=[],
        claims=[
            ClaimInput(subject_slug="mirel", predicate="friends_with",
                       object_literal="ent_boxwell", confidence=0.99, evidence_excerpt="e"),
        ],
        chunks=[],
    )
    apply_delta(db, sneaky, embedder=FakeEmbedder())
    rows = db.execute(
        "SELECT object_entity_id, object_literal, status, confidence FROM facts"
        " WHERE subject_entity_id='ent_mirel' AND predicate='friends_with' ORDER BY fact_id"
    ).fetchall()
    # Two distinct soft facts coexist; the entity-object fact was not touched.
    entity_fact = next(r for r in rows if r["object_entity_id"] == "ent_boxwell")
    literal_fact = next(r for r in rows if r["object_literal"] == "ent_boxwell")
    assert entity_fact["status"] == "soft"
    assert entity_fact["confidence"] == 0.92  # unchanged by the literal claim
    assert literal_fact["status"] == "soft"
    assert_invariants(db)


def test_sql_metacharacters_are_inert(db):
    hostile = LoreDelta(
        story_id="story_inject",
        story_title="Robert'); DROP TABLE facts;--",
        story_summary="'; DELETE FROM entities; --",
        entities=[],
        claims=[],
        chunks=[],
    )
    apply_delta(db, hostile, embedder=FakeEmbedder())
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"facts", "entities"} <= tables
    assert_invariants(db)
