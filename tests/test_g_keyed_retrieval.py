"""Test G: keyed retrieval compiles a bounded Boxwell context with card + open hook."""
import json

from lore_stack.compiler import compile_context
from lore_stack.seams.embedder import FakeEmbedder


def test_compile_boxwell_query(db_seeded):
    db = db_seeded
    result = compile_context(db, "Tell another story with Boxwell", embedder=FakeEmbedder())

    assert "ent_boxwell" in result.targets
    assert "Boxwell is a quiet travelling clockmaker" in result.text
    # Primary header names the targeted entity; lanes are secondary "##" headings.
    assert "=== CONTEXT FOR: Boxwell ===" in result.text
    assert "## Character card" in result.text
    assert "## Open hooks" in result.text
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

    # The primary header names every targeted entity.
    assert result.text.startswith("=== CONTEXT FOR: ")
    header_line = result.text.splitlines()[0]
    assert "Boxwell" in header_line and "Mirel" in header_line and "Whitmoor" in header_line
    # Mirel (character, no authored card) gets a fact-synthesized character-card entry.
    card_section = result.text.split("## World info")[0]
    assert "Mirel" in card_section
    assert "friends_with Boxwell [unconfirmed]" in result.text
    # Whitmoor (location, no authored chunk at all) appears under World info.
    assert "Whitmoor" in result.text
    synth = [s for s in result.selected if "synthesized_from_facts" in s["reasons"]]
    assert {s["chunk_id"] for s in synth} == {"factcard_ent_mirel", "factcard_ent_whitmoor"}
    assert result.total_tokens <= result.budget_tokens

    # Determinism holds with synthesized cards in play.
    again = compile_context(
        db, "tell me a story about boxwell and mirel at whitmoor", embedder=FakeEmbedder()
    )
    assert again.text.encode("utf-8") == result.text.encode("utf-8")


def test_no_entity_match_is_legible_in_a_populated_lore(db_seeded):
    """A query naming no known entity, in an existing lore, returns recent
    continuity clearly labeled as optional connective tissue (the lore is the
    unit of connection: a new character can be woven into existing threads)."""
    result = compile_context(
        db_seeded, "a tale of arthur the hedgehog on the space station",
        embedder=FakeEmbedder(),
    )
    assert result.targets == []
    assert result.text.startswith(
        "=== CONTEXT FOR: (no entities from your request are in this lore) ==="
    )
    assert "## Recent continuity (offered for optional connection)" in result.text
    assert result.total_tokens <= result.budget_tokens


def test_no_entity_match_in_an_empty_lore_returns_nothing(db):
    """Same query in a brand-new empty lore returns nothing -- a new lore is a
    clean slate with no continuity to connect to."""
    result = compile_context(
        db, "a tale of arthur the hedgehog on the space station", embedder=FakeEmbedder()
    )
    assert result.targets == []
    assert result.text == ""
    assert result.total_tokens == 0


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
