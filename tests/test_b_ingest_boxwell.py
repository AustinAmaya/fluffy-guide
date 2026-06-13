"""Test B: first Boxwell ingest validates, writes back, and is idempotent."""
from conftest import ingest_fixture


def _counts(conn):
    tables = ["sources", "story_runs", "entities", "entity_aliases", "story_entities",
              "claims", "facts", "lore_chunks", "chunk_embeddings", "adjudication_queue"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def test_first_ingest_writes_boxwell(db):
    report = ingest_fixture(db, 1)
    assert not report.noop
    assert "ent_boxwell" in report.entities_created

    assert db.execute("SELECT COUNT(*) FROM story_runs").fetchone()[0] == 1
    ent = db.execute("SELECT * FROM entities WHERE slug='boxwell'").fetchone()
    assert ent is not None
    assert ent["status"] == "provisional"

    link = db.execute(
        "SELECT * FROM story_entities WHERE story_id='story_boxwell_01' AND entity_id=?",
        (ent["entity_id"],),
    ).fetchone()
    assert link is not None

    assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] > 0
    assert db.execute("SELECT COUNT(*) FROM lore_chunks").fetchone()[0] > 0
    # First-mention facts are soft, never canonical.
    statuses = {r[0] for r in db.execute("SELECT DISTINCT status FROM facts")}
    assert statuses <= {"soft"}
    # Chunks got embeddings from the FakeEmbedder.
    assert db.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0] == \
        db.execute("SELECT COUNT(*) FROM lore_chunks").fetchone()[0]


def test_reapply_same_delta_is_noop(db):
    ingest_fixture(db, 1)
    before = _counts(db)
    report = ingest_fixture(db, 1)
    assert report.noop
    assert _counts(db) == before
