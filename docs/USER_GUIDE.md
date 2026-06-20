# lore-stack — User Guide

How to do everything. lore-stack is a deterministic lore-memory substrate: it
remembers the people, places, things, and relationships in your stories, keeps
them consistent, and hands a story model a tight, relevant context block on
request. There is no LLM in the loop unless you wire one into a seam — the
substrate is plain Python over SQLite.

This guide is task-oriented. For the design rationale see the README and
`CLAUDE.md`; for the deep ontology, the project's ontology specification.

---

## 1. Install and first run

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"          # Windows; .venv/bin on POSIX

lore-stack init-db --db lore.db                 # create the schema
lore-stack ingest-delta --db lore.db \
  --file examples/lores/winnie-the-pooh/01_pooh_and_some_bees.delta.json
lore-stack compile-context --db lore.db --query "Tell a story with Pooh"
lore-stack serve --db lore.db                   # visualizer at http://127.0.0.1:8377
```

`lore-stack <command> --help` documents any command. Everything the visualizer
and the Hermes skill do is also available on this CLI.

The fastest way to see the whole system live is the demo, which seeds and freezes
four example worlds and opens the visualizer:

```bash
powershell -ExecutionPolicy Bypass -File demo.ps1
```

### Wiring a Hermes profile (one command)

To give a Hermes profile a persistent world-memory, install in one command — it
copies the two skills into `<home>/skills/`, writes a `<home>/.env`
(`LORE_STACK_PYTHON` / `LORE_STACK_EMBEDDER` / `LORE_STACK_DB`), and inits a bare
lore. Idempotent and non-destructive (existing skill dirs are backed up to
`<name>.bak` unless `--force`):

```bash
lore-stack init-hermes --home <HERMES_HOME> --embedder ollama
```

Because `.env` sets **`LORE_STACK_DB`**, `--db` is then optional on `init-db`,
`ingest-delta`, `stage-delta`, `stage`, and `compile-context` — they fall back to
that env var. The one manual step is pinning the `lore-extract` skill to a capable
model. See `docs/INSTALL.md` for the full flow.

### Live model adapters (opt-in)

By default everything runs on deterministic fakes — no API keys, no network. Two
seams can be switched to a real model; both are opt-in and both leave the
deterministic gate on the fakes:

- **Extraction:** `pip install lore-stack[anthropic]`, set `ANTHROPIC_API_KEY`, and
  use `lore_stack_adapters.anthropic_extractor.AnthropicExtractor`.
- **Embeddings (cloud):** `pip install lore-stack[embeddings]`, set `OPENAI_API_KEY`,
  and use `--embedder openai` (or `lore_stack_adapters.openai_embedder.OpenAIEmbedder`).
- **Embeddings (local, offline):** `pip install lore-stack[ollama]`, run `ollama
  serve` with `nomic-embed-text` pulled, and use `--embedder ollama` (or
  `lore_stack_adapters.ollama_embedder.OllamaEmbedder`). Honors `OLLAMA_HOST`.

Live and fake embeddings coexist in one lore (retrieval gates by model name), so use
the *same* embedder to ingest and to query. The `--embedder {fake,openai,ollama}`
flag (or `LORE_STACK_EMBEDDER`) selects it on `ingest-delta`, `stage apply`, and
`compile-context`.

---

## 2. Core concepts

- **Entity** — the only first-class thing: a `character`, `location`, `item`,
  `organization`, `event`, or `concept`. Identity is a normalized slug; surface
  names and nicknames are **aliases** that resolve to one entity and never fork it.
- **Claim** — a raw observation from one story: `(subject, predicate, object)`
  with a quoted evidence excerpt and a confidence. Claims are the append-only lab
  notebook; they are never edited.
- **Fact** — a distilled, usable assertion derived from claims. Facts have a
  status: `soft` (believed, single source) → `canonical` (corroborated) , plus
  `motif` (recurring joke, never canon) and `deprecated` (soft-deleted history).
  Every fact carries provenance, enforced by the database.
- **Lore** — one self-contained SQLite world, fully isolated from every other.
  The lore you select **is** the unit of narrative connection (see §9).
- **Chunk** — a piece of authored prose (a character card, a world note, an open
  hook, a continuity summary) tagged with an insertion lane. Chunks are what the
  compiler actually packs into a context block.

### Claim → fact lifecycle

```
claim (conf >= 0.7) ──▶ soft ──corroborated by a 2nd story──▶ canonical
                         │                                       │
