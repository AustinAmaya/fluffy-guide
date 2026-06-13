"""Visualizer API tests: reads, authoritative writes (the two carve-outs), and
rejection of writes that would violate non-carve-out invariants."""
import pytest
from conftest import ingest_fixture
from invariant_checks import assert_invariants

from lore_stack.db import connect, init_db
from lore_stack.visualizer.app import create_app


@pytest.fixture
def client_db(tmp_path):
    db_path = tmp_path / "lore.db"
    conn = connect(db_path)
    init_db(conn)
    for n in (1, 2, 4, 5, 6):  # canon + relationship + open conflict + motif
        ingest_fixture(conn, n)
    app = create_app(db_path)
    app.config["TESTING"] = True
    yield app.test_client(), conn
    conn.close()


def test_read_endpoints(client_db):
    client, _ = client_db
    assert client.get("/").status_code == 200

    entities = client.get("/api/entities").get_json()
    boxwell = next(e for e in entities if e["slug"] == "boxwell")
    assert "the clockmaker" in boxwell["aliases"]

    graph = client.get("/api/graph").get_json()
    assert any(e["slug"] == "mirel" for e in graph["entities"])
    assert any(
        e["subject_entity_id"] == "ent_mirel" and e["predicate"] == "trusts"
        and e["object_entity_id"] == "ent_boxwell"
        for e in graph["edges"]
    )

    facts = client.get("/api/facts?entity=ent_boxwell").get_json()
    profession = next(f for f in facts if f["predicate"] == "profession")
    assert profession["provenance"]["kind"] == "extracted"
    assert profession["provenance"]["story_title"]

    conflicts = client.get("/api/conflicts").get_json()
    assert len(conflicts) == 1
    assert conflicts[0]["payload"]["proposed_object_literal"] == "baker"

    motifs = client.get("/api/motifs").get_json()
    assert [m["object_literal"] for m in motifs] == ["Mayor of the Mantelpiece"]

    cands = client.get("/api/retrieval?q=Tell another story with Boxwell").get_json()
    assert any("exact_name" in c["reasons"] for c in cands)


def test_query_context_endpoint(client_db):
    client, _ = client_db
    resp = client.post("/api/query_context", json={"query": "Tell another story with Boxwell"})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "Boxwell is a quiet travelling clockmaker" in payload["text"]
    assert payload["total_tokens"] <= payload["budget_tokens"]
    assert payload["selected"]
    assert all({"chunk_id", "lane", "score", "reasons"} <= set(s) for s in payload["selected"])

    assert client.post("/api/query_context", json={}).status_code == 400


def test_manual_edit_is_canonical_and_preserves_history(client_db):
    client, conn = client_db
    resp = client.post(
        "/api/entity/ent_boxwell/edit",
        json={"predicate": "profession", "value": "horologist"},
    )
    assert resp.status_code == 200
    new_fact = resp.get_json()
    assert new_fact["status"] == "canonical"
    assert new_fact["provenance"]["kind"] == "manual"

    rows = conn.execute(
        "SELECT status, object_literal, manual_source_id FROM facts"
        " WHERE subject_entity_id='ent_boxwell' AND predicate='profession' ORDER BY created_at"
    ).fetchall()
    by_value = {r["object_literal"]: r for r in rows}
    assert by_value["clockmaker"]["status"] == "deprecated"  # prior value preserved
    assert by_value["horologist"]["status"] == "canonical"
    assert by_value["horologist"]["manual_source_id"] is not None

    # Bypasses adjudication: still exactly the one pre-existing open item.
    assert len(client.get("/api/conflicts").get_json()) == 1
    assert_invariants(conn)


