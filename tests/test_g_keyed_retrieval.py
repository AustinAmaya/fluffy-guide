"""Test G: keyed retrieval compiles a bounded Boxwell context with card + open hook."""
import json

from lore_stack.compiler import compile_context
from lore_stack.seams.embedder import FakeEmbedder


def test_compile_boxwell_query(db_seeded):
    db = db_seeded
    result = compile_context(db, "Tell another story with Boxwell", embedder=FakeEmbedder())

    assert "ent_boxwell" in result.targets
    assert "Boxwell is a quiet travelling clockmaker" in result.text
    assert "[CHARACTER CARD]" in result.text
    assert "[OPEN HOOKS]" in result.text
    assert "escapement spring" in result.text
    assert result.total_tokens <= result.budget_tokens

    run = db.execute("SELECT * FROM compiler_runs").fetchone()
    assert run is not None
    assert run["target_entity_id"] == "ent_boxwell"
    assert run["compiled_context_text"] == result.text
    selected = json.loads(run["selected_chunk_ids_json"])
    assert selected == [s["chunk_id"] for s in result.selected]
    assert db.execute("SELECT COUNT(*) FROM compiler_runs").fetchone()[0] == 1
