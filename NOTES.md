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
