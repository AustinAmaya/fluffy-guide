-- A8: supersession proposals. A corroborated new value on a single-valued `state`
-- predicate (e.g. lives_in -- you live in one place but can move) opens a
-- 'supersession' item rather than a contradiction: accepting canonizes the new
-- value and deprecates the old, with superseded lineage recorded on the item.
-- `permanent` single-valued predicates (profession, species) keep opening plain
-- contradictions. SQLite can't ALTER a CHECK, so rebuild the table (the 0004
-- pattern); existing rows are preserved.

CREATE TABLE adjudication_queue_new (
  item_id TEXT PRIMARY KEY,
  item_kind TEXT NOT NULL CHECK (item_kind IN ('claim', 'fact', 'entity', 'merge_suggestion', 'supersession')),
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
