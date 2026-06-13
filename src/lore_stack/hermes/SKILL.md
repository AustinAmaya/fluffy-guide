---
name: narrative-lore
description: Query and maintain a local narrative lore memory substrate (lore-stack) for stories.
version: 0.2.0
metadata:
  hermes:
    tags: [storytelling, lore, sqlite, retrieval]
    category: writing
    requires_toolsets: [terminal]
    config:
      - key: narrative_lore.db_path
        description: Path to the lore SQLite database
        default: "~/.hermes/local/lore-stack/data/lore.db"
        prompt: Narrative lore database path
---
# Narrative Lore

This skill is a thin shell over the local `lore-stack` CLI. It contains no lore
logic of its own; the deterministic substrate is the Python package. The skill
script exposes the three operations a storytelling agent needs mid-session;
everything else (review queue, inspect, edit, snapshots, lores, the visualizer)
is on the full `lore-stack` CLI — see `docs/USER_GUIDE.md`.

## Compile continuity context before writing

When the user asks for continuity context before a story, compile lore context
and return the artifact:

    powershell -NoProfile -ExecutionPolicy Bypass -File "${HERMES_SKILL_DIR}/scripts/lore_skill.ps1" `
      -Command compile-context -DbPath "<db_path>" -Query "<user request>" -Out "<artifact path>"

(POSIX: `bash "${HERMES_SKILL_DIR}/scripts/lore_skill.sh" compile-context <db> <query> <out>`)

The artifact leads with `=== CONTEXT FOR: <entities> ===` and lane sections
(Character card, World info, Relationships, Open hooks, Recent continuity). It is
bounded and deterministic. A query naming nobody in a populated lore returns that
lore's recent continuity as optional connective tissue.

## Store an extracted LoreDelta

When the user provides an extracted `LoreDelta` JSON to store:

    powershell -NoProfile -ExecutionPolicy Bypass -File "${HERMES_SKILL_DIR}/scripts/lore_skill.ps1" `
      -Command ingest-delta -DbPath "<db_path>" -File "<delta.json>"

## Initialize a fresh database

    powershell -NoProfile -ExecutionPolicy Bypass -File "${HERMES_SKILL_DIR}/scripts/lore_skill.ps1" `
      -Command init-db -DbPath "<db_path>"

## Authoring a LoreDelta — the relationship rule (important)

Relationships (claims with an `object_slug`, i.e. an edge to another entity) are a
**closed set of 11** child-legible predicates. Anything off the set is **rejected**
at writeback (the claim is stored `rejected` and forms no fact; the rest of the
delta still applies), so author edges using only:

`family_of`, `friends_with`, `against`, `mentors`, `serves`, `leads`,
`belongs_to`, `lives_in`, `visits`, `wants`, `linked_to`.

Direction matters and is not reversible by alias, so flip ownership and teaching
when you author: `X keeps/owns Y` → `Y belongs_to X`; `X taught_by Y` →
`Y mentors X`. A fact about one entity (its job, species, colour) is an
**attribute** — a claim with an `object_literal` (e.g. `profession`, `species`),
an open vocabulary. Pure scenery (a part in a machine, honey in a tree) is
**prose**: put it in the entity's `summary`, not a claim. *A lens does not have a
social life.*
