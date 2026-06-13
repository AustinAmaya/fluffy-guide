"""Test A: one command builds the full schema from empty."""
from importlib import resources

from invariant_checks import assert_invariants

from lore_stack.cli import main
from lore_stack.db import connect, init_db
from lore_stack.db.migrations import applied_versions

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

    from lore_stack.db.migrations import MIGRATIONS

    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == [v for v, _ in MIGRATIONS]  # all migrations applied, in order
    assert versions[0] == "0001_initial"

    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_sources_checksum" in indexes

    # The predicate registry exists and was seeded.
    assert "predicates" in tables
    assert conn.execute("SELECT COUNT(*) FROM predicates").fetchone()[0] > 0
    assert conn.execute(
        "SELECT cardinality FROM predicates WHERE predicate_id='profession'"
    ).fetchone()[0] == "single"

    # Re-running is a no-op, not an error.
    assert main(["init-db", "--db", str(db_path)]) == 0


def test_migration_upgrades_a_seeded_v1_db(tmp_path):
    """A database stamped only at 0001, with data already present (written by the
    old code, so none of the later columns), upgrades through the latest migration
    without losing rows, and gains the seeded registry."""
    db_path = tmp_path / "legacy.db"
    conn = connect(db_path)

    # Build a 0001-only database by hand: apply the first migration, then seed
    # v1-shaped rows directly (the old engine wrote none of the post-0001 columns).
    schema = resources.files("lore_stack.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.executescript(
        """
        INSERT INTO schema_migrations (version, applied_at) VALUES ('0001_initial', 'then');
        INSERT INTO sources (source_id, source_kind, uri, checksum, created_at)
          VALUES ('src_legacy', 'manual', NULL, NULL, 'then');
        INSERT INTO entities (entity_id, kind, slug, display_name, status, summary,
          description, canonical_confidence, created_from_story_id, created_at, updated_at)
          VALUES ('ent_legacy', 'character', 'legacy', 'Legacy', 'canonical', 's', NULL,
                  1.0, NULL, 'then', 'then');
        INSERT INTO facts (fact_id, subject_entity_id, predicate, object_entity_id,
          object_literal, confidence, status, first_supported_story_id,
          last_supported_story_id, source_claim_id, manual_source_id, created_at, updated_at)
          VALUES ('fct_legacy', 'ent_legacy', 'profession', NULL, 'clockmaker', 1.0,
                  'canonical', NULL, NULL, NULL, 'src_legacy', 'then', 'then');
        INSERT INTO lore_chunks (chunk_id, scope, entity_id, story_id, title, body,
          activation_keys_json, retrieval_mode, insertion_lane, group_key, priority,
          token_estimate, status, created_at, updated_at)
          VALUES ('chk_legacy', 'entity', 'ent_legacy', NULL, 'Legacy card', 'A legacy chunk.',
                  '[]', 'hybrid', 'character_card', NULL, 100, 5, 'canonical', 'then', 'then');
        """
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    assert "predicates" not in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    # Now run the migration runner: it applies the later migrations and seeds.
    from lore_stack.db.migrations import MIGRATIONS

    all_versions = [v for v, _ in MIGRATIONS]
    applied = init_db(conn)
    assert applied == all_versions[1:]  # everything after 0001
    assert applied_versions(conn) == all_versions
    # Pre-existing data survived; registry is now present.
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM predicates").fetchone()[0] > 0
    # The 0006 columns were added without disturbing the legacy chunk.
    chunk = conn.execute(
        "SELECT stale, derived_from_fact_ids FROM lore_chunks WHERE chunk_id='chk_legacy'"
    ).fetchone()
    assert chunk["stale"] == 0 and chunk["derived_from_fact_ids"] is None
    assert_invariants(conn)
