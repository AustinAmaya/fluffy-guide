---
name: lore-memory
description: Store and retrieve lore in the local lore-stack substrate (deterministic SQLite world-memory).
version: 0.4.0
metadata:
  hermes:
    tags: [lore, memory, sqlite, retrieval]
    category: knowledge
    requires_toolsets: [terminal]
    config:
      - key: lore_memory.db_path
        description: Path to the lore SQLite database
        default: "~/.hermes/local/lore.db"
        prompt: Lore database path
---
# Lore Memory (store + retrieve)

The **read/write half** of the lore loop: a thin shell over the local `lore-stack`
CLI. It contains no lore logic — the deterministic substrate is the Python package.
The **extraction half** (turning a piece of text into structured lore) is the
separate `lore-extract` skill; this skill stores what that produces and retrieves
continuity. Pin this skill to any model; it needs no reasoning.

This skill needs `LORE_STACK_PYTHON` set to the lore-stack venv's Python (else it
falls back to bare `python`, which won't have the package). Embeddings default to
`$LORE_STACK_EMBEDDER` (set `ollama` for local semantic recall — needs `ollama
serve` + `nomic-embed-text` — or `openai` for cloud). The db path defaults to
`$LORE_STACK_DB` when `-DbPath` is omitted.

## compile-context — the shared read primitive

`compile-context` is how **any** consumer reads the world: it returns a bounded,
lane-based block of the lore relevant to a query. Run it whenever you need
continuity before producing new text:

    powershell -NoProfile -ExecutionPolicy Bypass -File "${HERMES_SKILL_DIR}/scripts/lore_skill.ps1" `
      -Command compile-context -DbPath "<db_path>" -Query "<request>" -Out "<artifact path>" -Embedder ollama

(POSIX: `bash "${HERMES_SKILL_DIR}/scripts/lore_skill.sh" compile-context <db> <query> <out>`)

The artifact leads with `=== CONTEXT FOR: <entities> ===` and lane sections
(Character card, World info, Relationships, Open hooks, Recent continuity). Use the
same embedder you ingest with (live and fake vectors don't cross). On a bare lore
it returns cleanly with empty lanes.

## Store extracted lore (two tiers)

The `lore-extract` skill produces **two** `LoreDelta` JSON files per run: a DIRECT
delta (the items the operator named — apply straight to canon) and a STAGE delta
(the rest — hold for review).

**Direct → canon** (operator-vouched, written canonical immediately):

    powershell ... lore_skill.ps1 -Command ingest-delta -DbPath "<db>" -File "<direct.json>" -Canon -Embedder ollama

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
      --selection '{"entities":[0],"claims":[0,2]}' --embedder ollama

## Initialize a fresh database

    powershell ... lore_skill.ps1 -Command init-db -DbPath "<db_path>"
