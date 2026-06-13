"""Test F: a contradicting story never overwrites canon; it opens adjudication."""
import json

import pytest
from conftest import ingest_fixture
from invariant_checks import assert_invariants


def test_contradiction_opens_adjudication_and_keeps_canon(db_after_c):
    db = db_after_c
    report = ingest_fixture(db, 5)
    assert len(report.adjudications_opened) == 1

    item = db.execute("SELECT * FROM adjudication_queue WHERE status='open'").fetchone()
    assert item is not None
    payload = json.loads(item["payload_json"])
    assert payload["proposed_object_literal"] == "baker"
    assert payload["predicate"] == "profession"

    # Canon unchanged: profession is still clockmaker, and no baker fact exists.
    canon = db.execute(
        "SELECT object_literal FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession' AND status='canonical'"
    ).fetchall()
    assert [r[0] for r in canon] == ["clockmaker"]
    assert db.execute(
        "SELECT COUNT(*) FROM facts WHERE object_literal='baker'"
    ).fetchone()[0] == 0

    claim = db.execute(
        "SELECT canon_state FROM claims WHERE story_id='story_boxwell_05' AND predicate='profession'"
    ).fetchone()
    assert claim["canon_state"] == "needs_review"


def test_resolve_contradiction_keep_existing(db_after_c):
    """keep_existing dismisses the conflict and leaves canon untouched."""
    from lore_stack.writeback import resolve_contradiction

    db = db_after_c
    report = ingest_fixture(db, 5)  # baker contradicts canonical clockmaker
    item_id = report.adjudications_opened[0]

    resolve_contradiction(db, item_id, "keep_existing")
    assert db.execute(
        "SELECT status FROM adjudication_queue WHERE item_id=?", (item_id,)
    ).fetchone()["status"] == "dismissed"
    canon = [r[0] for r in db.execute(
        "SELECT object_literal FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession' AND status='canonical'")]
    assert canon == ["clockmaker"]  # unchanged


def test_resolve_contradiction_accept_proposed(db_after_c):
    """accept_proposed deprecates the old canonical value and makes the proposed
    value canonical (operator authority), then resolves the item."""
    from lore_stack.writeback import resolve_contradiction

    db = db_after_c
    report = ingest_fixture(db, 5)
    item_id = report.adjudications_opened[0]

    resolve_contradiction(db, item_id, "accept_proposed")
    assert db.execute(
        "SELECT status FROM adjudication_queue WHERE item_id=?", (item_id,)
    ).fetchone()["status"] == "resolved"
    canon = [r[0] for r in db.execute(
        "SELECT object_literal FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession' AND status='canonical'")]
    assert canon == ["baker"]  # proposed value now canon
    # The old clockmaker value survives as deprecated history.
    assert db.execute(
        "SELECT status FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession' AND object_literal='clockmaker'"
    ).fetchone()["status"] == "deprecated"
    assert_invariants(db)


def test_resolve_contradiction_rejects_bad_inputs(db_after_c):
    from lore_stack.writeback import WritebackError, resolve_contradiction

    db = db_after_c
    report = ingest_fixture(db, 5)
    item_id = report.adjudications_opened[0]
    with pytest.raises(WritebackError):
        resolve_contradiction(db, item_id, "nonsense")
    with pytest.raises(WritebackError):
        resolve_contradiction(db, "adj_nope", "keep_existing")
    resolve_contradiction(db, item_id, "keep_existing")
    with pytest.raises(WritebackError):  # already resolved
        resolve_contradiction(db, item_id, "accept_proposed")


def test_motif_is_stored_but_never_canonized(db_after_c):
    db = db_after_c
    ingest_fixture(db, 6)
    motif = db.execute(
        "SELECT * FROM facts WHERE subject_entity_id='ent_boxwell' AND predicate='claimed_title'"
    ).fetchone()
    assert motif is not None
    assert motif["status"] == "motif"
    assert db.execute(
        "SELECT COUNT(*) FROM facts WHERE predicate='claimed_title' AND status='canonical'"
    ).fetchone()[0] == 0