motif-hinted claim ──▶ motif (terminal)                          │
                         └──────────── deprecated ◀──────────────┘
                              (soft delete / superseded by a manual edit)
```

A contradiction of a canonical fact never overwrites it — it opens an
adjudication item for you to resolve. Operator edits bypass the pipeline:
immediately canonical, prior value preserved as deprecated history.

---

## 3. The relationship ontology (read this before authoring lore)

The audience is a six-year-old, so the **relationship** vocabulary is small,
closed, and fixed. Predicates come in two kinds, governed differently:

### Relationships — a closed set of 11 (range: entity)

A relationship is an edge to **another entity**. There are exactly eleven, all
multi-valued:

| id | child-legible meaning | direction |
|----|------------------------|-----------|
| `family_of` | "they're family" | symmetric |
| `friends_with` | "friends / like each other" (absorbs trust & love) | symmetric |
| `against` | "doesn't get along / is the baddie to" | disliker → disliked |
| `mentors` | "teaches & looks after" | teacher → student |
| `serves` | "works for / helps / apprentice of" | helper → leader |
| `leads` | "is in charge of / boss of" | leader → led |
| `belongs_to` | "this thing/pet is someone's" | thing → owner |
| `lives_in` | "lives/stays in a place" (also place-in-place) | resident → place |
| `visits` | "goes to / travels to a place" | visitor → place |
| `wants` | "really wants / is after a thing or person" | wanter → wanted |
| `linked_to` | catch-all: "connected some other way" (only if none fit) | symmetric |

Two carry special behavior from their persistence class: `lives_in` is
single-valued and changeable, so a new home *supersedes* the old (see §8); `visits`
is episodic — story-anchored, it never hardens into permanent canon.

Aliases normalize in automatically (`resents` → `against`, `friend_of`/`trusts` →
`friends_with`, `resides_in` → `lives_in`, `apprentices_to`/`taught_by` →
`serves`, `sibling_of` → `family_of`, …). But **direction-reversing verbs are
flipped when you author, not aliased**, because an alias can't reverse an arrow:

- ownership: `Mirel keeps the inn` → `the-inn belongs_to mirel`
- teaching: `Boxwell taught_by Gregor` → `gregor mentors boxwell`

An entity-object claim whose predicate is **not** one of the eleven — or a text
attribute misused with an entity object — is **rejected** at writeback: the claim
is stored `rejected`, no fact forms, and the rest of the delta still applies. An
operator edit likewise can't mint a new edge type. To extend the set, add it to
`src/lore_stack/db/predicates.json` deliberately (the 12th slot is reserved for a
future `made_by` if "who built this" ever becomes a recurring edge).

### Attributes — an open vocabulary (range: text)

A fact about **one** entity, whose object is a literal string: `profession`,
`species`, `carries`, `has_trait`, `claimed_title`, … These are open: an
unregistered attribute still forms a soft fact (it just can't auto-canonize), and
an operator edit auto-registers it. Synonyms normalize (`occupation` →
`profession`).

### Edge vs attribute vs prose — the authoring rule

> Make it an **edge** only if it ties *two* named beings a child would point at
> twice and that could change later (who loves whom, who's family, who's the
> baddie, who lives where, whose toy, who leads whom). A fact about *one* thing —
> its job, species, colour, name — is an **attribute**. Mere *scenery* — a part
> fitted into a machine, honey sitting in a tree, a bell hanging in a room — is
> **prose**: write it in the entity's description and create no fact at all.
> *A lens does not have a social life.* Items relate to people through
> `belongs_to` / `wants`, not to other items.

---

## 4. The primary workflow: stage → review → downselect → apply

Automatic extraction over-produces, so the main path writes **nothing** to the
lore until you approve it. Stage a story (extract into a review queue), look at
what it proposes, uncheck the noise, and apply the subset.

```bash
# 1. Stage (writes nothing to the lore yet)
lore-stack stage-story --db lore.db --file story.md --fixtures tests/fixtures/stories

