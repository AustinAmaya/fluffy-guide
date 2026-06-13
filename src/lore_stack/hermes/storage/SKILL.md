---
name: narrative-lore
description: Store and retrieve narrative lore in the local lore-stack substrate (deterministic SQLite memory for stories).
version: 0.3.0
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
# Narrative Lore (storage + retrieval)

The **storage half** of the lore loop: a thin shell over the local `lore-stack`
CLI. It contains no lore logic — the deterministic substrate is the Python package.
The **extraction half** (turning a told story into structured lore) is the separate
`lore-extraction` skill; this skill stores what that produces and retrieves
continuity. Pin this skill to any model; it needs no reasoning.

This skill needs `LORE_STACK_PYTHON` set to the lore-stack venv's Python (else it
falls back to bare `python`, which won't have the package). Embeddings default to
`$LORE_STACK_EMBEDDER` (set it to `openai` for live semantic recall; needs
`OPENAI_API_KEY` and `pip install lore-stack[embeddings]`).

## Compile continuity context before writing

When the user asks for continuity before a story, compile lore context and return
the artifact:

    powershell -NoProfile -ExecutionPolicy Bypass -File "${HERMES_SKILL_DIR}/scripts/lore_skill.ps1" `
      -Command compile-context -DbPath "<db_path>" -Query "<user request>" -Out "<artifact path>" -Embedder openai

(POSIX: `bash "${HERMES_SKILL_DIR}/scripts/lore_skill.sh" compile-context <db> <query> <out>`)

The artifact leads with `=== CONTEXT FOR: <entities> ===` and lane sections
(Character card, World info, Relationships, Open hooks, Recent continuity). Use the
same embedder you ingest with (live and fake vectors don't cross).

## Store extracted lore (two tiers)

The `lore-extraction` skill produces **two** `LoreDelta` JSON files per run: a
DIRECT delta (the items the operator named — apply straight to canon) and a STAGE
delta (the rest — hold for review).

**Direct → canon** (operator-vouched, written canonical immediately):

    powershell ... lore_skill.ps1 -Command ingest-delta -DbPath "<db>" -File "<direct.json>" -Canon -Embedder openai

(POSIX: `bash lore_skill.sh ingest-delta <db> <direct.json> canon`)

**The rest → review queue** (writes nothing to the lore until you apply it):

    powershell ... lore_skill.ps1 -Command stage-delta -DbPath "<db>" -File "<stage.json>"

(POSIX: `bash lore_skill.sh stage-delta <db> <stage.json>`)

## Review and apply staged lore

Downselect staged proposals in the visualizer's inbox (run
`lore-stack serve --db <db>` and open it), or from the CLI:

    <python> -m lore_stack.cli stage list --db <db>
    <python> -m lore_stack.cli stage show --db <db> --id <stg_id>
    <python> -m lore_stack.cli stage apply --db <db> --id <stg_id> \
      --selection '{"entities":[0],"claims":[0,2]}' --embedder openai

## Initialize a fresh database

    powershell ... lore_skill.ps1 -Command init-db -DbPath "<db_path>"
