"""Hypothesis property tests: random valid deltas keep every invariant, re-apply
is idempotent, and foreign keys never break. Derandomized for the gate."""
import string

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from invariant_checks import assert_invariants

from lore_stack.db import connect, init_db
from lore_stack.models.delta import ChunkInput, ClaimInput, EntityUpsert, LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta

SETTINGS = settings(
    derandomize=True,
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

words = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8)
names = st.builds(lambda ws: " ".join(ws), st.lists(words, min_size=1, max_size=3))
slugs = st.builds(lambda ws: "-".join(ws), st.lists(words, min_size=1, max_size=3))
confidences = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)

entity_st = st.builds(
    EntityUpsert,
    slug=slugs,
    display_name=names,
    kind=st.sampled_from(["character", "location", "item", "concept"]),
    aliases=st.lists(names, max_size=3),
    summary=names,
    confidence=confidences,
    evidence_excerpt=names,
)

claim_st = st.builds(
    lambda subject, predicate, obj, is_entity, confidence, hint, ev: ClaimInput(
        subject_slug=subject,
        predicate=predicate,
        object_slug=obj if is_entity else None,
        object_literal=None if is_entity else obj,
        confidence=confidence,
        canonicality_hint=hint,
        evidence_excerpt=ev,
    ),
    subject=slugs,
    predicate=st.sampled_from(["species", "profession", "carries", "trusts", "visits"]),
    obj=names,
    is_entity=st.booleans(),
    confidence=confidences,
    hint=st.sampled_from(["candidate", "soft", "motif", "uncertain"]),
    ev=names,
)

chunk_st = st.builds(
    ChunkInput,
    title=names,
    body=names,
    activation_keys=st.lists(words, min_size=1, max_size=4),
    retrieval_mode=st.sampled_from(["key", "semantic", "hybrid", "pinned"]),
    insertion_lane=st.sampled_from(
        ["character_card", "world_info", "relationships", "open_hooks", "recent_continuity"]
    ),
    priority=st.integers(min_value=0, max_value=2000),
    entity_slug=st.one_of(st.none(), slugs),
)


def delta_st(story_id: str):
    return st.builds(
        LoreDelta,
        story_id=st.just(story_id),
        story_title=names,
        story_summary=names,
        entities=st.lists(entity_st, max_size=3),
        claims=st.lists(claim_st, max_size=4),
        chunks=st.lists(chunk_st, max_size=2),
        open_questions=st.lists(names, max_size=2),
    )


def _counts(conn):
    tables = ["sources", "story_runs", "entities", "entity_aliases", "story_entities",
              "claims", "facts", "lore_chunks", "chunk_embeddings", "adjudication_queue"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


@SETTINGS
@given(delta=delta_st("story_prop_1"))
def test_random_delta_keeps_invariants_and_is_idempotent(tmp_path_factory, delta):
    conn = connect(tmp_path_factory.mktemp("prop") / "lore.db")
    init_db(conn)
    try:
        apply_delta(conn, delta, embedder=FakeEmbedder())
        assert_invariants(conn)
        before = _counts(conn)
        report = apply_delta(conn, delta, embedder=FakeEmbedder())
        assert report.noop
        assert _counts(conn) == before
        assert_invariants(conn)
    finally:
        conn.close()


@SETTINGS
@given(delta_a=delta_st("story_prop_a"), delta_b=delta_st("story_prop_b"))
def test_two_random_deltas_never_break_fks_or_duplicate(tmp_path_factory, delta_a, delta_b):
    conn = connect(tmp_path_factory.mktemp("prop2") / "lore.db")
    init_db(conn)
    try:
        apply_delta(conn, delta_a, embedder=FakeEmbedder())
        apply_delta(conn, delta_b, embedder=FakeEmbedder())
        assert_invariants(conn)
    finally:
        conn.close()
