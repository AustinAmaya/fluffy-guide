"""Golden-file + byte-determinism tests for the compiler."""
from pathlib import Path

from conftest import GOLDEN, ingest_fixture

from lore_stack.compiler import compile_context
from lore_stack.db import connect, init_db
from lore_stack.seams.embedder import FakeEmbedder

GOLDEN_QUERY = "Tell another story with Boxwell"
GOLDEN_FILE = GOLDEN / "golden_context_boxwell.txt"


def test_compiled_context_matches_golden(db_seeded):
    result = compile_context(db_seeded, GOLDEN_QUERY, embedder=FakeEmbedder())
    expected = GOLDEN_FILE.read_bytes().decode("utf-8")
    assert result.text == expected


def test_compile_is_byte_identical_across_fresh_dbs(tmp_path):
    texts = []
    for name in ("a", "b"):
        conn = connect(tmp_path / f"{name}.db")
        init_db(conn)
        for n in (1, 2, 3, 4):
            ingest_fixture(conn, n)
        texts.append(compile_context(conn, GOLDEN_QUERY, embedder=FakeEmbedder()).text)
        conn.close()
    assert texts[0].encode("utf-8") == texts[1].encode("utf-8")


def test_recompile_on_same_db_is_byte_identical(db_seeded):
    first = compile_context(db_seeded, GOLDEN_QUERY, embedder=FakeEmbedder()).text
    second = compile_context(db_seeded, GOLDEN_QUERY, embedder=FakeEmbedder()).text
    assert first.encode("utf-8") == second.encode("utf-8")
