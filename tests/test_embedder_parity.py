"""Live embedder parity: the OpenAI adapter yields unit vectors that round-trip
through writeback and retrieval, coexisting with the fake via the `model` column.

Marker `model` -> excluded from the deterministic gate. Skipped without the openai
SDK or OPENAI_API_KEY, exactly like the live-extractor parity test.
"""
import os

import pytest

pytestmark = pytest.mark.model

openai = pytest.importorskip("openai")

requires_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set"
)


@requires_key
def test_live_embedder_vectors_are_unit_and_dimensioned():
    from lore_stack_adapters.openai_embedder import OpenAIEmbedder

    emb = OpenAIEmbedder()
    vecs = emb.embed(["the clockmaker mends the tide clock", "a quiet inn at dusk"])
    assert len(vecs) == 2
    for v in vecs:
        assert len(v) == emb.dimensions
        assert abs(sum(x * x for x in v) ** 0.5 - 1.0) < 1e-5  # L2-normalized


@requires_key
def test_live_embedder_round_trips_through_apply_and_compile(db):
    from conftest import ingest_fixture

    from lore_stack.compiler import compile_context
    from lore_stack_adapters.openai_embedder import OpenAIEmbedder

    emb = OpenAIEmbedder()
    ingest_fixture(db, 1, embedder=emb)  # chunks embedded with the live model

    # Stored under the live model name with its dimension (not the fake's).
    row = db.execute("SELECT DISTINCT model, dimensions FROM chunk_embeddings").fetchone()
    assert row["model"] == emb.model_name
    assert row["dimensions"] == emb.dimensions

    # Retrieval with the same embedder surfaces Boxwell and stays within budget.
    result = compile_context(db, "Tell another story with Boxwell", embedder=emb)
    assert "Boxwell" in result.text
    assert result.total_tokens <= result.budget_tokens
