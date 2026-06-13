"""Embedder seam: text -> L2-normalized vector.

Phase 1 ships only the FakeEmbedder: deterministic feature-hashing over tokens.
Each token maps to a pseudo-random unit vector derived from its sha256, token
vectors are summed and the result L2-normalized. Texts sharing tokens get high
cosine similarity, which makes semantic retrieval fully deterministic offline.
"""
import hashlib
import math
import re
import struct
from typing import Protocol

_TOKEN_RE = re.compile(r"[a-z0-9']+")

# Function words carry no lore semantics; without this filter, chunks sharing
# only "the"/"a"/"story" with a query can outrank genuinely related chunks.
# Shared with retrieval.fts so keyword and semantic passes agree on what is noise.
STOPWORDS = frozenset(
    "a an and are as at about been be but by for from had has have he her his is it its"
    " of on or she that the their them they this to was were will with".split()
)
_STOPWORDS = STOPWORDS  # backward-compatible alias


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    model_name = "fake-hash-256"
    dimensions = 256

    def __init__(self) -> None:
        self._token_cache: dict[str, list[float]] = {}

    def _token_vector(self, token: str) -> list[float]:
        cached = self._token_cache.get(token)
        if cached is not None:
            return cached
        values: list[float] = []
        block = 0
        while len(values) < self.dimensions:
            digest = hashlib.sha256(f"{token}:{block}".encode("utf-8")).digest()
            values.extend((b - 127.5) / 127.5 for b in digest)
            block += 1
        vec = values[: self.dimensions]
        norm = math.sqrt(sum(v * v for v in vec))
        vec = [v / norm for v in vec]
        self._token_cache[token] = vec
        return vec

    def embed_one(self, text: str) -> list[float]:
        all_tokens = _TOKEN_RE.findall(text.lower())
        tokens = [t for t in all_tokens if t not in _STOPWORDS] or all_tokens
        if not tokens:
            return [0.0] * self.dimensions
        acc = [0.0] * self.dimensions
        for token in tokens:
            tv = self._token_vector(token)
            for i in range(self.dimensions):
                acc[i] += tv[i]
        norm = math.sqrt(sum(v * v for v in acc))
        if norm == 0.0:
            return [0.0] * self.dimensions
        return [v / norm for v in acc]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]


def pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(blob: bytes, dimensions: int) -> list[float]:
    return list(struct.unpack(f"<{dimensions}f", blob))
