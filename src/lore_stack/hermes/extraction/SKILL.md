---
name: lore-extraction
description: Extract structured lore (a LoreDelta) from a told story, guided by the operator, and route it to canon vs review.
version: 0.1.0
metadata:
  hermes:
    tags: [storytelling, lore, extraction]
    category: writing
    requires_toolsets: [terminal]
    config:
      - key: narrative_lore.db_path
        description: Path to the lore SQLite database
        default: "~/.hermes/local/lore-stack/data/lore.db"
        prompt: Narrative lore database path
---
# Lore Extraction

Turn a told story into structured lore for the lore-stack substrate, guided by the
operator, and route it in two tiers. **You — the model running this skill — are the
extractor.** lore-stack has no extraction model of its own; it just stores the JSON
you produce. Pin this skill to a capable model.

Read `references/contract.md` for the full `LoreDelta` JSON schema, the closed
relationship ontology, and a worked example. Follow it exactly — off-ontology
relationship edges are **rejected** at writeback.

## When the operator says "run extraction"

Extraction is operator-triggered and may be guided, e.g. *"run extraction — make
sure to get Boxwell, his profession, and where he lives; ignore Mirel."* Do this:

1. **Take the story text** just told. Optionally pull recent continuity first
   (`narrative-lore` → compile-context) so you reuse existing entity slugs instead
   of forking them.
2. **Apply the operator's guidance:**
   - *Focus* ("get X, his Y, where he lives"): make sure those entities and facts
     are extracted and correct. These are the operator's vouched items.
   - *Ignore* ("ignore Z"): do not extract Z at all.
3. **Build TWO `LoreDelta` JSON files** (same schema):
   - **direct.json** — *only the operator-named items*: the focus entities, their
     named facts, plus any entity needed as a relationship object. These go straight
     to canon.
   - **stage.json** — *everything else you legitimately found* in the story (other
     entities, relationships, attributes, chunks, open questions), minus anything
     the operator said to ignore. These go to the review queue.
   - Give the two deltas distinct `story_id`s (e.g. `<story>__direct`,
     `<story>__stage`) so both apply cleanly.
4. **Store them** through the `narrative-lore` skill:
   - direct → `ingest-delta … -Canon` (written canonical immediately)
   - stage  → `stage-delta …` (waits for the operator's review)
5. **Report** what landed in canon and what's waiting in the review inbox.

## Rules of thumb (full detail in references/contract.md)

- **Relationships are a closed set of 11** child-legible edges: `family_of`,
  `friends_with`, `against`, `mentors`, `serves`, `leads`, `belongs_to`,
  `lives_in`, `visits`, `wants`, `linked_to`. Anything else is rejected. Flip
  direction-reversing verbs: `X keeps/owns Y` → `Y belongs_to X`; `X taught_by Y` →
  `Y mentors X`.
- A fact about **one** thing (job, species, colour, name) is an **attribute**
  (`object_literal`), not an edge. Pure scenery (a part in a machine, honey in a
  tree) is **prose** — put it in the entity's summary, create no claim. *A lens does
  not have a social life.*
- Don't invent anything the story doesn't support. When unsure whether something is
  worth committing, **stage it** — never send a guess straight to canon.
