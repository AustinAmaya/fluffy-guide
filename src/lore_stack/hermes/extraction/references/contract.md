# LoreDelta extraction contract

The exact JSON you produce, the closed ontology you must stay within, and a worked
example. lore-stack validates this strictly (`extra="forbid"`): unknown fields are
rejected, and so are off-ontology relationship edges.

## The `LoreDelta` JSON shape

```json
{
  "story_id": "string (unique per delta)",
  "story_title": "string",
  "story_summary": "one or two factual sentences",
  "entities": [ EntityUpsert, ... ],
  "claims":   [ ClaimInput, ... ],
  "chunks":   [ ChunkInput, ... ],
  "open_questions": ["unresolved hooks as questions", ...]
}
```

### EntityUpsert — a named thing
```json
{
  "slug": "lowercase-hyphenated-name",      // identity; "The Brambled Inn" -> "the-brambled-inn"
  "display_name": "The Brambled Inn",
  "kind": "character|location|item|organization|event|concept",
  "aliases": ["the inn"],                    // surface names used in the text
  "summary": "one factual sentence (scenery/prose lives here)",
  "confidence": 0.0-1.0,
  "evidence_excerpt": "a short quote from the story supporting this"
}
```

### ClaimInput — one atomic (subject, predicate, object) fact
```json
{
  "subject_slug": "boxwell",
  "predicate": "profession",                 // an attribute id OR one of the 11 relationships
  "object_literal": "clockmaker",            // SET EXACTLY ONE of object_literal ...
  "object_slug": null,                       // ... or object_slug (another entity's slug)
  "confidence": 0.0-1.0,
  "importance": "high|medium|low",
  "canonicality_hint": "candidate|soft|motif|uncertain",
  "evidence_excerpt": "supporting quote"
}
```
- Use `object_slug` when the object is another entity (a relationship edge); use
  `object_literal` for a literal value (an attribute). Never both.
- `canonicality_hint`: `candidate` for stable facts; `soft` for incidental detail;
  `motif` for a recurring joke/bit that must never become canon; `uncertain` for a
  weakly grounded inference.

### ChunkInput — a promptable memory unit (authored prose)
```json
{
  "title": "Boxwell card",
  "body": "1-3 sentences of prose.",
  "activation_keys": ["boxwell", "clockmaker"],
  "retrieval_mode": "hybrid",
  "insertion_lane": "character_card|world_info|relationships|open_hooks|recent_continuity",
  "priority": 100,
  "entity_slug": "boxwell",                  // optional: bind to an entity
  "derived_from": [ {"subject_slug": "boxwell", "predicate": "profession"} ]  // optional fact links
}
```
- `recent_continuity` chunks should read "Previous story summary: …".
- `derived_from` (optional) links a chunk to the facts it paraphrases; if such a
  fact is later deprecated/superseded the chunk is auto-flagged stale.

## The closed relationship ontology (the only edges allowed)

A **relationship** is an `object_slug` claim (edge to another entity). There are
exactly eleven. Anything else is rejected at writeback.

| id | meaning | direction (subject → object) | aliases that normalize in |
|----|---------|------------------------------|----------------------------|
| `family_of` | "they're family" | symmetric | parent_of, child_of, sibling_of, married_to, related_to |
| `friends_with` | "friends / like each other" (also trusts, loves) | symmetric | friend_of, likes, loves, trusts |
| `against` | "doesn't get along / is the baddie to" | disliker → disliked | enemy_of, rival_of, resents, opposes, afraid_of |
| `mentors` | "teaches & looks after" | teacher → student | teacher_of, teaches, guides, trains |
| `serves` | "works for / helps / apprentice of" | helper → leader | apprentices_to, works_for, assistant_to, taught_by |
| `leads` | "is in charge of / boss of" | leader → led | leader_of, rules, commands, queen_of, captain_of |
| `belongs_to` | "this thing/pet is someone's" | thing → owner | owned_by, pet_of, property_of |
| `lives_in` | "lives/stays in a place" (single — you can move) | resident → place | resides_in, dwells_in, located_in, from |
| `visits` | "goes to / travels to a place" | visitor → place | travels_to, goes_to, arrives_at |
| `wants` | "really wants / is after a thing or person" | wanter → wanted | covets, seeks, desires, chases |
| `linked_to` | catch-all (only if none of the above fit) | symmetric | connected_to, tied_to, knows |

