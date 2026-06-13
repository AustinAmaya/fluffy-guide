"""The LoreDelta extraction contract and writeback payloads.

LoreDelta is the single contract between the Extractor seam and the writeback
engine. Validation is strict (extra='forbid', bounded list sizes) because this
is a real boundary: extractor output is never trusted.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

EntityKind = Literal["character", "location", "item", "organization", "event", "concept"]
RetrievalMode = Literal["key", "semantic", "hybrid", "pinned"]
InsertionLane = Literal[
    "character_card", "world_info", "relationships", "open_hooks", "recent_continuity"
]

# Size bounds: an oversized delta is rejected at validation, never partially written.
MAX_ENTITIES = 200
MAX_CLAIMS = 500
MAX_CHUNKS = 200
MAX_TEXT = 20_000


class EntityUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=500)
    kind: EntityKind
    aliases: list[str] = Field(default_factory=list, max_length=50)
    summary: str = Field(max_length=MAX_TEXT)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_excerpt: str = Field(max_length=MAX_TEXT)


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_slug: str = Field(min_length=1, max_length=200)
    predicate: str = Field(min_length=1, max_length=200)
    object_slug: Optional[str] = Field(default=None, max_length=200)
    object_literal: Optional[str] = Field(default=None, max_length=MAX_TEXT)
    confidence: float = Field(ge=0.0, le=1.0)
    importance: Literal["high", "medium", "low"] = "medium"
    canonicality_hint: Literal["candidate", "soft", "motif", "uncertain"] = "candidate"
    evidence_excerpt: str = Field(max_length=MAX_TEXT)

    @model_validator(mode="after")
    def _exactly_one_object(self) -> "ClaimInput":
        if (self.object_slug is None) == (self.object_literal is None):
            raise ValueError("claim must set exactly one of object_slug or object_literal")
        return self


class ChunkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=MAX_TEXT)
    activation_keys: list[str] = Field(min_length=1, max_length=50)
    retrieval_mode: RetrievalMode = "hybrid"
    insertion_lane: InsertionLane
    priority: int = Field(default=100, ge=0, le=10_000)
    entity_slug: Optional[str] = Field(default=None, max_length=200)


class LoreDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    story_id: str = Field(min_length=1, max_length=200)
    story_title: str = Field(max_length=500)
    story_summary: str = Field(max_length=MAX_TEXT)
    entities: list[EntityUpsert] = Field(default_factory=list, max_length=MAX_ENTITIES)
    claims: list[ClaimInput] = Field(default_factory=list, max_length=MAX_CLAIMS)
    chunks: list[ChunkInput] = Field(default_factory=list, max_length=MAX_CHUNKS)
    open_questions: list[str] = Field(default_factory=list, max_length=100)


class WritebackReport(BaseModel):
    """Outcome of applying one LoreDelta (or a manual operation)."""
    model_config = ConfigDict(extra="forbid")
    story_id: str
    noop: bool = False
    entities_created: list[str] = Field(default_factory=list)
    entities_resolved: list[str] = Field(default_factory=list)
    entities_promoted: list[str] = Field(default_factory=list)
    aliases_added: int = 0
    claims_written: int = 0
    facts_created: list[str] = Field(default_factory=list)
    facts_promoted: list[str] = Field(default_factory=list)
    chunks_created: list[str] = Field(default_factory=list)
    adjudications_opened: list[str] = Field(default_factory=list)
