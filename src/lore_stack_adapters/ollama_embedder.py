"""Live Embedder adapter: text -> L2-normalized vector via a local Ollama server.

Implements the lore_stack.seams.embedder.Embedder protocol with no change to the
core. Defaults to nomic-embed-text (768-d). Like every adapter this lives OUTSIDE
the lore_stack core (the core never imports it); the FakeEmbedder stays the default
everywhere and the deterministic gate runs only on the fake.

Why this is safe to mix with the fake (and OpenAI) in one lore: retrieval gates
embeddings by the `model` column (`semantic_search(..., model=embedder.model_name)`),
and this model name differs from the others, so vectors never cross; dimension
mismatches are skipped. The adapter L2-normalizes (Ollama, and nomic in particular,
returns un-normalized vectors), so the stored norm (1.0) holds and cosine == dot
product, matching the FakeEmbedder.

Query/document prefixes: nomic-embed-text was trained with task prefixes that DIFFER
between a stored document and a search query. The seam's symmetric `embed()` is the
document path (ingest + merge-suggestion dedup are all document-side), so it applies
`search_document:`. `embed_query()` applies `search_query:`; retrieval calls it when
present (see retrieval/fusion.py). A model that needs no prefixes can subclass with
both set to "".
"""
import math


class EmbeddingError(Exception):
    pass


class OllamaEmbedder:
    """Embedder protocol implementation backed by a local Ollama embedder model.

    A live embedder: opt-in only, never the default. Install with
    `pip install lore-stack[ollama]` and run `ollama serve` with the model pulled.
    Honors the OLLAMA_HOST env var via the ollama client.
    """

    model_name = "ollama-nomic-embed-text"
    dimensions = 768
    # nomic has a HIGH baseline cosine: measured on this corpus, unrelated text sits
    # ~0.45-0.51 and genuinely related text ~0.65, so the noise floor must sit between
    # them. 0.55 cleanly separates the two with margin on both sides. Tune per corpus
    # (FTS + exact-name still carry keyword hits; this only gates *semantic* matches).
    semantic_floor = 0.55
    # nomic task prefixes (see module docstring). A no-prefix model sets both to "".
    _DOC_PREFIX = "search_document: "
    _QUERY_PREFIX = "search_query: "

    def __init__(self, model: str = "nomic-embed-text", client=None) -> None:
        if client is None:
            import ollama  # adapter-local dependency; the core never imports this

            client = ollama.Client()  # honors OLLAMA_HOST; defaults to localhost:11434
        self._client = client
        self.model = model

    def _embed(self, texts: list[str], prefix: str) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.embed(model=self.model, input=[prefix + t for t in texts])
            vectors = response["embeddings"]  # EmbedResponse supports mapping access
        except Exception as exc:  # surface a typed error like the other adapters
            raise EmbeddingError(f"Ollama embedding request failed: {exc}") from exc
        return [_l2_normalize(list(v)) for v in vectors]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Document-side embedding (ingest + dedup): applies the document prefix."""
        return self._embed(texts, self._DOC_PREFIX)

    def embed_query(self, texts: list[str]) -> list[list[float]]:
        """Query-side embedding (retrieval): applies the query prefix."""
        return self._embed(texts, self._QUERY_PREFIX)


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return [0.0] * len(vector)
    return [v / norm for v in vector]
