"""Test A: one command builds the full schema from empty."""
from lore_stack.cli import main
from lore_stack.db import connect

EXPECTED_TABLES = {
    "schema_migrations", "sources", "story_runs", "entities", "entity_aliases",
    "story_entities", "claims", "facts", "lore_chunks", "chunk_embeddings",
    "compiler_runs", "adjudication_queue",
}
EXPECTED_TRIGGERS = {"lore_chunks_ai", "lore_chunks_ad", "lore_chunks_au"}


def test_init_db_cli_builds_schema(tmp_path, capsys):
    db_path = tmp_path / "lore.db"
    assert main(["init-db", "--db", str(db_path)]) == 0
    conn = connect(db_path)

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= tables
    assert "lore_chunks_fts" in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master")
    }

    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert EXPECTED_TRIGGERS <= triggers

    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == ["0001_initial"]

    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_sources_checksum" in indexes

    # Re-running is a no-op, not an error.
    assert main(["init-db", "--db", str(db_path)]) == 0
