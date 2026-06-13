"""Live Embedder adapter: text -> L2-normalized vector via the OpenAI API.

Implements the lore_stack.seams.embedder.Embedder protocol with no change to the
core. Uses text-embedding-3-small (1536-d). Like every adapter this lives OUTSIDE
the lore_stack core (the core never imports it); the FakeEmbedder stays the
default everywhere and the deterministic gate runs only on the fake.

Why this is safe to mix with the fake in one lore: retrieval gates embeddings by
the `model` column (`semantic_search(..., model=embedder.model_name)`), and the
live model name differs from the fake's, so live and fake vectors never cross.
The `semantic_floor` attribute raises retrieval's noise floor above the fake's
256-d default, since a 1536-d semantic model has a much tighter cosine spread.
"""
import math


class EmbeddingError(Exception):
    pass


class OpenAIEmbedder:
    """Embedder protocol implementation backed by OpenAI text-embedding-3-small.

    A live, non-deterministic embedder: opt-in only, never the default. Install
    with `pip install lore-stack[embeddings]`; reads OPENAI_API_KEY from the env.
    """

    model_name = "openai-text-embedding-3-small"
    dimensions = 1536
    # Real semantic embeddings sit far from orthogonal for related text; 0.30 keeps
    # unrelated chunks out without suppressing genuine topical matches. Tune per use.
    semantic_floor = 0.30

    def __init__(self, model: str = "text-embedding-3-small", client=None) -> None:
        if client is None:
            import openai  # adapter-local dependency; the core never imports this

            client = openai.OpenAI()  # reads OPENAI_API_KEY from the environment
        self._client = client
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(model=self.model, input=texts)
        except Exception as exc:  # surface a typed error like the extractor adapter
            raise EmbeddingError(f"OpenAI embedding request failed: {exc}") from exc
        # One item per input, in request order. Normalize defensively so the stored
        # norm (1.0) holds and cosine == dot product, matching the FakeEmbedder.
        return [_l2_normalize(list(item.embedding)) for item in response.data]


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return [0.0] * len(vector)
    return [v / norm for v in vector]
