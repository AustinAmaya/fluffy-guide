import json
from pathlib import Path

import pytest

from lore_stack.db import connect, init_db
from lore_stack.models.delta import LoreDelta
from lore_stack.seams.embedder import FakeEmbedder
from lore_stack.writeback import apply_delta

TESTS_DIR = Path(__file__).parent
FIXTURES = TESTS_DIR / "fixtures"
STORIES = FIXTURES / "stories"
ADVERSARIAL = FIXTURES / "adversarial"
GOLDEN = FIXTURES / "golden"


def story_path(n: int) -> Path:
    return STORIES / f"boxwell_story_{n:02d}.md"


def load_fixture_delta(n: int) -> LoreDelta:
    path = STORIES / f"boxwell_story_{n:02d}.delta.json"
    return LoreDelta.model_validate(json.loads(path.read_text(encoding="utf-8")))


def ingest_fixture(conn, n: int, embedder=None):
    delta = load_fixture_delta(n)
    story_text = story_path(n).read_text(encoding="utf-8")
    return apply_delta(
        conn,
        delta,
        story_text=story_text,
        embedder=embedder if embedder is not None else FakeEmbedder(),
    )


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "lore.db")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def db_after_c(db):
    """DB state used by tests C onward: stories 01 + 02 ingested."""
    ingest_fixture(db, 1)
    ingest_fixture(db, 2)
    return db


@pytest.fixture
def db_seeded(db):
    """Richer state: stories 01-04 ingested (used by retrieval/compiler/golden tests)."""
    for n in (1, 2, 3, 4):
        ingest_fixture(db, n)
    return db
