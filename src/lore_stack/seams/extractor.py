"""Extractor seam: story text -> LoreDelta.

This is one of exactly two places a model could ever plug in. Phase 1 ships
only the deterministic FakeExtractor; real adapters live outside the core.
"""
import hashlib
import json
from pathlib import Path
from typing import Protocol

from lore_stack.models.delta import LoreDelta


def story_checksum(story_text: str) -> str:
    return hashlib.sha256(story_text.encode("utf-8")).hexdigest()


class Extractor(Protocol):
    def extract(self, story_text: str, *, story_id: str) -> LoreDelta: ...


class FakeExtractor:
    """Returns pre-registered LoreDeltas keyed by the story text's sha256. No I/O, no model."""

    def __init__(self) -> None:
        self._registry: dict[str, LoreDelta] = {}

    def register(self, story_text: str, delta: LoreDelta) -> None:
        self._registry[story_checksum(story_text)] = delta

    @classmethod
    def from_pairs(cls, pairs: list[tuple[Path, Path]]) -> "FakeExtractor":
        """Build from (story_file, delta_json_file) pairs."""
        fake = cls()
        for story_path, delta_path in pairs:
            story_text = Path(story_path).read_text(encoding="utf-8")
            delta = LoreDelta.model_validate(
                json.loads(Path(delta_path).read_text(encoding="utf-8"))
            )
            fake.register(story_text, delta)
        return fake

    @classmethod
    def from_fixture_dir(cls, directory: Path) -> "FakeExtractor":
        """Pair every <name>.md story with its <name>.delta.json sibling."""
        directory = Path(directory)
        pairs = []
        for story_path in sorted(directory.glob("*.md")):
            delta_path = story_path.with_suffix(".delta.json")
            if not delta_path.exists():
                raise FileNotFoundError(f"no delta fixture for story {story_path.name}")
            pairs.append((story_path, delta_path))
        return cls.from_pairs(pairs)

    def extract(self, story_text: str, *, story_id: str) -> LoreDelta:
        checksum = story_checksum(story_text)
        if checksum not in self._registry:
            raise KeyError(
                f"FakeExtractor has no registered delta for story checksum {checksum[:12]}…"
            )
        return self._registry[checksum].model_copy(update={"story_id": story_id})
