"""Ollama embedder adapter + the query/document prefix wiring.

Two deterministic tests (injected stubs, no server or SDK needed) lock the prefix
behavior and the fusion fallback -- they run in the gate. One live, model-marked
test exercises a real local Ollama and skips when it isn't reachable.
"""
import pytest
from conftest import ingest_fixture
from invariant_checks import assert_invariants

from lore_stack.models.delta import ClaimInput, LoreDelta
from lore_stack.retrieval import gather_candidates


class _StubOllamaClient:
    """Stands in for ollama.Client: records the inputs it was asked to embed and
    returns a fixed 4-d vector per input (so the adapter can normalize it)."""

    def __init__(self):
        self.seen = []

    def embed(self, model, input):
        self.seen.extend(input)
        return {"embeddings": [[3.0, 0.0, 0.0, 4.0] for _ in input]}


def test_adapter_applies_doc_vs_query_prefixes_and_normalizes():
    from lore_stack_adapters.ollama_embedder import OllamaEmbedder

    stub = _StubOllamaClient()
    emb = OllamaEmbedder(client=stub)

    doc = emb.embed(["a clockmaker"])[0]
    qry = emb.embed_query(["a clockmaker"])[0]

    # The document path used search_document:, the query path used search_query:.
    assert stub.seen == ["search_document: a clockmaker", "search_query: a clockmaker"]
    # Vectors are L2-normalized (3,0,0,4 -> /5).
    assert doc == pytest.approx([0.6, 0.0, 0.0, 0.8])
    assert sum(x * x for x in qry) == pytest.approx(1.0)


def _seed_one_chunk(db, embedder):
    delta = LoreDelta(
        story_id="s1", story_title="t", story_summary="s",
        entities=[{"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
                   "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"}],
        claims=[], chunks=[{"title": "Boxwell card", "body": "A clockmaker.",
                            "activation_keys": ["boxwell"], "insertion_lane": "character_card",
                            "entity_slug": "boxwell"}],
    )
    from lore_stack.writeback import apply_delta
    apply_delta(db, delta, embedder=embedder)


def test_retrieval_uses_embed_query_when_present(db):
    """The one core change: gather_candidates calls embed_query for the query side."""
    class WithQuery:
        model_name = "stub"
        def __init__(self): self.calls = []
        def embed(self, texts): self.calls.append("embed"); return [[1.0, 0.0] for _ in texts]
        def embed_query(self, texts): self.calls.append("embed_query"); return [[1.0, 0.0] for _ in texts]

    emb = WithQuery()
    _seed_one_chunk(db, emb)            # ingest -> embed() (document side)
    emb.calls.clear()
    gather_candidates(db, "tell a story about boxwell", embedder=emb)
    assert "embed_query" in emb.calls and "embed" not in emb.calls
    assert_invariants(db)


def test_retrieval_falls_back_to_embed_without_embed_query(db):
    """A symmetric embedder (no embed_query) still works -- query uses embed()."""
    class SymOnly:
        model_name = "stub2"
        def __init__(self): self.calls = []
        def embed(self, texts): self.calls.append("embed"); return [[1.0, 0.0] for _ in texts]

    emb = SymOnly()
    _seed_one_chunk(db, emb)
    emb.calls.clear()
    gather_candidates(db, "tell a story about boxwell", embedder=emb)
    assert emb.calls == ["embed"]
    assert_invariants(db)


@pytest.mark.model
def test_live_ollama_round_trip(db):
    pytest.importorskip("ollama")
    from lore_stack.compiler import compile_context
    from lore_stack_adapters.ollama_embedder import EmbeddingError, OllamaEmbedder

    emb = OllamaEmbedder()
    try:
        vecs = emb.embed(["the clockmaker mends the tide clock"])
    except EmbeddingError:
        pytest.skip("Ollama not reachable or model not pulled")

    assert len(vecs[0]) == emb.dimensions  # 768 for nomic-embed-text
    assert abs(sum(x * x for x in vecs[0]) ** 0.5 - 1.0) < 1e-5  # L2-normalized
    # Query and document encodings differ (different task prefixes).
    assert emb.embed(["boxwell"])[0] != emb.embed_query(["boxwell"])[0]

    ingest_fixture(db, 1, embedder=emb)
    row = db.execute("SELECT DISTINCT model, dimensions FROM chunk_embeddings").fetchone()
    assert row["model"] == emb.model_name and row["dimensions"] == emb.dimensions
    result = compile_context(db, "Tell another story with Boxwell", embedder=emb)
    assert "Boxwell" in result.text
    assert_invariants(db)
