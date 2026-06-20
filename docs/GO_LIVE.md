# Going live with Hermes

How to plug a live Hermes storyteller into lore-stack. The substrate is
model-free: **Hermes' own LLM is the extractor** (via the `lore-extraction` skill),
and lore-stack stores what it produces and compiles continuity. This guide is the
one-time wiring.

## The live loop

```
operator: "run extraction — get Boxwell, his profession, where he lives; ignore Mirel"
        │
        ▼  (lore-extraction skill, pinned to a capable LLM)
Hermes reads the story (+ recent continuity) and emits TWO LoreDeltas:
   direct.json  (operator-named items)        stage.json  (everything else it found)
        │                                            │
        ▼  narrative-lore skill                      ▼
   ingest-delta --canon  ─▶ canon                stage-delta ─▶ review inbox
        │                                            │
        │                                     operator downselects (visualizer / stage apply)
        ▼
   compile-context ─▶ bounded continuity for the next story (OpenAI semantic recall)
```

## One-time setup

### 1. Install the package + the live embedder

```powershell
cd D:\202606-tellerHermes-Fable\lore-stack
.\.venv\Scripts\pip install -e ".[embeddings]"
```

Set the OpenAI key (used by `--embedder openai`) and make `openai` the default:

```powershell
setx OPENAI_API_KEY "sk-..."
setx LORE_STACK_EMBEDDER "openai"
```

**Local / offline alternative — Ollama** (no API key, no cloud): instead of the
above, `pip install -e ".[ollama]"`, run `ollama serve` with the model pulled
(`ollama pull nomic-embed-text`), and `setx LORE_STACK_EMBEDDER "ollama"`. Honors
`OLLAMA_HOST`. Pick one embedder and use it consistently — vectors from different
embedders don't cross.

### 2. Point the skills at the right Python

The skills shell `python -m lore_stack.cli`, so `LORE_STACK_PYTHON` must point at
the lore-stack venv (the Hermes venv does not have the package):

```powershell
setx LORE_STACK_PYTHON "D:\202606-tellerHermes-Fable\lore-stack\.venv\Scripts\python.exe"
```

### 3. Install both skills into Hermes

Copy the two skill directories into Hermes' skills folder:

```powershell
Copy-Item -Recurse "src\lore_stack\hermes\extraction" "$HOME\.hermes\skills\lore-extraction"
Copy-Item -Recurse "src\lore_stack\hermes\storage"    "$HOME\.hermes\skills\narrative-lore"
```

- `lore-extraction` — the extractor instructions. **Pin it to a capable model**
  (the one that produces good structured JSON). It reads `references/contract.md`.
- `narrative-lore` — the storage/retrieval shell. Pin it to anything.

### 4. Initialize the lore database

```powershell
.\.venv\Scripts\python.exe -m lore_stack.cli init-db --db "$HOME\.hermes\local\lore-stack\data\lore.db"
```

(Or use a lore *home* directory for several worlds: `lores create --home <dir>
--name <world>`, and target `--db <dir>\<world>.db`.)

### 5. Create / point a Hermes storyteller profile

Load both skills in the profile and set the `narrative_lore.db_path` config to the
DB from step 4. Pin `lore-extraction`'s model to a capable LLM.

## Running it

- **Before a story:** ask for continuity — the `narrative-lore` skill runs
  `compile-context --embedder openai` and returns the lore block; hand it to the
  story model.
- **After a story:** say *"run extraction — get X, Y; ignore Z."* The
  `lore-extraction` skill produces `direct.json` + `stage.json` and stores them
  (`ingest-delta --canon` + `stage-delta`).
- **Review:** open the visualizer (`lore-stack serve --db <db>`) and downselect the
  staged items in the Inbox, or `stage apply` from the CLI.

## Verifying the loop without Hermes (dry run)

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py -m lore_stack.cli init-db --db demo\golive.db
& $py -m lore_stack.cli ingest-delta --db demo\golive.db --file direct.json --canon
& $py -m lore_stack.cli stage-delta  --db demo\golive.db --file stage.json
& $py -m lore_stack.cli compile-context --db demo\golive.db --query "Tell a story with Boxwell"
& $py -m lore_stack.cli stage list --db demo\golive.db
```

The direct items come back as canon in the compiled context; the staged delta waits
in `stage list`. With `LORE_STACK_EMBEDDER=openai` set, embeddings are real OpenAI
vectors. (`direct.json` / `stage.json` are the two deltas from the worked example in
`hermes/extraction/references/contract.md`.)
