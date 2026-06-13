# CLAUDE.md — lore-stack

Orientation for any agent (local or cloud, e.g. an ultraplan session) working in
this repository. Read this first.

## What this is

**lore-stack** is a deterministic, model-agnostic lore-memory substrate for
narrative continuity (its working purpose: bedtime stories for one six-year-old).
It ingests structured lore (`LoreDelta`), stores it in SQLite, canonizes
conservatively, retrieves with FTS5 + exact cosine, and compiles a bounded,
lane-based context block. **No LLM and no agent are in the loop** — the only two
seams a model could plug into are the Extractor (`story → LoreDelta`) and the
Embedder (`text → vector`), both Protocols in `src/lore_stack/seams/` with
deterministic fakes as the default.

## The cardinal rule: determinism

The project's whole safety promise is that the deterministic gate is **green and
byte-for-byte repeatable**:

```bash
.venv/Scripts/python -m pytest -m "not model"     # Windows; use bin/ on POSIX
```

Before finishing any change, this must pass. It includes a **golden-file** test
(`tests/fixtures/golden/golden_context_boxwell.txt`) and byte-determinism tests:
if you change ingestion, canonization, retrieval, or compilation in a way that
shifts compiled output, regenerate the golden deliberately and confirm it's
intended. IDs are content-derived; compiled text contains **no timestamps**
(recency uses story insertion order). Tables are SQLite `STRICT`.

Live-model tests carry the `model` marker and are excluded from the gate. Do not
add non-deterministic behavior (clocks, RNG, network) to the core.

## Layout

```
src/lore_stack/
  db/            schema.sql (0001) + migration_000{2..6}.sql; predicates.json (registry seed)
  models/        delta.py — the LoreDelta / ClaimInput / WritebackReport contracts (Pydantic, strict)
  writeback/     engine.py — apply_delta, conservative canonization, manual edits, soft deletes
  registry.py    predicate registry: alias normalization + the closed-relationship guard
  retrieval/     fts.py, cosine.py, fusion.py — FTS5 + exact cosine + fusion/targets
  compiler/      compile.py — bounded lane-based context compiler (writes a compiler_runs audit row)
  staging.py     review-before-commit queue (stage → review → downselect → apply)
  snapshots.py   per-lore point-in-time snapshots + rollback
  frozen.py      frozen baselines (DB + history) and full hard reset
  lores.py       lore lifecycle (create/copy)
  visualizer/    Flask JSON API + single-file offline frontend
  hermes/        two Hermes skills: extraction/ (LLM extraction instructions) + storage/ (CLI shell)
  cli.py         every operation is exposed here; the UI and skill are shells over the same library
examples/lores/  committed seed worlds (harrow-hollow, clockwork-coast, winnie-the-pooh) — ship with the package
tests/           the deterministic gate; fixtures under tests/fixtures/
docs/USER_GUIDE.md   the task-oriented "how to do everything"
```

## Load-bearing invariants (don't break these)

- **Provenance is mandatory.** Every fact carries either extracted lineage
  (`source_claim_id`) or a `manual` source — a DB CHECK enforces it.
- **All deletes are soft.** Status flips to `deprecated`; rows survive and stay
  recoverable. Hard `DELETE` lives only in test teardown.
- **Canon is conservative.** Promotion needs ≥2 distinct stories; a contradiction
  of canon opens an adjudication item and never overwrites. Motifs never promote;
  episodic predicates (`visits`) never promote. A new value for a single-valued
  `state` fact (`lives_in`) opens a *supersession* proposal, not a contradiction.
- **Relationships are a closed set of 11** child-legible predicates (`range:
  entity`): `family_of, friends_with, against, mentors, serves, leads, belongs_to,
  lives_in, visits, wants, linked_to`. An off-vocabulary entity-object claim is
  rejected at writeback; an operator edit can't mint a new edge type. Attributes
  (`range: text`) stay an open vocabulary. The boundary: edge (two beings) vs
  attribute (one thing) vs prose (scenery — no fact). See `docs/USER_GUIDE.md`.
- The `tests/` invariant suite (`invariant_checks.assert_invariants`) encodes the
  rest; property tests fuzz it. Keep them green.

## Running things

```bash
# install (editable, with dev extras)
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"

lore-stack init-db --db lore.db
lore-stack ingest-delta --db lore.db --file examples/lores/winnie-the-pooh/01_pooh_and_some_bees.delta.json
lore-stack compile-context --db lore.db --query "Tell a story with Pooh"
lore-stack serve --home lores            # visualizer with a lore switcher
powershell -ExecutionPolicy Bypass -File demo.ps1   # seed + freeze the demo worlds, then serve
```

## Conventions

- This is **plain library + CLI**, dependency-light (pydantic, flask). The core
  imports no model SDK. Live adapters live in a separate `lore_stack_adapters`
  package the core never imports.
- Match the existing code's style: terminal-legible docstrings that state the
  *why*, content-derived IDs, small functions.
- The deterministic gate is the contract. If you can't keep it green, stop and
  explain why rather than weakening a test.
