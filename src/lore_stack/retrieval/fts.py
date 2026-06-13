"""FTS5 keyword retrieval over lore chunks (titles, bodies, activation keys)."""
import re
import sqlite3

from lore_stack.seams.embedder import STOPWORDS

ACTIVE_CHUNK_STATUSES = ("provisional", "canonical")

_TOKEN_RE = re.compile(r"[a-zA-Z0-9']+")


def fts_search(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[tuple[str, float]]:
    """Return (chunk_id, rank_score) for active chunks matching any query token.

    rank_score is positional (1/(1+rank)) rather than raw bm25, which keeps the
    fusion scorer simple and byte-deterministic. Stopwords are dropped from the
    MATCH expression: the default FTS5 tokenizer indexes function words like "a",
    so a query containing "a" would otherwise match nearly every chunk.
    """
    tokens = [t for t in _TOKEN_RE.findall(query) if t.lower() not in STOPWORDS]
    if not tokens:
        return []
    match = " OR ".join(f'"{t}"' for t in tokens)
    rows = conn.execute(
        "SELECT c.chunk_id FROM lore_chunks_fts f"
        " JOIN lore_chunks c ON c.rowid = f.rowid"
        f" WHERE lore_chunks_fts MATCH ? AND c.status IN ({','.join('?' * len(ACTIVE_CHUNK_STATUSES))})"
        " AND c.stale = 0"
        " ORDER BY bm25(lore_chunks_fts), c.chunk_id LIMIT ?",
        (match, *ACTIVE_CHUNK_STATUSES, limit),
    ).fetchall()
    return [(row["chunk_id"], 1.0 / (1.0 + i)) for i, row in enumerate(rows)]
