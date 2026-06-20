-- Migration 0007: operator-configured entity exclusions.
--
-- Some entities a consumer cares about are *owned outside the lore* — e.g. a
-- storyteller's protagonists, authored in a separate identity document. Those must
-- never be profiled here. An exclusion is a single normalized name key; at
-- writeback any delta entity whose slug / display name / alias normalizes to an
-- excluded key is dropped, together with every claim that references it and every
-- chunk bound to it. lore-stack stays domain-agnostic: it knows nothing about
-- *which* entities a consumer excludes, only that excluded keys never get stored.
CREATE TABLE IF NOT EXISTS entity_exclusions (
    name       TEXT PRIMARY KEY,   -- normalized exclusion key (writeback.exclusion_key)
    label      TEXT,               -- the operator's original spelling, for display
    created_at TEXT NOT NULL
) STRICT;
