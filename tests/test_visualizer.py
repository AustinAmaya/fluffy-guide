"""Visualizer API tests: reads, authoritative writes (the two carve-outs), and
rejection of writes that would violate non-carve-out invariants."""
import pytest
from conftest import ingest_fixture
from invariant_checks import assert_invariants

from lore_stack.db import connect, init_db
from lore_stack.visualizer.app import create_app
from lore_stack.writeback import manual_edit_fact


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
        e["subject_entity_id"] == "ent_mirel" and e["predicate"] == "friends_with"
        and e["object_entity_id"] == "ent_boxwell"
        for e in graph["edges"]
    )
    assert all("story_count" in e for e in graph["entities"])  # drives the strength slider

    facts = client.get("/api/facts?entity=ent_boxwell").get_json()
    profession = next(f for f in facts if f["predicate"] == "profession")
    assert profession["provenance"]["kind"] == "extracted"
    assert profession["provenance"]["story_title"]

    conflicts = client.get("/api/conflicts").get_json()
    assert len(conflicts) == 1
    assert conflicts[0]["payload"]["proposed_object_literal"] == "baker"
    # the review enrichment makes the issue decidable: named subject, focus ids,
    # and two sides where the proposed side carries its source-story evidence.
    review = conflicts[0]["review"]
    assert review["subject"]["name"] == "Boxwell"
    assert "ent_boxwell" in review["focus_entity_ids"]
    assert len(review["sides"]) == 2
    proposed = next(s for s in review["sides"] if s["decision"] == "accept_proposed")
    assert proposed["value"] == "baker"
    assert proposed["snippet"] and proposed["snippet"]["evidence_excerpt"]

    motifs = client.get("/api/motifs").get_json()
    assert [m["object_literal"] for m in motifs] == ["Mayor of the Mantelpiece"]

    cands = client.get("/api/retrieval?q=Tell another story with Boxwell").get_json()
    assert any("exact_name" in c["reasons"] for c in cands)


def test_stale_chunks_endpoint(client_db):
    client, _ = client_db
    # The seeded set has no fact-linked chunks, so nothing is stale.
    assert client.get("/api/stale-chunks").get_json() == []
    # Confirming an unknown chunk is a clean 400, not a crash.
    assert client.post("/api/chunk/chk_nope/confirm").status_code == 400


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
        "SELECT fact_id FROM facts WHERE subject_entity_id='ent_mirel' AND predicate='friends_with'"
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


def test_contradiction_resolves_via_api(client_db):
    """The open conflict from the seeded baker story (stories 1,2,5 ingested in
    the client_db fixture) resolves through the API: accept_proposed flips canon."""
    client, conn = client_db
    conflicts = client.get("/api/conflicts").get_json()
    contradictions = [c for c in conflicts if c["payload"].get("kind") != "merge_suggestion"]
    assert len(contradictions) == 1
    item_id = contradictions[0]["item_id"]

    resp = client.post(f"/api/conflicts/{item_id}/resolve", json={"decision": "accept_proposed"})
    assert resp.status_code == 200
    facts = client.get("/api/facts?entity=ent_boxwell").get_json()
    canon = {f["object_literal"] for f in facts
             if f["predicate"] == "profession" and f["status"] == "canonical"}
    assert canon == {"baker"}  # proposed value accepted
    # Conflict no longer open.
    assert not [c for c in client.get("/api/conflicts").get_json()
                if c["payload"].get("kind") != "merge_suggestion"]
    assert client.post("/api/conflicts/adj_nope/resolve",
                       json={"decision": "keep_existing"}).status_code == 400


def test_merge_suggestion_surfaces_and_resolves_via_api(client_db):
    import json

    from lore_stack.models.delta import ClaimInput, LoreDelta
    from lore_stack.seams.embedder import FakeEmbedder
    from lore_stack.writeback import apply_delta

    client, conn = client_db

    def carry(sid, obj):
        return LoreDelta(
            story_id=sid, story_title="t", story_summary="s",
            entities=[{"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
                       "aliases": [], "summary": "s", "confidence": 0.9, "evidence_excerpt": "e"}],
            claims=[ClaimInput(subject_slug="boxwell", predicate="carries",
                               object_literal=obj, confidence=0.9, evidence_excerpt="e")],
            chunks=[])
    apply_delta(conn, carry("mc1", "cedar tool case"), embedder=FakeEmbedder())
    apply_delta(conn, carry("mc2", "a cedar case of tools"), embedder=FakeEmbedder())

    conflicts = client.get("/api/conflicts").get_json()
    merges = [c for c in conflicts if c["payload"].get("kind") == "merge_suggestion"]
    assert len(merges) == 1
    item = merges[0]
    keep = item["payload"]["fact_a"]

    resp = client.post(f"/api/merge/{item['item_id']}/resolve", json={"keep": keep})
    assert resp.status_code == 200
    # The other value is now deprecated history; the kept one survives.
    drop = item["payload"]["fact_b"]
    assert conn.execute("SELECT status FROM facts WHERE fact_id=?", (drop,)).fetchone()[0] == "deprecated"
    # Resolved suggestion no longer appears as open.
    assert not [c for c in client.get("/api/conflicts").get_json()
                if c["payload"].get("kind") == "merge_suggestion"]
    assert client.post("/api/merge/mrg_nope/resolve", json={"keep": "x"}).status_code == 400


