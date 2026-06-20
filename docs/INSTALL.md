# Installing lore-stack into a Hermes

How to wire a Hermes profile onto lore-stack so it has a persistent, deterministic
world-memory. The substrate is **model-free**: *Hermes' own LLM is the extractor*
(via the `lore-extract` skill), and lore-stack stores what it produces and compiles
continuity on request. This is a one-time wiring, done in **one command**.

The current consumer is the Bear & Papa storyteller, but nothing here is
story-specific — the skills are neutral verbs (extract / store / compile) over a
shared world-memory, so any future Hermes consumer wires in the same way.

## The one command

```powershell
lore-stack init-hermes --home <HERMES_HOME> --embedder ollama
```

That single call, idempotent and non-destructive:

1. **Copies the two skills** into `<HERMES_HOME>/skills/` — `lore-extract` (the
   extractor instructions; reads `references/contract.md`) and `lore-memory` (the
   store/retrieve CLI shell). An existing skill dir is backed up to `<name>.bak`
   first (unless `--force`).
2. **Writes `<HERMES_HOME>/.env`** with `LORE_STACK_PYTHON`, `LORE_STACK_EMBEDDER`,
   and `LORE_STACK_DB`. It never touches `config.yaml` — Hermes auto-discovers
   skills from `skills/`.
3. **Inits a bare lore** at the db path (default `<HERMES_HOME>/local/lore.db`).
4. **Prints a summary** and the one manual step below.

Options:

| flag | meaning | default |
|------|---------|---------|
| `--home` | the Hermes `HERMES_HOME` to wire | *(required)* |
| `--db` | lore database path | `<home>/local/lore.db` |
| `--embedder` | `fake` \| `openai` \| `ollama`, written to `.env` | `fake` |
| `--python` | the Python that has lore-stack installed | this interpreter |
| `--force` | overwrite existing skill dirs in place (no `.bak`) | off |

### Prerequisites

- **lore-stack installed** in a venv. Point `--python` at it (or run `init-hermes`
  from it). For a local/offline embedder, install the extra and run Ollama:

  ```powershell
  pip install -e "D:\202606-tellerHermes-Fable\lore-stack[ollama]"
  ollama serve            # with: ollama pull nomic-embed-text
  ```

  For cloud embeddings instead, use `[embeddings]` + `OPENAI_API_KEY` and
  `--embedder openai`. Pick one embedder and use it consistently — vectors from
  different embedders don't cross.

### The one manual step

`init-hermes` can't pick your models. In the Hermes profile, **pin the
`lore-extract` skill to a capable model** (it produces the structured `LoreDelta`
JSON). `lore-memory` is a thin CLI shell and needs no reasoning.

## The live loop

```
operator: "run extraction — get Boxwell, his profession, where he lives; ignore Mirel"
        │
        ▼  (lore-extract skill, pinned to a capable LLM)
Hermes reads the text (+ recent continuity) and emits TWO LoreDeltas:
   direct.json  (operator-named items)        stage.json  (everything else it found)
        │                                            │
        ▼  lore-memory skill                         ▼
   ingest-delta --canon  ─▶ canon                stage-delta ─▶ review inbox
        │                                            │
        │                                     operator downselects (visualizer / stage apply)
        ▼
   compile-context ─▶ bounded continuity for the next piece of text
```

`compile-context` is the shared **read primitive**: any consumer calls it to get a
bounded, lane-based block of the world relevant to a query. On a bare lore it
returns cleanly with empty lanes.

## Running it

- **Before producing text:** ask for continuity — `lore-memory` runs
  `compile-context` (with the `.env` embedder) and returns the lore block.
- **After:** say *"run extraction — get X, Y; ignore Z."* The `lore-extract` skill
  produces `direct.json` + `stage.json` and stores them (`ingest-delta --canon` +
  `stage-delta`).
- **Review:** open the visualizer (`lore-stack serve --db <db>`) and downselect the
  staged items in the Inbox, or `stage apply` from the CLI.

Because the `.env` sets `LORE_STACK_DB`, the skills don't need the db path threaded
through every call — `-DbPath` / `--db` is optional and falls back to that env var.

## Verifying the wiring (dry run, no Hermes)

```powershell
$py = "<the venv python from .env>"
$db = "<HERMES_HOME>\local\lore.db"
& $py -m lore_stack.cli compile-context --db $db --query "anything"   # empty world, clean output
& $py -m lore_stack.cli ingest-delta   --db $db --file direct.json --canon --embedder ollama
& $py -m lore_stack.cli compile-context --db $db --query "Tell a story with Boxwell" --embedder ollama
```

The first compile on the bare lore returns cleanly with empty lanes; after the
`--canon` ingest the named items come back in the compiled context. (`direct.json`
is the worked example in `hermes/extraction/references/contract.md`.) Use the same
embedder to ingest and to query.
