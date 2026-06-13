-- A9: chunk staleness (fixes commitment C7). A chunk may declare the facts it
-- derives from (derived_from_fact_ids, resolved from each (subject, predicate) ref
-- at ingest). When any such fact is later deprecated or superseded, the chunk's
-- `stale` flag is set: retrieval/compilation excludes stale chunks, while the
-- visualizer still shows them so the operator can rewrite-or-confirm. Authored
-- chunks with no fact links keep today's behavior; synthesized fact-cards are
-- immune (regenerated from live facts at compile time).
--
-- Implemented as two ADDED COLUMNS rather than a new status value: lore_chunks is
-- the content table behind an FTS5 external-content index (with sync triggers) and
-- the target of a chunk_embeddings foreign key, so a CHECK-changing table rebuild
-- would force reconstructing the FTS index and risk the FK cascade. ADD COLUMN
-- avoids all of that; retrieval excludes `stale = 1` directly.

ALTER TABLE lore_chunks ADD COLUMN derived_from_fact_ids TEXT;
ALTER TABLE lore_chunks ADD COLUMN stale INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_lore_chunks_stale ON lore_chunks(stale);