# 2. Review the queue
lore-stack stage list --db lore.db
lore-stack stage show --db lore.db --id stg_000001

# 3. Apply only the items you want (0-based indices per section)
lore-stack stage apply --db lore.db --id stg_000001 \
  --selection '{"entities":[0],"claims":[0,2],"chunks":[]}'

# or discard the whole proposal
lore-stack stage discard --db lore.db --id stg_000001
```

In the visualizer this is the **Inbox** panel: checkboxes per proposed item, an
Apply-selected button, and Discard.

On this reviewed path a human's approval replaces the confidence gate: approved
claims always form soft facts, and promotion to canonical is corroboration-count
only (≥2 distinct stories), regardless of the model's self-reported confidence.

### Direct ingest, direct-to-canon, and stage-delta

When you already trust a delta, skip review:

```bash
lore-stack ingest-delta --db lore.db --file story.delta.json     # soft facts, normal canonization
lore-stack ingest-delta --db lore.db --file direct.json --canon  # operator-vouched -> CANONICAL now
lore-stack stage-delta  --db lore.db --file story.delta.json     # queue a pre-made delta for review
```

The plain direct path keeps the original confidence thresholds (soft ≥ 0.7,
promote ≥ 0.9). **`--canon`** is the operator-authoritative path: every claim is
written canonical immediately (the named value wins, like a manual edit) — it's how
the live Hermes loop commits the items the operator explicitly named.
**`stage-delta`** is the review-queue sibling of `ingest-delta` (stages a *pre-made*
delta; `stage-story` is the fixture-based extractor equivalent). Re-applying a delta
with an already-seen checksum is a detectable no-op.

Add **`--embedder openai`** (or set `LORE_STACK_EMBEDDER=openai`) on `ingest-delta`,
`stage-delta`, `stage apply`, and `compile-context` for live OpenAI semantic recall
(needs `lore-stack[embeddings]` + `OPENAI_API_KEY`); the default is the fake
embedder. Use the same embedder to ingest and to query. See `docs/INSTALL.md` for
the one-command Hermes wiring.

---

## 5. Compiling a context block

Hand a story model exactly the lore it needs for the next scene:

```bash
lore-stack compile-context --db lore.db --query "Tell another story with Boxwell"
lore-stack compile-context --db lore.db --query "..." --json --out context.json
lore-stack compile-context --db lore.db --query "..." --budget 3000
```

The output leads with a primary `=== CONTEXT FOR: <entities> ===` header, then
lane sections (`## Character card`, `## World info`, `## Relationships`,
`## Open hooks`, `## Recent continuity`). It is bounded by per-lane token budgets
(global 6000, overridable); over-budget chunks are dropped whole by priority,
never truncated mid-fact. Output is **byte-identical** for the same DB + query,
and every compile writes an auditable `compiler_runs` row.

A query that names a character is the target. A query that names nobody in a
populated lore returns that lore's recent continuity, clearly labeled as optional
connective tissue (so a brand-new character can be woven into existing threads).
The same query in a brand-new empty lore returns nothing.

---

## 6. Inspecting and exporting

```bash
lore-stack inspect entity    --db lore.db --slug boxwell   # entity + aliases + facts + chunks
lore-stack inspect conflicts --db lore.db                  # open adjudication items
lore-stack inspect motifs    --db lore.db                  # motif facts
lore-stack inspect stories   --db lore.db                  # ingested stories

lore-stack export --db lore.db --format markdown           # whole lore
lore-stack export --db lore.db --entity boxwell            # one entity + 1-hop neighbors (JSON)
```

