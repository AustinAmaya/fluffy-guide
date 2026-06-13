-- A6: the predicate registry. A controlled vocabulary that turns free-text
-- predicates into a governed ontology: each predicate declares its cardinality
-- (single-valued vs multi-valued), persistence class, and accepted aliases.
-- Seeded from db/predicates.json at init-db (idempotent INSERT OR IGNORE).

CREATE TABLE IF NOT EXISTS predicates (
  predicate_id TEXT PRIMARY KEY,
  aliases_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(aliases_json)),
  domain_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(domain_json)),
  range TEXT NOT NULL DEFAULT 'text' CHECK (range IN ('text', 'entity')),
  cardinality TEXT NOT NULL CHECK (cardinality IN ('single', 'multi')),
  persistence TEXT NOT NULL CHECK (persistence IN ('permanent', 'state', 'episodic')),
  symmetry TEXT NOT NULL DEFAULT 'directed' CHECK (symmetry IN ('directed', 'symmetric')),
  inverse_of TEXT,
  registered_by TEXT NOT NULL CHECK (registered_by IN ('seed', 'operator', 'extractor')),
  created_at TEXT NOT NULL
) STRICT;

-- Each extractor spelling normalizes to exactly one predicate; uniqueness of the
-- normalized alias prevents one spelling mapping to two predicates.
CREATE TABLE IF NOT EXISTS predicate_aliases (
  normalized_alias TEXT PRIMARY KEY,
  predicate_id TEXT NOT NULL REFERENCES predicates(predicate_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX IF NOT EXISTS idx_predicate_aliases_pid ON predicate_aliases(predicate_id);