def test_home_mode_staging_inbox(home_client):
    from conftest import load_fixture_delta
    from lore_stack import staging
    from lore_stack.db import connect

    client, home = home_client
    client.post("/api/lores", json={"name": "production"})

    # Stage a proposal directly into the lore's staging table.
    conn = connect(home / "production.db")
    sid = staging.stage(conn, load_fixture_delta(1))
    conn.close()

    inbox = client.get("/api/staged?lore=production").get_json()
    assert len(inbox) == 1 and inbox[0]["staging_id"] == sid
    assert inbox[0]["counts"]["entities"] == 2

    detail = client.get(f"/api/staged/{sid}?lore=production").get_json()
    assert detail["delta"]["entities"][0]["slug"] == "boxwell"

    # Apply only Boxwell + his profession claim.
    resp = client.post(f"/api/staged/{sid}/apply?lore=production",
                       json={"selection": {"entities": [0], "claims": [0], "chunks": []}})
    assert resp.status_code == 200
    ents = {e["slug"] for e in client.get("/api/entities?lore=production").get_json()}
    assert ents == {"boxwell"}
    # Inbox now empty; re-applying is rejected.
    assert client.get("/api/staged?lore=production").get_json() == []
    assert client.post(f"/api/staged/{sid}/apply?lore=production").status_code == 400
    assert client.get("/api/staged/nope?lore=production").status_code == 404


def test_home_mode_snapshots_and_rollback(home_client):
    from lore_stack.db import connect

    client, home = home_client
    client.post("/api/lores", json={"name": "production"})

    # Seed two stories through a snapshot-enabled connection, then a bad edit.
    conn = connect(home / "production.db", auto_snapshot=True)
    ingest_fixture(conn, 1)
    ingest_fixture(conn, 2)
    manual_edit_fact(conn, entity_id="ent_boxwell", predicate="profession",
                     object_literal="baker")
    conn.close()

    snaps = client.get("/api/snapshots?lore=production").get_json()
    assert snaps, "expected snapshots from the seeded mutations"
    # Newest snapshot precedes the bad edit.
    target = snaps[0]["seq"]
    assert snaps[0]["operation"].startswith("edit ent_boxwell")

    # Preview a snapshot read-only: it shows the PRIOR state without mutating
    # the live lore. The snapshot before the bad edit still has clockmaker canon
    # and no baker fact; the live lore still has the baker edit applied.
    preview = client.get(f"/api/snapshots/{target}/preview?lore=production").get_json()
    preview_profs = {
        f["object_literal"] for e in preview["entities"] for f in e["facts"]
        if f["predicate"] == "profession"
    }
    assert "clockmaker" in preview_profs and "baker" not in preview_profs
    # Preview did not touch the live lore: it still has the baker edit applied.
    live_facts = client.get("/api/facts?entity=ent_boxwell&lore=production").get_json()
    live_baker = [f for f in live_facts
                  if f["predicate"] == "profession" and f["object_literal"] == "baker"
                  and f["status"] == "canonical"]
    assert len(live_baker) == 1
    assert client.get("/api/snapshots/9999/preview?lore=production").status_code == 404

    resp = client.post(f"/api/snapshots/{target}/rollback?lore=production")
    assert resp.status_code == 200

    facts = client.get("/api/facts?entity=ent_boxwell&lore=production").get_json()
    canon = {f["object_literal"] for f in facts
             if f["predicate"] == "profession" and f["status"] == "canonical"}
    assert canon == {"clockmaker"}  # the baker edit was undone

    assert client.post("/api/snapshots/9999/rollback?lore=production").status_code == 404


def test_copy_lore_is_independent(home_client):
    from lore_stack.db import connect

    client, home = home_client
    client.post("/api/lores", json={"name": "source"})
    conn = connect(home / "source.db")
    ingest_fixture(conn, 1)  # Boxwell + the Brambled Inn
    conn.close()

    # Copy via the API.
    resp = client.post("/api/lores", json={"name": "source-copy", "copy_from": "source"})
    assert resp.status_code == 200 and resp.get_json()["copied_from"] == "source"

    # The copy has the same entities...
    src_ents = {e["slug"] for e in client.get("/api/entities?lore=source").get_json()}
    copy_ents = {e["slug"] for e in client.get("/api/entities?lore=source-copy").get_json()}
    assert copy_ents == src_ents and "boxwell" in copy_ents

    # ...but is independent: editing the copy does not change the source.
    client.post("/api/entity/ent_boxwell/edit?lore=source-copy",
                json={"predicate": "profession", "value": "horologist"})
    src_facts = client.get("/api/facts?entity=ent_boxwell&lore=source").get_json()
    assert not any(f["object_literal"] == "horologist" for f in src_facts)

    # Copy errors: unknown source, existing destination.
    assert client.post("/api/lores", json={"name": "x", "copy_from": "nope"}).status_code == 400
    assert client.post("/api/lores",
                       json={"name": "source", "copy_from": "source-copy"}).status_code == 409


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