---

## 7. Editing, deleting, restoring (the human-authoritative carve-outs)

Operator edits are immediately canonical and bypass adjudication; the prior value
is preserved as deprecated history. All deletes are soft.

```bash
# attribute edit (open vocabulary, auto-registers a new predicate)
lore-stack edit-fact --db lore.db --entity-id ent_boxwell --predicate profession --value horologist

# relationship edit (must use one of the 11 closed predicates; object is an entity)
lore-stack edit-fact --db lore.db --entity-id ent_the-brambled-inn --predicate belongs_to \
  --object-entity-id ent_mirel

lore-stack deprecate --db lore.db --entity-id ent_boxwell    # soft delete (cascades to facts/chunks)
lore-stack deprecate --db lore.db --fact-id  fct_xxxx
lore-stack deprecate --db lore.db --chunk-id chk_xxxx
lore-stack restore   --db lore.db --entity-id ent_boxwell    # un-delete (returns as provisional)
```

A relationship edit with an unregistered predicate is rejected — relationships
are a closed set.

---

## 8. Conflicts, supersessions, and merge suggestions

Three ways the system asks for a human decision, all surfaced in the visualizer's
**Conflicts** panel (and via `inspect conflicts`):

- **Contradiction** — a story asserts a value that conflicts with a canonical
  *permanent* single-valued fact (e.g. "Boxwell is a baker" vs canon "clockmaker").
  Canon is untouched; resolve by keeping the existing value or accepting the
  proposed one (which deprecates the old via the authoritative edit path).
- **Supersession** — a story asserts a new value for a *changeable* (`state`)
  single-valued fact, like `lives_in` (someone moved). Instead of a contradiction,
  a supersession proposal opens; accepting canonizes the new value, deprecates the
  old, and records `superseded_by` lineage. Same keep/accept resolution.
- **Merge suggestion** — a new soft fact's object is embedding-similar (cosine ≥
  0.5) to an existing value on the same subject+predicate (e.g. "cedar tool case"
  ~ "a cedar case of tools"). Never auto-merged; pick which value survives and the
  other becomes deprecated history.

Resolve with one click in the UI, or through the API (`POST
/api/conflicts/<id>/resolve` for contradictions and supersessions, `POST
/api/merge/<id>/resolve` for merges).

**Stale chunks.** A chunk can declare the facts it derives from (`derived_from`, by
subject + predicate). When a source fact is later deprecated or superseded, the
chunk is flagged **stale**: held out of compiled context (so prose never silently
narrates an outdated fact) and surfaced in the **Stale chunks** panel, where you
confirm it still reads true (clears the flag, `POST /api/chunk/<id>/confirm`) or
rewrite it.

---

## 9. Lores: isolated worlds

A lore is one SQLite database, fully isolated. Point the server at a **lore home**
directory to keep several and switch between them in the UI.

```bash
lore-stack lores create --home lores --name middlemarsh
lore-stack lores copy   --home lores --from middlemarsh --to middlemarsh-draft   # independent copy
lore-stack lores list   --home lores
lore-stack serve        --home lores            # UI dropdown + "+ lore" / "copy lore"
```

Every CLI command targets a lore with `--db lores/<name>.db`; every API call in
home mode selects one with `?lore=<name>`.

### Frozen baselines and full reset

Freeze a pristine baseline (DB **and** snapshot history) you can play against and
hard-reset to:

```bash
lore-stack lores freeze --home lores --name harrow-hollow   # capture the baseline
lore-stack lores reset  --home lores --name harrow-hollow   # full hard restore to it
```

`reset` reverts **everything** since the freeze (content and history); the
baseline is the recovery point, so there's no pre-reset snapshot. In the UI a
"reset to frozen" button appears only for frozen lores, behind a strong confirm.

---

## 10. Snapshots and rollback

