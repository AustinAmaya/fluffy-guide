"""Test C: a second corroborating story promotes facts (and the entity) to canonical."""


def test_corroboration_promotes_profession(db_after_c):
    db = db_after_c
    assert db.execute("SELECT COUNT(*) FROM entities WHERE slug='boxwell'").fetchone()[0] == 1

    fact = db.execute(
        "SELECT * FROM facts WHERE subject_entity_id='ent_boxwell' AND predicate='profession'"
        " AND status='canonical'"
    ).fetchone()
    assert fact is not None
    assert fact["object_literal"] == "clockmaker"
    assert fact["first_supported_story_id"] == "story_boxwell_01"
    assert fact["last_supported_story_id"] == "story_boxwell_02"
    assert fact["source_claim_id"] is not None

    carries = db.execute(
        "SELECT * FROM facts WHERE subject_entity_id='ent_boxwell' AND predicate='carries'"
    ).fetchone()
    assert carries["status"] == "canonical"
    assert carries["confidence"] == 0.93

    ent = db.execute("SELECT status FROM entities WHERE slug='boxwell'").fetchone()
    assert ent["status"] == "canonical"


def test_single_story_facts_stay_soft(db_after_c):
    # 'visits the-brambled-inn' was only asserted by story 01: still soft.
    visits = db_after_c.execute(
        "SELECT status FROM facts WHERE subject_entity_id='ent_boxwell' AND predicate='visits'"
    ).fetchone()
    assert visits["status"] == "soft"