**Direction matters and aliases cannot reverse it — flip at authoring:**
`X keeps/owns/possesses/runs Y` → `Y belongs_to X`; `X taught_by Y` → `Y mentors X`.

**Special behavior:** `lives_in` is single (a new home *supersedes* the old);
`visits` is episodic (story-anchored, never hardens into permanent canon).

## Attributes (open vocabulary) — facts about ONE entity, as `object_literal`

`profession`, `species`, `carries`, `has_trait`, `claimed_title`, … — any literal
fact about a single entity. These don't count against the closed set.

## Edge vs attribute vs prose

- **Edge** (relationship): ties *two* named beings that could change later (who
  loves whom, who's family, who's the baddie, who lives where, whose toy, who leads
  whom). → `object_slug`, one of the 11.
- **Attribute**: a fact about *one* thing — its job, species, colour, name. →
  `object_literal`.
- **Prose / scenery**: a part fitted into a machine, honey in a tree, a bell in a
  room. → put it in the entity's `summary`; create **no claim**.

## The two-tier split (direct vs stage)

- **direct.json** = exactly what the operator named ("get Boxwell, his profession,
  where he lives") + entities those facts need. Applied to **canon** immediately.
- **stage.json** = everything else you legitimately found (minus anything the
  operator said to ignore). Held for **review**.

## Worked example

Source text: *"By dusk, Boxwell — a travelling clockmaker who lodges above the
bakery — reached the rain-soaked Brambled Inn, where the innkeeper Mirel kept the
hallway clock that had stopped for twenty years."*

Operator: *"run extraction — get Boxwell, his profession, and where he lives; ignore
Mirel."*

**direct.json** (operator-named → canon):
```json
{
  "story_id": "cedar_case__direct",
  "story_title": "The Cedar Case",
  "story_summary": "Boxwell, a travelling clockmaker who lodges above the bakery.",
  "entities": [
    {"slug": "boxwell", "display_name": "Boxwell", "kind": "character",
     "aliases": ["the clockmaker"], "summary": "A travelling clockmaker.",
     "confidence": 0.97, "evidence_excerpt": "Boxwell — a travelling clockmaker"},
    {"slug": "the-bakery", "display_name": "The Bakery", "kind": "location",
     "aliases": [], "summary": "Where Boxwell lodges, above the shop.",
     "confidence": 0.85, "evidence_excerpt": "who lodges above the bakery"}
  ],
  "claims": [
    {"subject_slug": "boxwell", "predicate": "profession", "object_literal": "clockmaker",
     "confidence": 0.97, "importance": "high", "canonicality_hint": "candidate",
     "evidence_excerpt": "a travelling clockmaker"},
    {"subject_slug": "boxwell", "predicate": "lives_in", "object_slug": "the-bakery",
     "confidence": 0.85, "importance": "high", "canonicality_hint": "candidate",
     "evidence_excerpt": "who lodges above the bakery"}
  ],
  "chunks": [], "open_questions": []
}
```

**stage.json** (the rest → review; Mirel excluded per "ignore Mirel"):
```json
{
  "story_id": "cedar_case__stage",
  "story_title": "The Cedar Case",
  "story_summary": "Boxwell reached the Brambled Inn at dusk in the rain.",
  "entities": [
    {"slug": "the-brambled-inn", "display_name": "The Brambled Inn", "kind": "location",
     "aliases": ["the inn"], "summary": "A roadside inn whose hallway clock stopped twenty years ago.",
     "confidence": 0.9, "evidence_excerpt": "reached the rain-soaked Brambled Inn"}
  ],
  "claims": [
    {"subject_slug": "boxwell", "predicate": "visits", "object_slug": "the-brambled-inn",
     "confidence": 0.92, "importance": "medium", "canonicality_hint": "candidate",
     "evidence_excerpt": "reached the rain-soaked Brambled Inn"}
  ],
  "chunks": [
    {"title": "The Brambled Inn", "body": "A weathered roadside inn whose hallway clock has been stopped for twenty years.",
     "activation_keys": ["brambled inn", "hallway clock"], "retrieval_mode": "hybrid",
     "insertion_lane": "world_info", "priority": 800, "entity_slug": "the-brambled-inn"}
  ],
  "open_questions": ["Why has the Brambled Inn's hallway clock been stopped for twenty years?"]
}
```

Note: "the hallway clock that had stopped for twenty years" is **prose** — it lives
in the inn's summary/chunk, not as a claim (a clock has no social life). Mirel and
her `belongs_to`/`friends_with` edges are dropped entirely because the operator said
to ignore her.
