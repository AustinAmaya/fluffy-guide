"""Review-before-commit staging: nothing is written until approval; partial
approval applies exactly the selected subset; discard writes nothing; the
reviewed path drops the confidence gate."""
import pytest
from conftest import load_fixture_delta, story_path
from invariant_checks import assert_invariants

from lore_stack import staging
from lore_stack.models.delta import ClaimInput, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta


def _counts(conn):
    tables = ["entities", "claims", "facts", "lore_chunks", "story_runs"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def test_staging_writes_nothing_until_applied(db):
    delta = load_fixture_delta(1)
    sid = staging.stage(db, delta, story_text="...")
    assert sid == "stg_000001"
    # Lore is untouched; the proposal is pending.
    assert _counts(db) == {t: 0 for t in _counts(db)}
    pending = staging.list_staged(db)
    assert len(pending) == 1
    assert pending[0]["staging_id"] == sid
    assert pending[0]["counts"]["entities"] == 2

    report = staging.apply_staged(db, sid, embedder=FakeEmbedder())
    assert not report.noop
    assert db.execute("SELECT COUNT(*) FROM entities WHERE slug='boxwell'").fetchone()[0] == 1
    # The stage is now resolved, not pending.
    assert staging.list_staged(db) == []
    assert staging.list_staged(db, status="applied")[0]["staging_id"] == sid
    assert_invariants(db)


def test_partial_approval_applies_only_selected(db):
    delta = load_fixture_delta(1)  # 2 entities (boxwell, the-brambled-inn), 3 claims, 4 chunks
    sid = staging.stage(db, delta)
    # Keep only Boxwell (entity 0) and the first claim (profession=clockmaker).
    staging.apply_staged(db, sid, selection={"entities": [0], "claims": [0], "chunks": []},
                         embedder=FakeEmbedder())

    slugs = {r["slug"] for r in db.execute("SELECT slug FROM entities")}
    assert slugs == {"boxwell"}  # the inn was dropped
    assert db.execute("SELECT COUNT(*) FROM lore_chunks").fetchone()[0] == 0
    preds = {r["predicate"] for r in db.execute("SELECT predicate FROM claims")}
    assert preds == {"profession"}  # only the kept claim
    assert_invariants(db)


def test_discard_writes_nothing(db):
    delta = load_fixture_delta(1)
    sid = staging.stage(db, delta)
    staging.discard_staged(db, sid)
    assert _counts(db) == {t: 0 for t in _counts(db)}
    assert staging.list_staged(db) == []
    assert staging.list_staged(db, status="discarded")[0]["staging_id"] == sid
    # Re-resolving a resolved stage is rejected.
    with pytest.raises(staging.StagingError):
        staging.apply_staged(db, sid, embedder=FakeEmbedder())


def test_apply_unknown_stage_raises(db):
    with pytest.raises(staging.StagingError):
        staging.apply_staged(db, "stg_999999", embedder=FakeEmbedder())


def test_reviewed_path_keeps_low_confidence_claims(db):
    """A claim below the legacy 0.7 floor is dropped by direct ingest but kept
    when a human approves it (reviewed path)."""
    low = LoreDelta(
        story_id="s_low", story_title="t", story_summary="s",
        entities=[{"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
                   "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"}],
        claims=[ClaimInput(subject_slug="boxwell", predicate="species",
                           object_literal="human", confidence=0.4, evidence_excerpt="e")],
        chunks=[],
    )
    # Direct ingest: confidence 0.4 < 0.7 -> no soft fact.
    apply_delta(db, low, embedder=FakeEmbedder())
    assert db.execute("SELECT COUNT(*) FROM facts WHERE predicate='species'").fetchone()[0] == 0

    # Reviewed (staged + approved): the human's approval overrides the floor.
    sid = staging.stage(db, low.model_copy(update={"story_id": "s_low2"}))
    staging.apply_staged(db, sid, embedder=FakeEmbedder())
    assert db.execute(
        "SELECT status FROM facts WHERE predicate='species'"
    ).fetchone()["status"] == "soft"
    assert_invariants(db)


def test_reviewed_path_promotes_on_count_not_confidence(db):
    """Two distinct stories corroborate a modest-confidence claim into canon via
    the reviewed path -- promotion is count-based, not confidence-gated."""
    def modest(story_id):
        return LoreDelta(
            story_id=story_id, story_title="t", story_summary="s",
            entities=[{"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
                       "aliases": [], "summary": "s", "confidence": 0.9,
                       "evidence_excerpt": "e"}],
            claims=[ClaimInput(subject_slug="boxwell", predicate="profession",
                               object_literal="clockmaker", confidence=0.6,
                               evidence_excerpt="e")],
            chunks=[],
        )
    for sid_story in ("m1", "m2"):
        sid = staging.stage(db, modest(sid_story))
        staging.apply_staged(db, sid, embedder=FakeEmbedder())
    # Confidence never reached 0.9, but two stories corroborate -> canonical.
    assert db.execute(
        "SELECT status FROM facts WHERE predicate='profession'"
    ).fetchone()["status"] == "canonical"
    assert_invariants(db)


def test_staged_all_items_parity_with_direct_ingest(db, tmp_path):
    """Applying a full staged delta (all items) reaches the same entity/claim/
    chunk structure as direct ingest of the same delta -- the fixtures are all
    high-confidence, so fact statuses match too."""
    from lore_stack.db import connect, init_db

    delta = load_fixture_delta(1)
    text = story_path(1).read_text(encoding="utf-8")

    # Path A: direct ingest.
    a = connect(tmp_path / "a.db")
    init_db(a)
    apply_delta(a, delta, story_text=text, embedder=FakeEmbedder())

    # Path B: stage then apply-all (reviewed).
    sid = staging.stage(db, delta, story_text=text)
    staging.apply_staged(db, sid, embedder=FakeEmbedder())

    for table in ["entities", "facts", "lore_chunks", "claims"]:
        ca = a.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        cb = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert ca == cb, f"{table}: direct={ca} staged={cb}"
    a.close()
