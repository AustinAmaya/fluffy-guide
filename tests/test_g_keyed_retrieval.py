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
    _assert_audit_matches(db, run, result)


def test_multi_entity_query_represents_every_target(db_seeded):
    """A query naming several entities must surface each of them, synthesizing
    identity cards from facts when extraction never authored a card chunk."""
    db = db_seeded
    result = compile_context(
        db, "tell me a story about boxwell and mirel at whitmoor", embedder=FakeEmbedder()
    )
    assert {"ent_boxwell", "ent_mirel", "ent_whitmoor"} <= set(result.targets)

    # Mirel (character, no authored card) gets a fact-synthesized CHARACTER CARD entry.
    card_section = result.text.split("[WORLD INFO]")[0]
    assert "Mirel" in card_section
    assert "trusts Boxwell [unconfirmed]" in result.text
    # Whitmoor (location, no authored chunk at all) appears in WORLD INFO.
    assert "Whitmoor" in result.text
    synth = [s for s in result.selected if "synthesized_from_facts" in s["reasons"]]
    assert {s["chunk_id"] for s in synth} == {"factcard_ent_mirel", "factcard_ent_whitmoor"}
    assert result.total_tokens <= result.budget_tokens

    # Determinism holds with synthesized cards in play.
    again = compile_context(
        db, "tell me a story about boxwell and mirel at whitmoor", embedder=FakeEmbedder()
    )
    assert again.text.encode("utf-8") == result.text.encode("utf-8")


def _assert_audit_matches(db, run, result):
    assert run is not None
    assert run["target_entity_id"] == "ent_boxwell"
    assert run["compiled_context_text"] == result.text
    selected = json.loads(run["selected_chunk_ids_json"])
    assert selected == [s["chunk_id"] for s in result.selected]
    assert db.execute("SELECT COUNT(*) FROM compiler_runs").fetchone()[0] == 1
