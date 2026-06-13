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
