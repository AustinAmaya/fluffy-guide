"""Phase 2 parity: the live AnthropicExtractor, behind the unchanged Extractor
interface, must produce output that satisfies the same invariants and test-B
assertions as the fakes. Tagged `model`: excluded from the deterministic gate."""
import os

import pytest
from conftest import story_path
from invariant_checks import assert_invariants

from lore_stack.models.delta import LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta
from lore_stack.writeback.engine import resolve_entity

pytestmark = pytest.mark.model

anthropic = pytest.importorskip("anthropic")

requires_key = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"
)


@requires_key
def test_live_extractor_parity_with_test_b(db):
    from lore_stack_adapters.anthropic_extractor import AnthropicExtractor

    extractor = AnthropicExtractor()
    story_text = story_path(1).read_text(encoding="utf-8")

    delta = extractor.extract(story_text, story_id="story_live_01")
    # Schema-valid by construction; assert the contract explicitly anyway.
    assert isinstance(delta, LoreDelta)
    assert delta.story_id == "story_live_01"
    assert delta.entities, "live extraction found no entities"

    report = apply_delta(db, delta, story_text=story_text, embedder=FakeEmbedder())
    assert not report.noop

    # Same shape of assertions as test B, via the same writeback engine.
    boxwell = resolve_entity(db, "Boxwell")
    assert boxwell is not None, "live extraction did not establish Boxwell"
    assert db.execute(
        "SELECT status FROM entities WHERE entity_id=?", (boxwell,)
    ).fetchone()["status"] == "provisional"
    assert db.execute("SELECT COUNT(*) FROM story_runs").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] > 0
    link = db.execute(
        "SELECT 1 FROM story_entities WHERE story_id='story_live_01' AND entity_id=?",
        (boxwell,),
    ).fetchone()
    assert link is not None
    # No live claim may have written canon directly: first mention is never canonical.
    assert db.execute(
        "SELECT COUNT(*) FROM facts WHERE status='canonical'"
    ).fetchone()[0] == 0

    assert_invariants(db)
