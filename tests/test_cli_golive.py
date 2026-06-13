"""Phase 8 CLI surface: ingest-delta --canon (direct-to-canon), stage-delta, and
the --embedder selector. Exercised through the public main() entry point.
"""
import pytest
from conftest import STORIES

from lore_stack.cli import main
from lore_stack.db import connect

DELTA1 = str(STORIES / "boxwell_story_01.delta.json")


def _init(tmp_path):
    db = str(tmp_path / "lore.db")
    assert main(["init-db", "--db", db]) == 0
    return db


def _profession_statuses(db):
    conn = connect(db)
    out = {r[0] for r in conn.execute(
        "SELECT status FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession'")}
    conn.close()
    return out


def test_ingest_delta_canon_writes_canonical_facts(tmp_path, capsys):
    db = _init(tmp_path)
    capsys.readouterr()
    assert main(["ingest-delta", "--db", db, "--file", DELTA1, "--canon"]) == 0
    assert _profession_statuses(db) == {"canonical"}  # operator-vouched -> canon now


def test_ingest_delta_default_is_soft(tmp_path, capsys):
    db = _init(tmp_path)
    capsys.readouterr()
    assert main(["ingest-delta", "--db", db, "--file", DELTA1]) == 0
    assert _profession_statuses(db) == {"soft"}  # one story, default path -> soft


def test_stage_delta_writes_nothing_until_applied(tmp_path, capsys):
    db = _init(tmp_path)
    capsys.readouterr()
    assert main(["stage-delta", "--db", db, "--file", DELTA1]) == 0
    assert "staged" in capsys.readouterr().out
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0  # nothing landed
    conn.close()
    assert main(["stage", "list", "--db", db]) == 0
    assert "stg_" in capsys.readouterr().out


def test_embedder_openai_without_sdk_errors_cleanly(tmp_path, capsys):
    try:
        import openai  # noqa: F401
        pytest.skip("openai installed; this test covers the missing-SDK path")
    except ImportError:
        pass
    db = _init(tmp_path)
    capsys.readouterr()
    rc = main(["compile-context", "--db", db, "--query", "boxwell", "--embedder", "openai"])
    assert rc == 1  # clean non-zero exit, not a crash
    assert "openai" in capsys.readouterr().err.lower()
