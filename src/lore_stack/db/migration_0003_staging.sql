-- A7: the staging area for review-before-commit ingestion. An extracted
-- LoreDelta is parked here as a *proposal*; it writes nothing to the lore until
-- the operator approves a (possibly downselected) subset. This is the primary
-- ingestion path: extract -> review -> downselect -> apply.

CREATE TABLE IF NOT EXISTS staged_deltas (
  staging_id TEXT PRIMARY KEY,
  story_id TEXT,
  story_title TEXT,
  story_text TEXT,
  delta_json TEXT NOT NULL CHECK (json_valid(delta_json)),
  status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'discarded')),
  decisions_json TEXT CHECK (decisions_json IS NULL OR json_valid(decisions_json)),
  created_at TEXT NOT NULL,
  resolved_at TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_staged_status ON staged_deltas(status, created_at);
