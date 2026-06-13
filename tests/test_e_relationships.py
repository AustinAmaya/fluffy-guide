"""Test E: a story adds a relationship to the innkeeper Mirel."""
from conftest import ingest_fixture


def test_relationship_fact_and_chunk(db_after_c):
    db = db_after_c
    ingest_fixture(db, 4)

    mirel = db.execute("SELECT * FROM entities WHERE slug='mirel'").fetchone()
    assert mirel is not None

    rel = db.execute(
        "SELECT * FROM facts WHERE subject_entity_id='ent_mirel' AND predicate='trusts'"
    ).fetchone()
    assert rel is not None
    assert rel["object_entity_id"] == "ent_boxwell"
    assert rel["status"] == "soft"

    chunk = db.execute(
        "SELECT * FROM lore_chunks WHERE insertion_lane='relationships'"
    ).fetchone()
    assert chunk is not None
    assert chunk["entity_id"] == "ent_mirel"
