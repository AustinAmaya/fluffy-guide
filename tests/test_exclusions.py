"""Operator-configured entity exclusions (migration 0007). Entities a consumer
owns outside the lore (e.g. a storyteller's protagonists) are dropped at writeback
-- together with claims that reference them and chunks bound to them -- and matching
is robust to the extractor's slug spelling (ent-bear / Bear / bear all map to bear)."""
from lore_stack.cli import main
from lore_stack.db import connect, init_db
from lore_stack.models.delta import LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta
from lore_stack.writeback.engine import exclusion_key


def _delta():
    return LoreDelta.model_validate({
        "story_id": "s1", "story_title": "T", "story_summary": "sum",
        "entities": [
            {"slug": "ent-bear", "display_name": "Bear", "kind": "character", "aliases": [],
             "summary": "the cub", "confidence": 0.9, "evidence_excerpt": "Bear"},
            {"slug": "papa", "display_name": "Papa", "kind": "character", "aliases": ["Papa Bear"],
             "summary": "the dad", "confidence": 0.9, "evidence_excerpt": "Papa"},
            {"slug": "sentinel", "display_name": "Sentinel", "kind": "character", "aliases": [],
             "summary": "a creeper", "confidence": 0.9, "evidence_excerpt": "Sentinel"},
        ],
        "claims": [
            {"subject_slug": "ent-bear", "predicate": "friends_with", "object_slug": "sentinel",
             "confidence": 0.9, "evidence_excerpt": "x"},          # subject excluded
            {"subject_slug": "sentinel", "predicate": "friends_with", "object_slug": "papa",
             "confidence": 0.9, "evidence_excerpt": "x"},          # object excluded
            {"subject_slug": "sentinel", "predicate": "species", "object_literal": "creeper",
             "confidence": 0.9, "evidence_excerpt": "x"},          # survives
        ],
        "chunks": [
            {"title": "Bear card", "body": "About Bear.", "activation_keys": ["bear"],
             "insertion_lane": "character_card", "entity_slug": "ent-bear"},   # excluded
            {"title": "Sentinel card", "body": "About Sentinel.", "activation_keys": ["sentinel"],
             "insertion_lane": "character_card", "entity_slug": "sentinel"},   # survives
        ],
    })


def test_exclusion_key_normalizes_slug_spelling():
    assert exclusion_key("ent-bear") == "bear"
    assert exclusion_key("Bear") == "bear"
    assert exclusion_key("  PAPA ") == "papa"
    assert exclusion_key("ent_papa") == "papa"


def test_excluded_entities_claims_and_chunks_dropped(tmp_path):
    conn = connect(tmp_path / "lore.db")
    init_db(conn)
    conn.execute("INSERT INTO entity_exclusions (name, label, created_at)"
                 " VALUES ('bear','Bear','t'),('papa','Papa','t')")
    conn.commit()

    report = apply_delta(conn, _delta(), embedder=FakeEmbedder())

    assert {r[0] for r in conn.execute("SELECT slug FROM entities")} == {"sentinel"}
    assert sorted(report.entities_excluded) == ["ent-bear", "papa"]
    # both bear/papa-touching claims dropped; only sentinel's species survives
    assert report.claims_excluded == 2
    assert {r[0] for r in conn.execute("SELECT predicate FROM claims")} == {"species"}
    # the Bear character card dropped; Sentinel's kept
    assert {r[0] for r in conn.execute("SELECT title FROM lore_chunks")} == {"Sentinel card"}
    assert report.chunks_excluded == 1


def test_no_exclusions_writes_everything(tmp_path):
    conn = connect(tmp_path / "lore.db")
    init_db(conn)
    report = apply_delta(conn, _delta(), embedder=FakeEmbedder())
    assert {r[0] for r in conn.execute("SELECT slug FROM entities")} == {"ent-bear", "papa", "sentinel"}
    assert report.entities_excluded == []
    assert report.claims_excluded == 0 and report.chunks_excluded == 0


def test_exclude_cli_add_list_remove(tmp_path):
    db = str(tmp_path / "lore.db")
    assert main(["init-db", "--db", db]) == 0
    assert main(["exclude", "add", "Bear", "Papa", "--db", db]) == 0
    conn = connect(db)
    assert {r[0] for r in conn.execute("SELECT name FROM entity_exclusions")} == {"bear", "papa"}
    assert main(["exclude", "remove", "ent-papa", "--db", db]) == 0   # spelling-robust
    assert {r[0] for r in conn.execute("SELECT name FROM entity_exclusions")} == {"bear"}
