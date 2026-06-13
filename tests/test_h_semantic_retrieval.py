"""Test H: semantic retrieval recovers Boxwell from 'the travelling clockmaker'
with no exact name or alias hit, via the deterministic FakeEmbedder."""
from lore_stack.compiler import compile_context
from lore_stack.retrieval import resolve_query_targets, semantic_search
from lore_stack.seams.embedder import FakeEmbedder

QUERY = "Tell another story about the travelling clockmaker"


def test_query_has_no_name_or_alias_hit(db_seeded):
    # Guard: the premise of this test is that nothing resolves by name.
    assert resolve_query_targets(db_seeded, QUERY) == []


def test_cosine_alone_recovers_boxwell_card(db_seeded):
    embedder = FakeEmbedder()
    qvec = embedder.embed([QUERY])[0]
    results = semantic_search(db_seeded, qvec, model=embedder.model_name)
    assert results, "semantic search returned nothing"
    top_chunk = db_seeded.execute(
        "SELECT entity_id, title FROM lore_chunks WHERE chunk_id=?", (results[0][0],)
    ).fetchone()
    assert top_chunk["entity_id"] == "ent_boxwell"
    assert top_chunk["title"] == "Boxwell card"
    assert results[0][1] > 0.15


def test_compiled_context_includes_boxwell(db_seeded):
    result = compile_context(db_seeded, QUERY, embedder=FakeEmbedder())
    assert "Boxwell is a quiet travelling clockmaker" in result.text
    assert db_seeded.execute("SELECT COUNT(*) FROM compiler_runs").fetchone()[0] == 1
