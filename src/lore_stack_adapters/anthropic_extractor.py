"""Live Extractor adapter: story text -> schema-valid LoreDelta via the Claude API.

Implements the lore_stack.seams.extractor.Extractor protocol with no change to
the core. Uses structured outputs (client.messages.parse with the LoreDelta
Pydantic model), so the response is validated against the same contract the
FakeExtractor and the writeback engine already enforce.
"""
from lore_stack.models.delta import LoreDelta

EXTRACTION_SYSTEM_PROMPT = """\
You extract structured lore from a story for a conservative canon database.
Return a LoreDelta object. Rules:

- story_id: use the literal placeholder "story_pending" (the caller overrides it).
- entities: one entry per named character/location/item/organization/event/concept
  that the story actually establishes. slug is the lowercase-hyphenated form of the
  name (e.g. "Boxwell" -> "boxwell", "The Brambled Inn" -> "the-brambled-inn").
  Include surface aliases used in the text ("the clockmaker"). summary is one
  factual sentence. evidence_excerpt quotes the supporting text.
- claims: atomic (subject, predicate, object) assertions. Use object_slug when the
  object is one of the extracted entities, otherwise object_literal. Set exactly
  one of the two. confidence reflects how explicitly the text asserts it.
  canonicality_hint: "candidate" for stable facts, "soft" for incidental details,
  "motif" for recurring jokes/bits that must never become canon, "uncertain" for
  weakly grounded inferences. Never invent entities or facts without evidence.
- chunks: promptable memory units. Give each a title, a 1-3 sentence body,
  lowercase activation_keys, retrieval_mode "hybrid", and an insertion_lane:
  character_card (stable identity), world_info (places/setting), relationships,
  open_hooks (unresolved threads), recent_continuity (a "Previous story summary:"
  recap). Bind entity-specific chunks via entity_slug.
- open_questions: unresolved hooks as questions.
Extract only what the text supports; when in doubt, lower the confidence."""


class ExtractionError(Exception):
    pass


class AnthropicExtractor:
    """Extractor protocol implementation backed by claude-opus-4-8 structured outputs."""

    def __init__(self, model: str = "claude-opus-4-8", client=None) -> None:
        if client is None:
            import anthropic  # adapter-local dependency; the core never imports this

            client = anthropic.Anthropic()
        self._client = client
        self.model = model

    def extract(self, story_text: str, *, story_id: str) -> LoreDelta:
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": story_text}],
            output_format=LoreDelta,
        )
        if response.stop_reason == "refusal":
            raise ExtractionError("model declined to extract this story")
        delta = response.parsed_output
        if delta is None:
            raise ExtractionError(
                f"no parsed output (stop_reason={response.stop_reason!r})"
            )
        return delta.model_copy(update={"story_id": story_id})
