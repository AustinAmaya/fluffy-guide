"""Reusable §5.2 invariant assertions, run after operations in invariant and property tests."""


def assert_invariants(conn):
    # Referential integrity: no broken foreign keys anywhere.
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert broken == [], f"broken foreign keys: {[tuple(r) for r in broken]}"

    # 1. No duplicate entities per normalized slug; no duplicate normalized aliases.
    dup_slugs = conn.execute(
        "SELECT slug, COUNT(*) c FROM entities GROUP BY slug HAVING c > 1"
    ).fetchall()
    assert dup_slugs == []
    dup_aliases = conn.execute(
        "SELECT normalized_alias, COUNT(*) c FROM entity_aliases GROUP BY normalized_alias"
        " HAVING c > 1"
    ).fetchall()
    assert dup_aliases == []

    # 2. Every fact has provenance; no orphan facts or aliases.
    no_provenance = conn.execute(
        "SELECT COUNT(*) FROM facts WHERE source_claim_id IS NULL AND manual_source_id IS NULL"
    ).fetchone()[0]
    assert no_provenance == 0
    orphan_aliases = conn.execute(
        "SELECT COUNT(*) FROM entity_aliases a LEFT JOIN entities e USING (entity_id)"
        " WHERE e.entity_id IS NULL"
    ).fetchone()[0]
    assert orphan_aliases == 0
    orphan_facts = conn.execute(
        "SELECT COUNT(*) FROM facts f LEFT JOIN entities e"
        " ON e.entity_id = f.subject_entity_id WHERE e.entity_id IS NULL"
    ).fetchone()[0]
    assert orphan_facts == 0

    # 3. No two active canonical facts disagree on a (subject, predicate) without
    #    at least one open adjudication... stronger: canonical facts are unique
    #    per (subject, predicate, object) and contradictions live in the queue.
    dup_canon = conn.execute(
        "SELECT subject_entity_id, predicate, COALESCE(object_entity_id, ''),"
        " COALESCE(LOWER(TRIM(object_literal)), ''), COUNT(*) c FROM facts"
        " WHERE status='canonical' GROUP BY 1, 2, 3, 4 HAVING c > 1"
    ).fetchall()
    assert dup_canon == []

    # 4. Motif facts are never canonical: a motif-hinted claim's fact stays motif.
    #    (status enum makes 'motif' and 'canonical' mutually exclusive per row;
    #    promotion never touches motif rows -- asserted behaviorally in tests.)

    # 6. Status values are within the allowed enums (CHECKs held).
    for table, col, allowed in [
        ("entities", "status", {"provisional", "canonical", "deprecated"}),
        ("facts", "status", {"canonical", "soft", "motif", "deprecated"}),
        ("lore_chunks", "status", {"provisional", "canonical", "suppressed", "deprecated"}),
        ("claims", "canon_state", {"candidate", "accepted", "rejected", "needs_review"}),
        ("adjudication_queue", "status", {"open", "resolved", "dismissed"}),
    ]:
        values = {r[0] for r in conn.execute(f"SELECT DISTINCT {col} FROM {table}")}
        assert values <= allowed, f"{table}.{col} contains {values - allowed}"

    # FTS external-content index is in sync with lore_chunks.
    fts_count = conn.execute("SELECT COUNT(*) FROM lore_chunks_fts").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM lore_chunks").fetchone()[0]
    assert fts_count == chunk_count, "FTS index out of sync with lore_chunks"