def test_delete_is_soft_everywhere(client_db):
    client, conn = client_db
    fact_id = conn.execute(
        "SELECT fact_id FROM facts WHERE subject_entity_id='ent_mirel' AND predicate='trusts'"
    ).fetchone()[0]
    assert client.post(f"/api/fact/{fact_id}/deprecate").status_code == 200
    row = conn.execute("SELECT status FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
    assert row["status"] == "deprecated"  # row survives

    assert client.post("/api/entity/ent_boxwell/deprecate").status_code == 200
    assert conn.execute(
        "SELECT status FROM entities WHERE entity_id='ent_boxwell'"
    ).fetchone()[0] == "deprecated"
    assert conn.execute(
        "SELECT COUNT(*) FROM facts WHERE subject_entity_id='ent_boxwell'"
    ).fetchone()[0] > 0  # history intact
    graph = client.get("/api/graph").get_json()
    assert not any(e["entity_id"] == "ent_boxwell" for e in graph["entities"])
    assert_invariants(conn)


def test_invalid_ui_writes_are_rejected(client_db):
    client, conn = client_db
    before = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    # Unknown entity.
    resp = client.post("/api/entity/ent_nobody/edit", json={"predicate": "p", "value": "v"})
    assert resp.status_code == 400
    # Empty predicate.
    resp = client.post("/api/entity/ent_boxwell/edit", json={"predicate": "  ", "value": "v"})
    assert resp.status_code == 400
    # Both value and object_entity_id (provenance/object invariant).
    resp = client.post(
        "/api/entity/ent_boxwell/edit",
        json={"predicate": "p", "value": "v", "object_entity_id": "ent_mirel"},
    )
    assert resp.status_code == 400
    # Neither value nor object.
    resp = client.post("/api/entity/ent_boxwell/edit", json={"predicate": "p"})
    assert resp.status_code == 400
    # Unknown object entity (would orphan a relationship edge).
    resp = client.post(
        "/api/entity/ent_boxwell/edit",
        json={"predicate": "p", "object_entity_id": "ent_ghost"},
    )
    assert resp.status_code == 400
    # Unknown fact/entity deprecation.
    assert client.post("/api/fact/fct_nope/deprecate").status_code == 400
    assert client.post("/api/entity/ent_nope/deprecate").status_code == 400

    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == before
    assert_invariants(conn)


def test_restore_reverses_soft_delete_without_zombies(client_db):
    client, conn = client_db
    assert client.post("/api/entity/ent_boxwell/deprecate").status_code == 200

    # No zombie facts: editing a deprecated entity is rejected.
    resp = client.post(
        "/api/entity/ent_boxwell/edit", json={"predicate": "p", "value": "v"}
    )
    assert resp.status_code == 400
    assert "restore" in resp.get_json()["error"]

    assert client.post("/api/entity/ent_boxwell/restore").status_code == 200
    assert conn.execute(
        "SELECT status FROM entities WHERE entity_id='ent_boxwell'"
    ).fetchone()[0] == "provisional"
    # Chunks revive; facts remain history, revivable individually via manual edit.
    assert conn.execute(
        "SELECT COUNT(*) FROM lore_chunks WHERE entity_id='ent_boxwell'"
        " AND status='provisional'"
    ).fetchone()[0] > 0
    assert conn.execute(
        "SELECT COUNT(*) FROM facts WHERE subject_entity_id='ent_boxwell'"
        " AND status != 'deprecated'"
    ).fetchone()[0] == 0
    edit = client.post(
        "/api/entity/ent_boxwell/edit",
        json={"predicate": "profession", "value": "clockmaker"},
    )
    assert edit.status_code == 200
    assert edit.get_json()["status"] == "canonical"

    # Restoring a non-deprecated entity is rejected.
    assert client.post("/api/entity/ent_boxwell/restore").status_code == 400
    assert_invariants(conn)


def test_export_subgraph(client_db):
    client, _ = client_db
    full = client.get("/api/export").get_json()
    assert any(e["slug"] == "boxwell" for e in full["entities"])
    scoped = client.get("/api/export?entity=mirel").get_json()
    slugs = {e["slug"] for e in scoped["entities"]}
    assert "mirel" in slugs and "boxwell" in slugs  # 1-hop neighbor included
    assert client.get("/api/export?entity=nope").status_code == 400


def test_single_db_mode_has_no_lores_api(client_db):
    client, _ = client_db
    assert client.get("/api/lores").status_code == 404


@pytest.fixture
def home_client(tmp_path):
    from lore_stack.visualizer.app import create_app as make

    home = tmp_path / "lores"
    app = make(home=home)
    app.config["TESTING"] = True
    return app.test_client(), home


def test_home_mode_lore_lifecycle(home_client):
    client, _ = home_client
    assert client.get("/api/lores").get_json() == []

    assert client.post("/api/lores", json={"name": "production"}).status_code == 200
    assert client.post("/api/lores", json={"name": "test-alpha"}).status_code == 200
    for bad in ["", "../evil", "a b", "x" * 80, ".hidden"]:
        assert client.post("/api/lores", json={"name": bad}).status_code == 400
    assert client.post("/api/lores", json={"name": "production"}).status_code == 409

    lores = client.get("/api/lores").get_json()
    assert [l["name"] for l in lores] == ["production", "test-alpha"]
    assert all(l["entities"] == 0 and l["stories"] == 0 for l in lores)

    # Lore selection is mandatory and validated in home mode.
    assert client.get("/api/entities").status_code == 400
    assert client.get("/api/entities?lore=nope").status_code == 404
    assert client.get("/api/entities?lore=../evil").status_code == 400
    assert client.get("/api/entities?lore=production").get_json() == []


def test_home_mode_lores_are_isolated(home_client):
    from lore_stack.db import connect

    client, home = home_client
    client.post("/api/lores", json={"name": "production"})
    client.post("/api/lores", json={"name": "testing"})

    conn = connect(home / "testing.db")
    ingest_fixture(conn, 1)
    conn.close()

    testing = client.get("/api/entities?lore=testing").get_json()
    assert any(e["slug"] == "boxwell" for e in testing)
    assert client.get("/api/entities?lore=production").get_json() == []

    # Writes target only the selected lore.
    resp = client.post(
        "/api/entity/ent_boxwell/edit?lore=testing",
        json={"predicate": "profession", "value": "clockmaker"},
    )
    assert resp.status_code == 200
    assert client.get("/api/entities?lore=production").get_json() == []
    assert client.get("/api/facts?entity=ent_boxwell&lore=testing").get_json()

    counts = {l["name"]: l for l in client.get("/api/lores").get_json()}
    assert counts["testing"]["entities"] > 0
    assert counts["production"]["entities"] == 0
