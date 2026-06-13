"""Test F: a contradicting story never overwrites canon; it opens adjudication."""
import json

from conftest import ingest_fixture


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
