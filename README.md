# lore-stack

A **deterministic, model-agnostic lore-memory substrate**. It ingests structured
lore (`LoreDelta`), stores it safely in SQLite, retrieves it (FTS5 keyword/alias
matching + exact-cosine semantic search), and compiles a bounded, lane-based
context block. The whole stack runs and validates **with no LLM and no agent
anywhere in the loop**.

```
story text ──▶ [Extractor seam] ──▶ LoreDelta ──▶ [Writeback + Canonization] ──▶ SQLite
query ──────▶ [Embedder seam] ─────────────────▶ [Retrieval: FTS5 + cosine] ──▶ [Context Compiler]
                                                          │
                              [CLI] / [Local web visualizer] / [Hermes skill stub]
```

The only two places a model could ever plug in are the **Extractor**
(`story text → LoreDelta`) and the **Embedder** (`text → normalized vector`).
Both are Protocol interfaces in `lore_stack/seams/`; Phase 1 ships deterministic
fakes (`FakeExtractor`: checksum-keyed frozen deltas; `FakeEmbedder`:
feature-hashed token vectors). The core imports no model SDK, no agent, and
nothing Hermes-specific.

## Quick start

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # or bin/ on POSIX

lore-stack init-db --db lore.db
lore-stack ingest-delta --db lore.db --file tests/fixtures/stories/boxwell_story_01.delta.json
lore-stack compile-context --db lore.db --query "Tell another story with Boxwell"
lore-stack serve --db lore.db          # local visualizer at http://127.0.0.1:8377
```

Other commands: `ingest-story` (FakeExtractor over story+delta fixture pairs),
`inspect entity|conflicts|motifs|stories`, `edit-fact` (authoritative manual
edit), `deprecate` (soft delete), `export` (subgraph JSON/markdown),
`stage-story` + `stage list|show|apply|discard` (review workflow), and
`snapshot create|list|rollback` (point-in-time history).

## Review before commit (the primary ingestion path)

Automatic extraction tends to over-produce. So the main path is **extract →
review → downselect → apply**: an extracted `LoreDelta` is *staged* (writes
nothing to the lore), the operator reviews it in the visualizer's inbox,
unchecks unwanted items, and applies the selected subset.

```bash
lore-stack stage-story --db lore.db --file story.md --fixtures tests/fixtures/stories
lore-stack stage list --db lore.db
lore-stack stage apply --db lore.db --id stg_000001 --selection '{"entities":[0],"claims":[0]}'
```

On this reviewed path a human's approval replaces the confidence gate: approved
claims always form soft facts, and promotion to canonical is corroboration-count
only (≥2 distinct stories). The legacy `ingest-delta`/`ingest-story` path keeps
the original 0.7/0.9 confidence thresholds.

## Predicate registry (controlled vocabulary)

`db/predicates.json` seeds a registry that turns free-text predicates into a
governed ontology. Each predicate declares a **cardinality** (single vs
multi-valued), a **persistence** class, and **aliases**. Effects: `occupation`
normalizes to `profession` so synonyms corroborate; multi-valued predicates
(`carries`, `visits`) coexist instead of falsely conflicting; unregistered
predicates can form soft facts but never auto-canonize; operator manual edits
auto-register their predicate.

## Snapshots & merge suggestions

Every mutating operation auto-snapshots the lore first; `snapshot list` / the
visualizer History panel offer one-click rollback (itself undoable). When a new
soft fact's value is embedding-similar (cosine ≥ 0.5) to an existing value on the
same subject+predicate, a **merge suggestion** opens — never auto-merged; the
operator picks which value to keep.

## The deterministic gate

```bash
.venv/Scripts/python -m pytest -m "not model"
```

This must be fully green and byte-for-byte repeatable before any live-model
work. It covers: tests A–J (bootstrap, ingest, corroboration, alias resolution,
relationships, contradiction, keyed retrieval, semantic retrieval, budget
enforcement, Hermes stub), the §5.2 invariant suite, Hypothesis property tests
(derandomized), adversarial inputs, golden-file and byte-determinism tests, and
the visualizer API. Live-model tests carry the `model` marker and are excluded.

## Schema: base + amendments

`src/lore_stack/db/schema.sql` is migration 0001, plus later migrations 0002–0004
(`db/migration_000*.sql`) layered on top. Amendments A1–A5 are in 0001; A6 (the
predicate registry) is 0002; A7 (staging) is 0003; the `merge_suggestion`
adjudication kind is 0004. The migration runner applies them in order and seeds
the registry idempotently; a pre-existing 0001-only database upgrades cleanly
(tested). The 0001 amendments:

- **A1 — `facts.status` gains `'motif'`.** Recurring jokes are stored and
  retrievable but are never asserted canon and never auto-promoted.
- **A2 — mandatory provenance.** `facts.manual_source_id` records human
  authorship; a table CHECK requires every fact to carry either extracted
  lineage (`source_claim_id`) or a `manual` source. No orphan facts.
- **A3 — `lore_chunks.status` gains `'deprecated'`.** All deletes are soft
  status flips; rows survive and remain recoverable. Hard DELETE exists only in
  test teardown.
- **A4 — unique partial index on `sources.checksum`.** Re-applying a delta with
  an already-seen checksum is a detectable no-op (idempotent ingest).
- **A5 — FTS5 external-content fixes.** The FTS column set must mirror
  content-table column names (`activation_keys_json`, not the draft's
  non-existent `activation_keys`), and the delete/update triggers must feed the
  FTS `'delete'` command the exact values inserted (raw column values, COALESCE
  on nullable title) — otherwise full scans fail and updates corrupt the index.

## Canonization policy (conservative by design)

| Situation | Outcome |
|---|---|
| First mention | provisional entity, candidate claim; soft fact at confidence ≥ 0.7 |
| Corroboration from ≥ 2 distinct stories at confidence ≥ 0.9 | fact promoted to `canonical` |
| Claim contradicts a canonical fact | open `adjudication_queue` item; canon untouched |
| Motif-hinted claim | `motif` fact; never canonized |
| Human edit via visualizer/CLI | immediately `canonical` with `manual` source, **bypasses adjudication**; prior value preserved as deprecated history |
| Delete (entity/fact/chunk) | soft `deprecated` flip; cascades from entity to its facts and chunks; retrieval ignores it |

The two human-authoritative carve-outs (manual edit, soft delete) are the only
sanctioned exceptions to normal canonization, and both are unit-tested as such.

## Lores — first-class, isolated worlds

A *lore* is a self-contained world: one SQLite database, fully isolated from
every other lore (nothing is shared between them). Lores are a **functional
feature, not just test scaffolding** — the lore you select is the unit of
narrative connection:

- **Working in an existing lore**, a new story connects to that world. Even a
  query naming a character not yet in the lore returns the lore's recent
  continuity as connective tissue — so a brand-new character can be woven into
  existing threads (you might discover, mid-story, that the newcomer is an old
  character's cousin).
- **Creating a new lore** is how you say "clean slate, no connections." A fresh
  lore is empty, so a story started there returns nothing until you populate it.

Point the server at a **lore home** directory to keep several worlds and switch
between them in the UI (dropdown + "+ lore"):

```bash
lore-stack lores create --home lores --name middlemarsh
lore-stack lores create --home lores --name clockwork-coast
lore-stack serve --home lores
```

Every CLI command targets a lore via `--db lores/<name>.db`; every API call in
home mode selects one via `?lore=<name>`. `serve --db one.db` still works for
single-lore use. `demo.ps1` seeds three example worlds at increasing size
(`test-boxwell` ~5 nodes, `harrow-hollow` ~10, `clockwork-coast` ~20).

## The visualizer

`lore-stack serve --db lore.db` runs a local Flask JSON API plus a single-file,
fully offline frontend (vanilla JS + SVG force graph — no CDN): entity graph
colored by kind, fact panels with provenance drilldown, open-conflict list,
motif view, retrieval inspector, compiled-context preview with per-chunk
traces, JSON export, and authoritative write-back (edit = canonical manual
fact; delete = soft deprecate).

## Determinism notes

- Compiled output contains no timestamps; recency scoring uses story insertion
  order, never a clock.
- All IDs are content-derived; identical inputs produce identical DB states
  (timestamps aside) and **byte-identical** compiled contexts.
- Token estimation is `ceil(len(text)/4)`. Lane budgets: character_card 400,
  world_info 350, relationships 250, open_hooks 250, recent_continuity 450
  (global 1700, CLI-overridable). Over-budget chunks are dropped whole, by
  priority — never truncated mid-fact.

## Hermes integration

One thin skill stub (`src/lore_stack/hermes/`) shells into the CLI. Hermes is a
downstream consumer, not the owner; nothing in the core imports it.

## Phase 2 (live adapter)

Exactly one seam is live: the **Extractor**, as
`lore_stack_adapters.anthropic_extractor.AnthropicExtractor` — a separate
top-level package the core never imports. It calls `claude-opus-4-8` with
structured outputs (`client.messages.parse` against the same `LoreDelta`
Pydantic contract), behind the unchanged `Extractor` protocol. Install with
`pip install -e ".[anthropic]"`; requires `ANTHROPIC_API_KEY`. The parity test
(`tests/test_phase2_parity.py`, marker `model`) feeds a fixed story through the
live path and asserts the same DB state shape as test B plus the full invariant
suite. The fakes remain the default everywhere; `pytest -m "not model"` is
unaffected.
