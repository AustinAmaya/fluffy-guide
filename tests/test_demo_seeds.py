"""The committed demo-lore seeds (examples/lores/) must keep ingesting cleanly to
their expected sizes, so demo.ps1 keeps producing browsable 10- and 20-node lores."""
import json
from pathlib import Path

import pytest
from invariant_checks import assert_invariants

from lore_stack.models.delta import LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta

LORES = Path(__file__).parents[1] / "examples" / "lores"


def _ingest_lore(conn, lore_name: str):
    for delta_file in sorted((LORES / lore_name).glob("*.delta.json")):
        delta = LoreDelta.model_validate(json.loads(delta_file.read_text(encoding="utf-8")))
        apply_delta(conn, delta, embedder=FakeEmbedder())


@pytest.mark.parametrize("lore_name,expected_entities", [
    ("harrow-hollow", 10),
    ("clockwork-coast", 20),
])
def test_seed_lore_loads_to_expected_size(db, lore_name, expected_entities):
    _ingest_lore(db, lore_name)
    active = db.execute(
        "SELECT COUNT(*) FROM entities WHERE status != 'deprecated'"
    ).fetchone()[0]
    assert active == expected_entities

    # Boxwell and Mirel persist across the extended worlds.
    slugs = {r["slug"] for r in db.execute("SELECT slug FROM entities")}
    assert {"boxwell", "mirel"} <= slugs

    # The world is connected: relationship facts (entity objects) produce edges.
    edges = db.execute(
        "SELECT COUNT(*) FROM facts WHERE object_entity_id IS NOT NULL"
        " AND status != 'deprecated'"
    ).fetchone()[0]
    assert edges >= 4
    assert_invariants(db)


def test_winnie_the_pooh_seed_loads(db):
    """The hand-authored chapter-1 Pooh lore loads cleanly with its full cast."""
    _ingest_lore(db, "winnie-the-pooh")
    active = db.execute(
        "SELECT COUNT(*) FROM entities WHERE status != 'deprecated'"
    ).fetchone()[0]
    assert active == 11

    slugs = {r["slug"] for r in db.execute("SELECT slug FROM entities")}
    assert {"winnie-the-pooh", "christopher-robin", "the-honey",
            "the-great-oak-tree", "piglet"} <= slugs

    # Pooh's many aliases resolved to one entity (no fork).
    assert db.execute("SELECT COUNT(*) FROM entities WHERE slug='winnie-the-pooh'").fetchone()[0] == 1
    aliases = {r["normalized_alias"] for r in db.execute(
        "SELECT normalized_alias FROM entity_aliases a JOIN entities e USING (entity_id)"
        " WHERE e.slug='winnie-the-pooh'")}
    assert "edward bear" in aliases and "sanders" in aliases

    # Connected world: relationship edges exist (friend_of, resides_in, etc.).
    edges = db.execute(
        "SELECT COUNT(*) FROM facts WHERE object_entity_id IS NOT NULL"
        " AND status != 'deprecated'"
    ).fetchone()[0]
    assert edges >= 6
    assert_invariants(db)


def test_seed_lores_share_only_the_recurring_cast():
    """The two seed worlds overlap only on recurring characters: Boxwell and
    Mirel throughout, plus Tobias (the apprentice who follows Boxwell from the
    hollow to the coast). Lores are isolated DBs, so this is authoring intent,
    not a functional constraint."""
    hh = {d["slug"] for f in sorted((LORES / "harrow-hollow").glob("*.delta.json"))
          for d in json.loads(Path(f).read_text(encoding="utf-8"))["entities"]}
    cc = {d["slug"] for f in sorted((LORES / "clockwork-coast").glob("*.delta.json"))
          for d in json.loads(Path(f).read_text(encoding="utf-8"))["entities"]}
    assert len(hh) == 10 and len(cc) == 20
    assert hh & cc == {"boxwell", "mirel", "tobias"}
