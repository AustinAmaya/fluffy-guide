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


def test_export_subgraph(client_db):
    client, _ = client_db
    full = client.get("/api/export").get_json()
    assert any(e["slug"] == "boxwell" for e in full["entities"])
    scoped = client.get("/api/export?entity=mirel").get_json()
    slugs = {e["slug"] for e in scoped["entities"]}
    assert "mirel" in slugs and "boxwell" in slugs  # 1-hop neighbor included
    assert client.get("/api/export?entity=nope").status_code == 400
