# Decisions log

One entry per non-obvious decision; one-line summaries up top.

- [Environment: hook python is the Hermes venv](#environment) — project venv instead; hook needs a one-line change (pending operator).
- [A5: FTS external-content fixes](#a5-fts) — draft schema's FTS table was unscannable and corruptible.
- [Priority-first lane packing](#lane-packing) — invariant 7 says dropped *by priority*; score gates candidacy.
- [FakeEmbedder stopword filter](#stopwords) — function words made unrelated chunks outrank related ones.
- [No frozen-vector fixture files](#frozen-vectors) — the FakeEmbedder *is* the freezing mechanism.
- [Canonization thresholds](#thresholds) — 0.7 soft floor; promotion needs ≥2 stories, ≥0.9, candidate hint, no active sibling.
- [Recency without clocks](#recency) — story insertion order replaces wall-clock decay.
- [Deprecation cascade scope](#cascade) — subject+object facts and entity-owned chunks; other entities' prose may still mention the name.
- [Adjudication resolution UI not built](#adjudication) — read-only conflict list; manual edit supersedes.
- [PowerShell `-Db` parameter conflict](#ps-db) — renamed to `-DbPath` in the skill stub.
- [Verifier round: budget joiners, object namespaces, restore](#verifier-round) — three fixes from independent fresh-context review.
- [Phase 2 adapter placement](#phase2) — separate `lore_stack_adapters` top-level package; structured outputs against the existing contract.
- [Synthesized fact-cards for query targets](#fact-cards) — entities named in a query always get identity representation, even without an authored card chunk.
- [Multi-lore = one DB file per lore](#multi-lore) — a lore home directory; isolation by construction, selected per request.
- [Retrieval precision (Phase 3 A)](#p3-retrieval) — stopword-free FTS, semantic noise floor, identity cards for targets only.
- [Snapshots auto-fire via the connection (Phase 3 B)](#p3-snapshots) — LoreConnection.auto_snapshot, not per-call-site.
- [Registry cardinality + confidence demotion (Phase 3 C/D)](#p3-registry) — single vs multi conflicts; reviewed path drops the confidence gate.
- [Merge suggestions use the FakeEmbedder's word-overlap (Phase 3 E)](#p3-merge) — catches reordering dups, not morphological ones.
- [Context format + lore-continuity fallback (Phase 4)](#p4-context) — primary/secondary headings; no-match in a populated lore reliably returns continuity.

## Environment: hook python is the Hermes venv {#environment}
`python` on PATH resolves to `...\hermes\hermes-agent\venv` — it has pytest and
pydantic but no pip, flask, or hypothesis. Installing into the Hermes runtime
was rejected as invasive; lore-stack uses its own `.venv`. The Stop hook
(`.claude/hooks/deterministic-gate.ps1`) therefore needs its own documented
one-liner (use `.venv\Scripts\python.exe` when present). Until then the hook
exits 0 on collection errors (pytest exit 2), so it cannot falsely block — but
it also cannot gate. **Why:** keep the Hermes runtime pristine. **How to
apply:** the hook's comment already prescribes the venv substitution.

## A5: FTS external-content fixes {#a5-fts}
The tech-stack report's FTS5 table declared a column `activation_keys` that does
not exist in the content table `lore_chunks`; external-content FTS reads
original values from same-named content columns, so any full scan (e.g.
`COUNT(*)`) raised `no such column`. Renamed the FTS column to
`activation_keys_json` and made all three triggers pass raw column values
(with `COALESCE(title,'')`) so the `'delete'` command receives exactly what was
inserted — required, because status flips fire the UPDATE trigger on every
soft delete. Found by the invariant suite's FTS-sync check.

## Priority-first lane packing {#lane-packing}
Invariant 7 reads "over-budget chunks are dropped by priority". The fused score
(name/alias/FTS/cosine/...) decides whether a chunk is a candidate at all;
within a lane, packing under budget pressure orders by (priority desc, score
desc, chunk_id). Rationale: bm25 rank jitter between near-identical chunks made
score-first packing effectively arbitrary, while priority is author-controlled
and matches the SillyTavern inclusion-priority concept the column came from.

## FakeEmbedder stopword filter {#stopwords}
Pure token-hash embeddings weighted "the"/"a"/"story" equally with content
words, so a continuity chunk sharing only function words outranked the Boxwell
card for "the travelling clockmaker". A small fixed stopword list (with
fallback to unfiltered tokens when everything is a stopword) keeps the fake
deterministic while making shared *content* tokens dominate cosine.

## No frozen-vector fixture files {#frozen-vectors}
The test docs call for frozen embedding fixtures. Not needed here: the
FakeEmbedder derives vectors purely from content (sha256-seeded token vectors,
summed, L2-normalized), so identical text always yields identical vectors —
the embedder itself is the freezing mechanism.

## Canonization thresholds {#thresholds}
Claim → soft fact requires confidence ≥ 0.7. Promotion to canonical requires:
existing soft fact corroborated from a *different* story (first ≠ current),
max confidence ≥ 0.9, the corroborating claim hinted `candidate` (a `soft`
hint corroborates but never promotes), and no active contradicting sibling on
the same (subject, predicate). `uncertain` hints store the claim only.
Contradiction with a *soft* fact lets both coexist as soft (promotion blocked);
only contradiction with *canon* opens adjudication.

## Recency without clocks {#recency}
The spec's scorer includes recency decay. Wall-clock time would break byte
determinism, so recency = story rowid / max rowid (insertion order). Compiled
output contains no timestamps anywhere.

## Deprecation cascade scope {#cascade}
`deprecate_entity` flips the entity, every fact where it is subject *or*
object, and every chunk *owned* by it (`lore_chunks.entity_id`). Chunks owned
by other entities may still mention the name in prose (e.g. Mirel's
relationship note mentions Boxwell) — that is their lore, not the deprecated
entity's, and deprecating it would destroy another entity's history.

## Adjudication resolution UI not built {#adjudication}
The spec requires creating adjudication items and listing them (visualizer
conflict panel, `inspect conflicts`). Resolution workflows beyond that are out
of the §2 fence; an operator resolves a conflict authoritatively via manual
edit (which deprecates the loser and records a manual source).

## PowerShell `-Db` parameter conflict {#ps-db}
A script param named `-Db` collides with the alias of the common `-Debug`
parameter and fails at parse time. The Hermes stub uses `-DbPath`.

## Verifier round: budget joiners, object namespaces, restore {#verifier-round}
A fresh-context verifier audit confirmed the contract and surfaced three nits,
all fixed:
1. **Budget joiner accounting** — the packing loop now charges every joining
   newline (header newline, lane separator, body joiners, final trailer), so
   `token_estimate(emitted_text) <= budget` holds exactly, not just the
   internal accounting (per-piece ceils sum to at least the whole-text ceil).
2. **Object namespaces** — `_object_norm` prefixes `ent:`/`lit:` so a literal
   that textually equals an internal entity id can never corroborate or
   contradict an entity-reference fact.
3. **Restore + zombie rejection** — manual edits on deprecated entities are
   rejected ("restore first"); `restore_entity` (library/CLI/API) revives the
   entity (as provisional) and its owned chunks. Facts deliberately stay
   deprecated history: blanket revival could resurrect values superseded by
   manual edits; the operator re-asserts specific facts via `edit-fact`.
Known accepted behaviors: counter-derived ids (`cmp_N`, `src_manual_N`) are
safe because nothing hard-deletes; a contradiction whose object slug cannot be
resolved is parked as a `needs_review` claim without an adjudication item (the
conflict is not yet established).

## Synthesized fact-cards for query targets {#fact-cards}
Found via a real multi-entity query ("boxwell and mirel at whitmoor"): all
targets resolved and Mirel's relationship chunk was retrieved, but she had no
headline card because extraction never authored one — the compiler only
assembled stored prose chunks, so an entity's *facts* had no path into the
briefing. Fix: for each query target with no authored chunk in its card lane
(characters → character_card, everything else → world_info), the compiler
synthesizes a deterministic card from the entity summary + active facts (soft
facts marked "[unconfirmed]", motifs excluded), priority 950 so authored cards
outrank it, subject to normal budgets, traced as `synthesized_from_facts`.
Synthesized cards are regenerated from live facts each compile, so they can
never go stale the way authored chunks can (see the ontology spec, C7).

## Multi-lore = one DB file per lore {#multi-lore}
Testing lores and a production lore are separate `<name>.db` files in a "lore
home" directory — isolation comes from the filesystem, not from tenancy columns
inside one database (no risk of a missing WHERE clause leaking test canon into
production). Home mode: `serve --home DIR`, every API request selects
`?lore=<name>` (strict name allowlist blocks path traversal; unknown → 404),
`/api/lores` lists/creates. Single-db mode unchanged. The frontend shows a
dropdown + "+ lore" only when home mode is detected.

## Retrieval precision (Phase 3 A) {#p3-retrieval}
Operator hit "tell a story about mirel visiting harrow fen" and got Boxwell's
card. Three causes, three fixes: (1) the default FTS5 tokenizer indexes function
words, so "a"/"about" in the query matched nearly every chunk body — FTS now
drops stopwords (shared `STOPWORDS` set with the embedder). (2) Unrelated
256-d hash vectors sit at ±0.06 cosine; that noise counted as a semantic signal
— added a 0.12 floor. (3) Boxwell's *identity card* was pulled in by graph
proximity (he's one hop from Mirel). Identity cards (character_card lane) now
require the entity to be a query target or earn a direct hit; graph expansion
still legitimately surfaces relationship/world/hook/continuity chunks. The
golden Boxwell query was unaffected (he's a target there).

## Snapshots auto-fire via the connection (Phase 3 B) {#p3-snapshots}
The "snapshot before every mutation, from CLI/API/UI, without each call site
remembering" requirement is met by a `LoreConnection(sqlite3.Connection)`
subclass carrying an `auto_snapshot` flag (the base class can't hold attributes).
`connect(path, auto_snapshot=True)` is set once at the CLI/API boundary; the
mutating engine functions call `maybe_snapshot(conn, op)` which is a no-op unless
the flag is set — so tests (default off) and read paths pay nothing, and reads
never snapshot even on an auto_snapshot connection because they don't call a
mutating function. Snapshots are whole-file via the SQLite online backup API
(corpus is tiny). `maybe_snapshot` raises if called mid-transaction (the backup
API would otherwise deadlock — verifier finding B-1).

## Registry cardinality + confidence demotion (Phase 3 C/D) {#p3-registry}
Two engine-policy shifts. **Cardinality:** the contradiction check runs only for
single-valued predicates; multi-valued ones (carries, visits) coexist per-value.
This changed test_double_contradiction from 2 adjudications to 1 (carries=multi
now coexists) — the correct fix for implicit commitment C1. **Confidence
demotion:** the reviewed path (`apply_delta(reviewed=True)`, used by staging)
drops the confidence gate entirely — a human's approval is a stronger signal than
a model's self-graded confidence, so approved claims always form soft facts and
promotion is corroboration-count only. The legacy path keeps 0.7/0.9 so the A–J
suite is untouched. The engine<->registry import cycle is broken by lazy
in-function imports in engine (registry imports `normalize` from engine at module
level; engine imports registry only inside functions).

## Merge suggestions use the FakeEmbedder's word-overlap (Phase 3 E) {#p3-merge}
The aggressive (cosine ≥ 0.5) duplicate detector is calibrated to what the
FakeEmbedder actually measures: token-hash overlap. It catches reordering /
filler-word duplicates ("cedar tool case" ~ "a cedar case of tools" = 0.67) but
NOT morphological ones ("clockmaker" ~ "clock maker" = 0.01 — different single
tokens, no subword similarity). A real embedder would catch both; this is a known
limit of the deterministic fake, acceptable because suggestions only ever open a
review item (never auto-merge). Stored as a new `merge_suggestion` adjudication
kind (migration 0004 rebuilds the table — SQLite can't ALTER a CHECK).

## Context format + lore-continuity fallback (Phase 4) {#p4-context}
Two coupled changes from the "make the compiled context legible" request.
**Format:** the block now leads with a primary `=== CONTEXT FOR: <entities> ===`
header (entities resolved from the deterministically-ordered targets) and renders
each lane as a secondary `## Heading`; the budget loop reserves the primary
header's tokens on the first emitted piece (same trick as the trailing-newline
reservation) so `token_estimate(text) <= budget` still holds. **Continuity
fallback:** the operator's model is "the selected lore is the unit of
connection" — within an existing lore, a query naming no known entity should
return recent continuity as connective tissue. The old behavior only returned it
when query words *incidentally* overlapped a continuity chunk (semantic/FTS),
which was unreliable. Fix: in `gather_candidates`, when there are no targets,
recent_continuity chunks are unconditionally relevant (reason `lore_continuity`).
An empty lore has no such chunks, so a new lore still returns nothing — the
"clean slate" path needs no special handling.

## Phase 2 adapter placement {#phase2}
The live extractor lives in a *separate top-level package*
(`lore_stack_adapters`) rather than a `lore_stack` subpackage, so directive 4
("the core imports nothing model-specific") holds physically, not just by
convention — importing `lore_stack` can never pull `anthropic`. The adapter
uses `client.messages.parse` with the existing `LoreDelta` Pydantic model as
the output format: schema enforcement happens server-side and validation
client-side against the exact same contract the writeback engine enforces, so
no repair layer is needed. Pydantic field constraints unsupported by the API's
schema subset (min/max lengths, ge/le) are stripped by the SDK and validated
client-side. Refusal/empty responses raise `ExtractionError`; nothing touches
the DB on failure (validation precedes writeback). Parity test ran live and
passed on 2026-06-12.
