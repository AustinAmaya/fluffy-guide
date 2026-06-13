"""Review-before-commit staging: extract -> review -> downselect -> apply.

An extracted LoreDelta is parked as a *proposal* (staged_deltas, status=pending)
without touching the lore. The operator reviews it in the visualizer, unchecks
unwanted items, and applies the selected subset -- which is rebuilt into a
LoreDelta and run through the normal writeback engine in reviewed mode (the
human's approval replaces the confidence gate). Nothing reaches canon without a
person in the loop.
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from lore_stack.models.delta import LoreDelta, WritebackReport
from lore_stack.seams.embedder import Embedder
from lore_stack.writeback import apply_delta


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stage(conn: sqlite3.Connection, delta: LoreDelta,
          story_text: Optional[str] = None) -> str:
    """Park a proposal. Writes only to staged_deltas; the lore is untouched."""
    n = conn.execute("SELECT COUNT(*) FROM staged_deltas").fetchone()[0]
    staging_id = f"stg_{n + 1:06d}"
    with conn:
        conn.execute(
            "INSERT INTO staged_deltas (staging_id, story_id, story_title, story_text,"
            " delta_json, status, decisions_json, created_at, resolved_at)"
            " VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)",
            (staging_id, delta.story_id, delta.story_title, story_text,
             delta.model_dump_json(), _now()),
        )
    return staging_id


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["delta"] = json.loads(d.pop("delta_json"))
    if d.get("decisions_json"):
        d["decisions"] = json.loads(d["decisions_json"])
    d.pop("decisions_json", None)
    # A compact section-count summary for list views.
    delta = d["delta"]
    d["counts"] = {
        "entities": len(delta.get("entities", [])),
        "claims": len(delta.get("claims", [])),
        "chunks": len(delta.get("chunks", [])),
        "open_questions": len(delta.get("open_questions", [])),
    }
    return d


def list_staged(conn: sqlite3.Connection, status: str = "pending") -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM staged_deltas WHERE status=? ORDER BY created_at, staging_id",
        (status,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_staged(conn: sqlite3.Connection, staging_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM staged_deltas WHERE staging_id=?", (staging_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def _filter_delta(delta: LoreDelta, selection: Optional[dict]) -> LoreDelta:
    """Keep only the selected indices per section. selection=None keeps everything.
    Selection shape: {"entities": [0,2], "claims": [..], "chunks": [..],
    "open_questions": [..]}; a missing key keeps that whole section."""
    if selection is None:
        return delta

    def pick(items, key):
        if key not in selection:
            return list(items)
        keep = set(selection[key])
        return [it for i, it in enumerate(items) if i in keep]

    return delta.model_copy(update={
        "entities": pick(delta.entities, "entities"),
        "claims": pick(delta.claims, "claims"),
        "chunks": pick(delta.chunks, "chunks"),
        "open_questions": pick(delta.open_questions, "open_questions"),
    })


class StagingError(Exception):
    pass


def apply_staged(
    conn: sqlite3.Connection,
    staging_id: str,
    *,
    selection: Optional[dict] = None,
    embedder: Optional[Embedder] = None,
) -> WritebackReport:
    """Apply the selected subset of a staged proposal through the reviewed
    writeback path, then mark the stage applied (recording the selection)."""
    row = conn.execute(
        "SELECT status, delta_json, story_text FROM staged_deltas WHERE staging_id=?",
        (staging_id,),
    ).fetchone()
    if row is None:
        raise StagingError(f"unknown staged delta {staging_id!r}")
    if row["status"] != "pending":
        raise StagingError(f"staged delta {staging_id!r} is already {row['status']}")

    delta = LoreDelta.model_validate(json.loads(row["delta_json"]))
    filtered = _filter_delta(delta, selection)
    report = apply_delta(
        conn, filtered, story_text=row["story_text"], embedder=embedder, reviewed=True
    )
    with conn:
        conn.execute(
            "UPDATE staged_deltas SET status='applied', decisions_json=?, resolved_at=?"
            " WHERE staging_id=?",
            (json.dumps(selection) if selection is not None else json.dumps("all"),
             _now(), staging_id),
        )
    return report


def discard_staged(conn: sqlite3.Connection, staging_id: str) -> None:
    row = conn.execute(
        "SELECT status FROM staged_deltas WHERE staging_id=?", (staging_id,)
    ).fetchone()
    if row is None:
        raise StagingError(f"unknown staged delta {staging_id!r}")
    if row["status"] != "pending":
        raise StagingError(f"staged delta {staging_id!r} is already {row['status']}")
    with conn:
        conn.execute(
            "UPDATE staged_deltas SET status='discarded', resolved_at=? WHERE staging_id=?",
            (_now(), staging_id),
        )
