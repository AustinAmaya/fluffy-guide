---
name: narrative-lore
description: Query and maintain a local narrative lore memory substrate (lore-stack) for stories.
version: 0.1.0
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
logic of its own; the deterministic substrate is the Python package.

When the user asks for continuity context before writing a story, compile lore
context and return the artifact:

    powershell -NoProfile -ExecutionPolicy Bypass -File "${HERMES_SKILL_DIR}/scripts/lore_skill.ps1" `
      -Command compile-context -DbPath "<db_path>" -Query "<user request>" -Out "<artifact path>"

(POSIX: `bash "${HERMES_SKILL_DIR}/scripts/lore_skill.sh" compile-context <db> <query> <out>`)

When the user provides an extracted LoreDelta JSON to store:

    powershell -NoProfile -ExecutionPolicy Bypass -File "${HERMES_SKILL_DIR}/scripts/lore_skill.ps1" `
      -Command ingest-delta -DbPath "<db_path>" -File "<delta.json>"

To initialize a fresh database:

    powershell -NoProfile -ExecutionPolicy Bypass -File "${HERMES_SKILL_DIR}/scripts/lore_skill.ps1" `
      -Command init-db -DbPath "<db_path>"
