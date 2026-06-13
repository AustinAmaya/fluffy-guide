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


def test_query_does_not_pull_unrequested_identity_cards(db_seeded):
    """Regression: 'mirel visiting harrow fen' must not surface Boxwell's card.
    Boxwell is one graph hop from Mirel, but graph proximity must never drag an
    unrequested entity's identity card into the briefing. His relationship chunk
    (which legitimately mentions him) may still appear."""
    db = db_seeded
    result = compile_context(
        db, "tell a story about mirel visiting harrow fen", embedder=FakeEmbedder()
    )
    assert set(result.targets) == {"ent_mirel", "ent_harrow-fen"}
    assert "ent_boxwell" not in result.targets

    card_titles = [s["title"] for s in result.selected if s["lane"] == "character_card"]
    assert "Boxwell card" not in card_titles
    assert "Boxwell is a quiet travelling clockmaker" not in result.text
    # The two requested entities are represented.
    assert any("Mirel" in t for t in card_titles)
    assert "Harrow Fen" in result.text
    # Mirel's relationship chunk survives even though its body names Boxwell.
    assert "Mirel and Boxwell" in [s["title"] for s in result.selected]

    # No selected chunk leaked in on graph proximity alone in the identity lane.
    for s in result.selected:
        if s["lane"] == "character_card":
            assert s["reasons"] != ["graph_1hop"]


def test_stopword_only_overlap_is_not_a_keyword_hit(db_seeded):
    """A chunk sharing only function words ('a', 'about') with the query must not
    earn an FTS hit."""
    from lore_stack.retrieval import gather_candidates

    cands = {c.chunk_id: c for c in gather_candidates(
        db_seeded, "tell a story about mirel", embedder=FakeEmbedder())}
    boxwell_card = db_seeded.execute(
        "SELECT chunk_id FROM lore_chunks WHERE title='Boxwell card'"
    ).fetchone()[0]
    # Boxwell's card body contains "a"/"and" but none of the content words; it
    # must not be a candidate at all for a query that doesn't name or neighbour-
    # qualify it through a non-identity lane.
    assert boxwell_card not in cands


def _assert_audit_matches(db, run, result):
    assert run is not None
    assert run["target_entity_id"] == "ent_boxwell"
    assert run["compiled_context_text"] == result.text
    selected = json.loads(run["selected_chunk_ids_json"])
    assert selected == [s["chunk_id"] for s in result.selected]
    assert db.execute("SELECT COUNT(*) FROM compiler_runs").fetchone()[0] == 1
