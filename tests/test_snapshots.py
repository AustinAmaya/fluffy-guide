"""Whole-file snapshots + rollback: auto-snapshot on mutations, opt-out by
default, rollback restores prior state, retention prunes, rollback is undoable."""
import pytest
from conftest import ingest_fixture, load_fixture_delta

from lore_stack import snapshots
from lore_stack.compiler import compile_context
from lore_stack.db import connect, init_db
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta, deprecate_entity, manual_edit_fact

TABLES = ["sources", "story_runs", "entities", "entity_aliases", "story_entities",
          "claims", "facts", "lore_chunks", "chunk_embeddings", "adjudication_queue"]


def _dump(conn) -> dict:
    """Order-independent snapshot of table contents for equality comparison."""
    out = {}
    for t in TABLES:
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {t}")]
        out[t] = sorted(json_safe(r) for r in rows)
    return out


def json_safe(row: dict) -> tuple:
    return tuple(sorted((k, repr(v)) for k, v in row.items()))


@pytest.fixture
def lore(tmp_path):
    path = tmp_path / "lore.db"
    conn = connect(path, auto_snapshot=True)
    init_db(conn)
    return conn, path


def test_default_connection_does_not_snapshot(tmp_path):
    path = tmp_path / "plain.db"
    conn = connect(path)  # auto_snapshot defaults off
    init_db(conn)
    ingest_fixture(conn, 1)
    assert snapshots.list_snapshots(path) == []
    assert not snapshots.snapshot_dir(path).exists()


def test_auto_snapshot_fires_on_each_mutation_kind(lore):
    conn, path = lore
    ingest_fixture(conn, 1)      # ingest
    ingest_fixture(conn, 2)      # ingest
    manual_edit_fact(conn, entity_id="ent_boxwell", predicate="hometown",
                     object_literal="Harrowgate")  # edit
    deprecate_entity(conn, "ent_the-brambled-inn")  # deprecate

    snaps = snapshots.list_snapshots(path)
    ops = [s["operation"] for s in snaps]
    # Newest first; one snapshot precedes each mutation.
    assert ops[0].startswith("deprecate ")
    assert ops[1].startswith("edit ent_boxwell")
    assert any(o.startswith("ingest story_boxwell_02") for o in ops)
    assert any(o.startswith("ingest story_boxwell_01") for o in ops)
    assert len(snaps) == 4
    # First snapshot captured the empty DB (0 stories), last captured 2 stories.
    assert snaps[-1]["counts"]["stories"] == 0
    assert snaps[0]["counts"]["stories"] == 2


def test_noop_ingest_does_not_snapshot(lore):
    conn, path = lore
    ingest_fixture(conn, 1)
    before = len(snapshots.list_snapshots(path))
    report = ingest_fixture(conn, 1)  # duplicate checksum -> no-op
    assert report.noop
    assert len(snapshots.list_snapshots(path)) == before


def test_rollback_restores_prior_state(lore):
    conn, path = lore
    ingest_fixture(conn, 1)
    ingest_fixture(conn, 2)
    good = _dump(conn)
    good_ctx = compile_context(conn, "Tell another story with Boxwell",
                               embedder=FakeEmbedder()).text

    # A destructive edit we will undo.
    manual_edit_fact(conn, entity_id="ent_boxwell", predicate="profession",
                     object_literal="baker")
    assert _dump(conn) != good

    # The snapshot taken right before the edit is seq-newest.
    target = snapshots.list_snapshots(path)[0]["seq"]
    conn.close()
    snapshots.rollback(path, target)

    conn2 = connect(path)
    assert _dump(conn2) == good
    ctx2 = compile_context(conn2, "Tell another story with Boxwell",
                           embedder=FakeEmbedder()).text
    assert ctx2 == good_ctx


def test_rollback_is_itself_undoable(lore):
    conn, path = lore
    ingest_fixture(conn, 1)
    one_story = _dump(conn)
    ingest_fixture(conn, 2)
    two_stories = _dump(conn)

    # Roll back to the pre-story-2 snapshot.
    pre_story2 = next(s["seq"] for s in snapshots.list_snapshots(path)
                      if s["operation"].startswith("ingest story_boxwell_02"))
    conn.close()
    snapshots.rollback(path, pre_story2)
    conn2 = connect(path)
    assert _dump(conn2) == one_story

    # The rollback snapshotted the two-story state first; roll forward to it.
    roll_forward = next(s["seq"] for s in snapshots.list_snapshots(path)
                        if s["operation"].startswith("rollback-to-"))
    conn2.close()
    snapshots.rollback(path, roll_forward)
    conn3 = connect(path)
    assert _dump(conn3) == two_stories


def test_retention_prunes_oldest(tmp_path):
    path = tmp_path / "lore.db"
    conn = connect(path, auto_snapshot=True)
    init_db(conn)
    # Force many snapshots with a tiny retention.
    for i in range(8):
        snapshots.create(conn, path, f"manual-{i}", retention=3)
    snaps = snapshots.list_snapshots(path)
    assert len(snaps) == 3
    # The three newest survive; their files exist, older ones are gone.
    sdir = snapshots.snapshot_dir(path)
    files = sorted(p.name for p in sdir.glob("*.db"))
    assert len(files) == 3
    assert [s["operation"] for s in snaps] == ["manual-7", "manual-6", "manual-5"]


def test_rollback_unknown_seq_raises(lore):
    conn, path = lore
    ingest_fixture(conn, 1)
    with pytest.raises(FileNotFoundError):
        snapshots.rollback(path, 9999)
