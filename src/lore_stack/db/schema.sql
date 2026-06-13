-- lore-stack canonical schema.
-- Base: "Core schema" block from hermes storrytell agent tech stack report.md, verbatim,
-- with the reconciliation amendments A1-A5 marked inline (rationale in README.md).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  source_kind TEXT NOT NULL CHECK (source_kind IN ('story','manual','test','import','adjudication')),
  uri TEXT,
  checksum TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS story_runs (
  story_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  title TEXT,
  prompt_text TEXT,
  story_text TEXT NOT NULL,
  model_provider TEXT,
  model_name TEXT,
  extractor_model TEXT,
  extraction_status TEXT NOT NULL CHECK (extraction_status IN ('pending','ok','error')),
  extraction_json TEXT CHECK (extraction_json IS NULL OR json_valid(extraction_json)),
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS entities (
  entity_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('character','location','item','organization','event','concept')),
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('provisional','canonical','deprecated')),
  summary TEXT,
  description TEXT,
  canonical_confidence REAL NOT NULL DEFAULT 0.0,
  created_from_story_id TEXT REFERENCES story_runs(story_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS entity_aliases (
  alias_id INTEGER PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL UNIQUE,
  alias_type TEXT NOT NULL CHECK (alias_type IN ('primary','nickname','surface','imported'))
) STRICT;

CREATE TABLE IF NOT EXISTS story_entities (
  story_id TEXT NOT NULL REFERENCES story_runs(story_id) ON DELETE CASCADE,
  entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('primary','secondary','mentioned')),
  mention_count INTEGER NOT NULL DEFAULT 1,
  salience REAL NOT NULL DEFAULT 0.0,
  PRIMARY KEY (story_id, entity_id)
) STRICT;

CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY,
  story_id TEXT NOT NULL REFERENCES story_runs(story_id) ON DELETE CASCADE,
  subject_entity_id TEXT REFERENCES entities(entity_id),
  predicate TEXT NOT NULL,
  object_entity_id TEXT REFERENCES entities(entity_id),
  object_literal TEXT,
  confidence REAL NOT NULL,
  canon_state TEXT NOT NULL CHECK (canon_state IN ('candidate','accepted','rejected','needs_review')),
  evidence_excerpt TEXT,
  extractor_payload_json TEXT CHECK (extractor_payload_json IS NULL OR json_valid(extractor_payload_json)),
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS facts (
  fact_id TEXT PRIMARY KEY,
  subject_entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
  predicate TEXT NOT NULL,
  object_entity_id TEXT REFERENCES entities(entity_id),
  object_literal TEXT,
  confidence REAL NOT NULL,
  -- A1: 'motif' added so recurring jokes are storable but never asserted canon.
  status TEXT NOT NULL CHECK (status IN ('canonical','soft','motif','deprecated')),
  first_supported_story_id TEXT REFERENCES story_runs(story_id),
  last_supported_story_id TEXT REFERENCES story_runs(story_id),
  source_claim_id TEXT REFERENCES claims(claim_id),
  -- A2: human authorship lineage for operator edits made through the visualizer.
  manual_source_id TEXT REFERENCES sources(source_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  -- A2: every fact carries provenance: extracted lineage or a manual source.
  CHECK (source_claim_id IS NOT NULL OR manual_source_id IS NOT NULL)
) STRICT;

CREATE TABLE IF NOT EXISTS lore_chunks (
  chunk_id TEXT PRIMARY KEY,
  scope TEXT NOT NULL CHECK (scope IN ('global','entity','story')),
  entity_id TEXT REFERENCES entities(entity_id) ON DELETE CASCADE,
  story_id TEXT REFERENCES story_runs(story_id) ON DELETE CASCADE,
  title TEXT,
  body TEXT NOT NULL,
  activation_keys_json TEXT NOT NULL CHECK (json_valid(activation_keys_json)),
  retrieval_mode TEXT NOT NULL CHECK (retrieval_mode IN ('key','semantic','hybrid','pinned')),
  insertion_lane TEXT NOT NULL CHECK (insertion_lane IN ('character_card','world_info','relationships','open_hooks','recent_continuity')),
  group_key TEXT,
  priority INTEGER NOT NULL DEFAULT 100,
  token_estimate INTEGER NOT NULL DEFAULT 0,
  -- A3: 'deprecated' added; deletes are soft status flips, rows survive.
  status TEXT NOT NULL CHECK (status IN ('provisional','canonical','suppressed','deprecated')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

-- A5: external-content FTS5 columns must mirror content-table column names
-- (the doc-1 draft used a non-existent 'activation_keys' column, which makes any
-- full scan of the FTS table fail), and the delete/update triggers must feed the
-- FTS 'delete' command the exact values that were inserted (hence raw column
-- values and COALESCE on the nullable title) or the index corrupts.
CREATE VIRTUAL TABLE IF NOT EXISTS lore_chunks_fts USING fts5(
  title,
  body,
  activation_keys_json,
  content='lore_chunks',
  content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS lore_chunks_ai AFTER INSERT ON lore_chunks BEGIN
  INSERT INTO lore_chunks_fts(rowid, title, body, activation_keys_json)
  VALUES (new.rowid, COALESCE(new.title, ''), new.body, new.activation_keys_json);
END;

CREATE TRIGGER IF NOT EXISTS lore_chunks_ad AFTER DELETE ON lore_chunks BEGIN
  INSERT INTO lore_chunks_fts(lore_chunks_fts, rowid, title, body, activation_keys_json)
  VALUES ('delete', old.rowid, COALESCE(old.title, ''), old.body, old.activation_keys_json);
END;

CREATE TRIGGER IF NOT EXISTS lore_chunks_au AFTER UPDATE ON lore_chunks BEGIN
  INSERT INTO lore_chunks_fts(lore_chunks_fts, rowid, title, body, activation_keys_json)
  VALUES ('delete', old.rowid, COALESCE(old.title, ''), old.body, old.activation_keys_json);
  INSERT INTO lore_chunks_fts(rowid, title, body, activation_keys_json)
  VALUES (new.rowid, COALESCE(new.title, ''), new.body, new.activation_keys_json);
END;

CREATE TABLE IF NOT EXISTS chunk_embeddings (
  chunk_id TEXT PRIMARY KEY REFERENCES lore_chunks(chunk_id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  vector_blob BLOB NOT NULL,
  norm REAL NOT NULL DEFAULT 1.0,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS compiler_runs (
  compile_id TEXT PRIMARY KEY,
  query_text TEXT NOT NULL,
  target_entity_id TEXT REFERENCES entities(entity_id),
  compiled_context_text TEXT NOT NULL,
  selected_chunk_ids_json TEXT NOT NULL CHECK (json_valid(selected_chunk_ids_json)),
  budget_tokens INTEGER NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS adjudication_queue (
  item_id TEXT PRIMARY KEY,
  item_kind TEXT NOT NULL CHECK (item_kind IN ('claim','fact','entity')),
  reason TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  status TEXT NOT NULL CHECK (status IN ('open','resolved','dismissed')),
  created_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_entities_kind_status ON entities(kind, status);
CREATE INDEX IF NOT EXISTS idx_story_runs_created_at ON story_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_story_entities_entity ON story_entities(entity_id);
CREATE INDEX IF NOT EXISTS idx_claims_story ON claims(story_id);
CREATE INDEX IF NOT EXISTS idx_claims_subject_predicate ON claims(subject_entity_id, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate_status ON facts(subject_entity_id, predicate, status);
CREATE INDEX IF NOT EXISTS idx_lore_chunks_entity_status ON lore_chunks(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_lore_chunks_lane_priority ON lore_chunks(insertion_lane, priority DESC);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model ON chunk_embeddings(model);
CREATE INDEX IF NOT EXISTS idx_adjudication_status ON adjudication_queue(status);

-- A4: idempotency anchor; re-applying a delta with an already-seen checksum is a no-op.
CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_checksum
  ON sources(checksum) WHERE checksum IS NOT NULL;
