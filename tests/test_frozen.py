"""Frozen baselines + full reset: a frozen lore can be played with and hard-reset
to its pristine state — restoring BOTH the lore content and its snapshot history,
discarding everything since the freeze."""
import pytest
from conftest import ingest_fixture

from lore_stack import frozen, snapshots
from lore_stack.db import connect, init_db
from lore_stack.lores import LoreError
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import manual_edit_fact


def _make_lore(home, name):
    home.mkdir(parents=True, exist_ok=True)
    conn = connect(home / f"{name}.db", auto_snapshot=True)
    init_db(conn)
    return conn


def test_freeze_then_mutate_then_reset_restores_db_and_history(tmp_path):
    home = tmp_path / "lores"
    conn = _make_lore(home, "world")
    ingest_fixture(conn, 1)
    ingest_fixture(conn, 2)  # Boxwell canon; this also produced snapshots
    conn.close()

    frozen.freeze(home, "world")
    assert frozen.has_frozen(home, "world")
    frozen_snap_count = len(snapshots.list_snapshots(home / "world.db"))
    frozen_entities = _entity_count(home / "world.db")

    # Play with the lore: an edit (more snapshots) and a brand-new entity.
    conn = connect(home / "world.db", auto_snapshot=True)
    manual_edit_fact(conn, entity_id="ent_boxwell", predicate="profession",
                     object_literal="balloonist")
    ingest_fixture(conn, 4)  # adds Mirel etc.
    conn.close()
    assert _entity_count(home / "world.db") > frozen_entities
    assert len(snapshots.list_snapshots(home / "world.db")) > frozen_snap_count

    # Full reset: content AND history revert to the frozen baseline.
    frozen.reset(home, "world")
    conn = connect(home / "world.db")
    profs = {r[0] for r in conn.execute(
        "SELECT object_literal FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND predicate='profession' AND status='canonical'")}
    assert profs == {"clockmaker"}  # the balloonist edit is gone
    assert "ent_mirel" not in {r[0] for r in conn.execute("SELECT entity_id FROM entities")}
    conn.close()
    assert _entity_count(home / "world.db") == frozen_entities
    # History reverted too: the post-freeze snapshots are gone.
    assert len(snapshots.list_snapshots(home / "world.db")) == frozen_snap_count


def test_reset_without_baseline_raises(tmp_path):
    home = tmp_path / "lores"
    _make_lore(home, "world").close()
    assert not frozen.has_frozen(home, "world")
    with pytest.raises(LoreError):
        frozen.reset(home, "world")


def test_refreeze_updates_the_baseline(tmp_path):
    home = tmp_path / "lores"
    conn = _make_lore(home, "world")
    ingest_fixture(conn, 1)
    conn.close()
    frozen.freeze(home, "world")

    conn = connect(home / "world.db", auto_snapshot=True)
    ingest_fixture(conn, 2)
    conn.close()
    frozen.freeze(home, "world")  # re-freeze captures the larger state

    # Mutate further, then reset -> back to the SECOND freeze (two stories).
    conn = connect(home / "world.db", auto_snapshot=True)
    ingest_fixture(conn, 4)
    conn.close()
    frozen.reset(home, "world")
    conn = connect(home / "world.db")
    stories = conn.execute("SELECT COUNT(*) FROM story_runs").fetchone()[0]
    conn.close()
    assert stories == 2


def _entity_count(db_path) -> int:
    conn = connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    conn.close()
    return n
