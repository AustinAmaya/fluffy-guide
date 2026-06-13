-- Extend the adjudication queue with a 'merge_suggestion' kind: an aggressive,
-- deterministic near-duplicate detector opens these when a new soft fact's object
-- is embedding-similar to an existing value on the same (subject, predicate).
-- They never auto-merge -- the operator picks the survivor. SQLite can't ALTER a
-- CHECK, so the table is rebuilt (nothing references adjudication_queue, so this
-- is safe); existing rows are preserved.

CREATE TABLE adjudication_queue_new (
  item_id TEXT PRIMARY KEY,
  item_kind TEXT NOT NULL CHECK (item_kind IN ('claim', 'fact', 'entity', 'merge_suggestion')),
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
