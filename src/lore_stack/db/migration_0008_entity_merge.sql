-- Operator-initiated entity merges. propose_entity_merge queues an 'entity_merge'
-- item; resolving it folds duplicate entities into one survivor (re-pointing their
-- facts/relationships/aliases/story mentions, soft-deprecating the duplicates).
-- SQLite can't ALTER a CHECK, so rebuild the table (the 0004/0005 pattern); existing
-- rows are preserved.

CREATE TABLE adjudication_queue_new (
  item_id TEXT PRIMARY KEY,
  item_kind TEXT NOT NULL CHECK (item_kind IN ('claim', 'fact', 'entity', 'merge_suggestion', 'supersession', 'entity_merge')),
  reason TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  status TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'dismissed')),
  created_at TEXT NOT NULL
) STRICT;

INSERT INTO adjudication_queue_new
  SELECT item_id, item_kind, reason, payload_json, status, created_at FROM adjudication_queue;

DROP TABLE adjudication_queue;
ALTER TABLE adjudication_queue_new RENAME TO adjudication_queue;

CREATE INDEX IF NOT EXISTS idx_adjudication_status ON adjudication_queue(status);
