"""Exact-cosine semantic retrieval over normalized vectors stored in SQLite.

The only retrieval math in v1: dot product of L2-normalized vectors, computed
exactly in Python. No vector extensions.
"""
import sqlite3

from lore_stack.retrieval.fts import ACTIVE_CHUNK_STATUSES
from lore_stack.seams.embedder import unpack_vector


def semantic_search(
    conn: sqlite3.Connection,
    query_vector: list[float],
    *,
    model: str,
    limit: int = 50,
) -> list[tuple[str, float]]:
    """Return (chunk_id, cosine_similarity) over active chunks, best first."""
    rows = conn.execute(
        "SELECT e.chunk_id, e.vector_blob, e.dimensions FROM chunk_embeddings e"
        " JOIN lore_chunks c ON c.chunk_id = e.chunk_id"
        f" WHERE e.model = ? AND c.status IN ({','.join('?' * len(ACTIVE_CHUNK_STATUSES))})"
        " AND c.stale = 0"
        " ORDER BY e.chunk_id",
        (model, *ACTIVE_CHUNK_STATUSES),
    ).fetchall()
    scored = []
    for row in rows:
        vec = unpack_vector(row["vector_blob"], row["dimensions"])
        if len(vec) != len(query_vector):
            continue
        sim = sum(a * b for a, b in zip(vec, query_vector))
        scored.append((row["chunk_id"], sim))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored[:limit]
