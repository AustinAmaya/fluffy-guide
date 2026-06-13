"""Test D: an alias-only story resolves to the existing entity; aliases never fork."""
from conftest import ingest_fixture


def test_alias_only_story_does_not_fork_entity(db_after_c):
    db = db_after_c
    report = ingest_fixture(db, 3)

    assert db.execute("SELECT COUNT(*) FROM entities WHERE slug='boxwell'").fetchone()[0] == 1
    # No new entity was created for "the clockmaker".
    assert db.execute(
        "SELECT COUNT(*) FROM entities WHERE slug LIKE '%clockmaker%'"
    ).fetchone()[0] == 0
    assert "ent_boxwell" in report.entities_resolved

    alias = db.execute(
        "SELECT * FROM entity_aliases WHERE normalized_alias='the old clockmaker'"
    ).fetchone()
    assert alias is not None
    assert alias["entity_id"] == "ent_boxwell"

    # The story links to the resolved entity, and its claim lands on ent_boxwell.
    link = db.execute(
        "SELECT * FROM story_entities WHERE story_id='story_boxwell_03' AND entity_id='ent_boxwell'"
    ).fetchone()
    assert link is not None
    claim = db.execute(
        "SELECT subject_entity_id FROM claims WHERE story_id='story_boxwell_03' AND predicate='visits'"
    ).fetchone()
    assert claim["subject_entity_id"] == "ent_boxwell"
