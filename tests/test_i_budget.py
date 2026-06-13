"""Test I: budget enforcement — over-budget chunks are dropped whole by priority."""
from lore_stack.compiler import (
    DEFAULT_LANE_BUDGETS,
    DEFAULT_TOTAL_BUDGET,
    compile_context,
)
from lore_stack.models.delta import ChunkInput, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta
from lore_stack.writeback.engine import token_estimate


def test_default_budget_is_sized_for_a_large_context_model():
    # Raised for large-context story models; lanes sum to the total.
    assert DEFAULT_TOTAL_BUDGET == 6000
    assert sum(DEFAULT_LANE_BUDGETS.values()) == DEFAULT_TOTAL_BUDGET


def _flood_delta(n_chunks: int) -> LoreDelta:
    body_unit = "The flood plain remembers every season of rising water in long detail. "
    chunks = [
        ChunkInput(
            title=f"Flood chunk {i:03d}",
            body=(body_unit * 3) + f"Entry number {i:03d}.",
            activation_keys=["flood"],
            retrieval_mode="key",
            insertion_lane="world_info",
            priority=1000 - i,  # strictly decreasing priority
        )
        for i in range(n_chunks)
    ]
    return LoreDelta(
        story_id="story_flood",
        story_title="Flood of chunks",
        story_summary="Synthetic seed for budget enforcement.",
        entities=[],
        claims=[],
        chunks=chunks,
    )


def test_budget_drops_low_priority_chunks_whole(db):
    apply_delta(db, _flood_delta(40), embedder=FakeEmbedder())
    result = compile_context(db, "a story about the flood", embedder=FakeEmbedder())

    assert result.total_tokens <= result.budget_tokens
    lane_tokens = sum(s["token_estimate"] for s in result.selected if s["lane"] == "world_info")
    assert lane_tokens <= DEFAULT_LANE_BUDGETS["world_info"]
    assert result.dropped, "flood must overflow the lane budget"

    # Included chunks all outrank every dropped chunk (drop by priority, whole chunks).
    included = [s["title"] for s in result.selected if s["lane"] == "world_info"]
    dropped = [d["title"] for d in result.dropped]
    assert included == sorted(included)  # priority 1000-i ties to title order
    assert max(included) < min(dropped)

    # Nothing was truncated: every included body is intact in the output.
    for s in result.selected:
        row = db.execute(
            "SELECT body FROM lore_chunks WHERE chunk_id=?", (s["chunk_id"],)
        ).fetchone()
        assert row["body"].strip() in result.text


def test_token_estimator_is_documented_rule(db):
    assert token_estimate("abcd" * 10) == 10
    assert token_estimate("abc") == 1