Every mutating operation auto-snapshots the lore first.

```bash
lore-stack snapshot list     --db lore.db
lore-stack snapshot create   --db lore.db --label "before big edit"
lore-stack snapshot rollback --db lore.db --seq 7      # itself saves a pre-rollback snapshot
```

In the visualizer's **History** panel each entry can be **previewed** read-only
(loads that snapshot's graph without mutating the live lore) before you roll back.

---

## 11. The visualizer

`lore-stack serve` runs a local Flask JSON API plus a single-file, fully offline
frontend (vanilla JS + SVG, no CDN). Panels:

- **Graph** — entities colored by kind; relationship facts drawn as directed
  edges; pan (drag), zoom (scroll), and a home/deselect control.
- **Entity detail** — summary, aliases, facts with provenance drilldown
  (extracted lineage or manual source), and bound chunks.
- **Inbox** — staged proposals to review, downselect, and apply or discard.
- **Conflicts** — contradictions and merge suggestions, each with one-click
  resolution.
- **Motifs** — recurring jokes/titles, shown but never asserted as canon.
- **Retrieval inspector** — type a query and see the candidate chunks with their
  scores and the reasons each was selected.
- **Context preview** — the compiled block for a query, with per-chunk traces.
- **History** — snapshots with read-only preview and rollback; "reset to frozen"
  for frozen lores.
- **Lore switcher** (home mode) — dropdown, "+ lore", "copy lore".

Writes from the UI are the same two authoritative carve-outs: edit = canonical
manual fact, delete = soft deprecate.

---

## 12. The deterministic gate

```bash
.venv/Scripts/python -m pytest -m "not model"
```

This must be green and byte-for-byte repeatable. It covers ingest, corroboration,
alias resolution, the closed relationship set, contradiction handling, retrieval,
budget enforcement, the invariant suite, Hypothesis property tests, adversarial
inputs, golden-file + byte-determinism, and the visualizer API. Live-model tests
carry the `model` marker and are excluded.

---

## Command reference

For the `--db`-taking commands marked below, `--db` is optional when
`LORE_STACK_DB` is set (the value `init-hermes` writes to `.env`).

| Command | What it does |
|---|---|
| `init-hermes --home [--db] [--embedder] [--python] [--force]` | One-command install into a Hermes home: copy skills, write `.env`, init a bare lore. |
| `init-db [--db]` | Create the schema (idempotent migrations + registry seed). |
| `ingest-delta [--db] --file [--story-text] [--canon] [--embedder]` | Write back a `LoreDelta` JSON; `--canon` = operator-authoritative direct-to-canon. |
| `stage-delta [--db] --file [--story-text]` | Stage a pre-made `LoreDelta` JSON for review (writes nothing yet). |
| `ingest-story --db --file --fixtures [--story-id]` | Extract (FakeExtractor) + write back a story file. |
| `stage-story --db --file --fixtures [--story-id]` | Extract a story into the review queue (writes nothing). |
| `stage list\|show\|apply\|discard [--db] [--id] [--selection] [--status] [--embedder]` | Drive the review queue. |
| `compile-context [--db] --query [--budget] [--out] [--json] [--embedder]` | Compile a bounded context block. |
| `inspect entity\|conflicts\|motifs\|stories --db [--slug]` | Inspect lore state. |
| `edit-fact --db --entity-id --predicate [--value] [--object-entity-id]` | Authoritative manual edit. |
| `deprecate --db [--entity-id] [--fact-id] [--chunk-id]` | Soft-delete. |
| `restore --db --entity-id` | Reverse a soft delete of an entity. |
| `export --db [--entity] [--format json\|markdown]` | Export the subgraph. |
| `serve [--db \| --home] [--port]` | Run the local visualizer. |
| `lores list\|create\|copy\|freeze\|reset --home [--name] [--from] [--to]` | Lore lifecycle. |
| `snapshot create\|list\|rollback --db [--seq] [--label]` | Point-in-time history. |
