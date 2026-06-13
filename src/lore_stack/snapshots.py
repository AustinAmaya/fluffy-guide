"""Whole-file snapshots of a lore database, with rollback.

A lore is one SQLite file and the corpus is small, so versioning is simply a
copy of the file via SQLite's online backup API (safe with open connections).
Snapshots for a lore at <dir>/<name>.db live in <dir>/.snapshots/<name>/, one
<seq>.db per snapshot plus a manifest.json describing them.

Mutating writeback operations call maybe_snapshot() before they write, so the
snapshot captures the state *before* the operation. Rolling back to snapshot N
therefore undoes operation N (and rollback snapshots first, so it is itself
undoable).
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_RETENTION = 20
_COUNT_TABLES = {
    "stories": "story_runs",
    "entities": "entities",
    "facts": "facts",
    "open_conflicts": None,  # special-cased below
}


def snapshot_dir(db_path: str | Path) -> Path:
    p = Path(db_path)
    return p.parent / ".snapshots" / p.stem


def _manifest_path(db_path: str | Path) -> Path:
    return snapshot_dir(db_path) / "manifest.json"


def _read_manifest(db_path: str | Path) -> dict:
    path = _manifest_path(db_path)
    if not path.exists():
        return {"next_seq": 1, "entries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(db_path: str | Path, manifest: dict) -> None:
    path = _manifest_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tmp.replace(path)


def _counts(conn: sqlite3.Connection) -> dict:
    out = {}
    for label, table in _COUNT_TABLES.items():
        if table is None:
            continue
        try:
            out[label] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            out[label] = 0
    try:
        out["open_conflicts"] = conn.execute(
            "SELECT COUNT(*) FROM adjudication_queue WHERE status='open'"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        out["open_conflicts"] = 0
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(
    conn: sqlite3.Connection,
    db_path: str | Path,
    operation: str,
    *,
    retention: int = DEFAULT_RETENTION,
) -> dict:
    """Capture the current committed state of db_path as a new snapshot.

    `operation` labels the action this snapshot precedes. Uses the live
    connection as the backup source (committed state only — call before opening
    a write transaction). Returns the new manifest entry.
    """
    db_path = str(db_path)
    sdir = snapshot_dir(db_path)
    sdir.mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest(db_path)
    seq = manifest["next_seq"]
    filename = f"{seq:06d}.db"
    target = sqlite3.connect(str(sdir / filename))
    try:
        with target:
            conn.backup(target)
    finally:
        target.close()
    entry = {
        "seq": seq,
        "operation": operation,
        "created_at": _now(),
        "file": filename,
        "counts": _counts(conn),
    }
    manifest["entries"].append(entry)
    manifest["next_seq"] = seq + 1
    _prune(sdir, manifest, retention)
    _write_manifest(db_path, manifest)
    return entry


def _prune(sdir: Path, manifest: dict, retention: int) -> None:
    entries = manifest["entries"]
    while len(entries) > retention:
        oldest = entries.pop(0)  # entries are append-ordered by seq
        f = sdir / oldest["file"]
        if f.exists():
            f.unlink()


def maybe_snapshot(conn: sqlite3.Connection, operation: str) -> Optional[dict]:
    """Snapshot before a mutation, but only if the connection opted in and points
    at a real file. Returns the entry, or None if snapshotting was skipped.

    Must be called before any write transaction is open: the online backup API
    blocks on a connection that holds an uncommitted write. All library mutations
    snapshot before their `with conn:` block, so this guard never trips in normal
    use -- it turns a future misuse into a clear error instead of a silent hang.
    """
    if not getattr(conn, "auto_snapshot", False):
        return None
    path = getattr(conn, "lore_path", None)
    if not path or path == ":memory:":
        return None
    if conn.in_transaction:
        raise RuntimeError(
            "maybe_snapshot called inside an open transaction; snapshot before"
            " opening the write transaction (the backup API would otherwise block)"
        )
    return create(conn, path, operation)


def list_snapshots(db_path: str | Path) -> list[dict]:
    """Newest first."""
    return list(reversed(_read_manifest(db_path)["entries"]))


def snapshot_file(db_path: str | Path, seq: int) -> Path:
    """Resolve the on-disk file for snapshot `seq` (for read-only preview), or raise."""
    sdir = snapshot_dir(db_path)
    match = next((e for e in _read_manifest(db_path)["entries"] if e["seq"] == seq), None)
    if match is None:
        raise FileNotFoundError(f"no snapshot with seq {seq} for {Path(db_path).stem!r}")
    path = sdir / match["file"]
    if not path.exists():
        raise FileNotFoundError(f"snapshot file missing: {path}")
    return path


def rollback(db_path: str | Path, seq: int) -> dict:
    """Restore the lore to snapshot `seq`, after snapshotting the current state
    (so the rollback is itself undoable). Overwrites the live file in place via
    the backup API, so connections reopened afterward see the restored content.
    """
    db_path = str(db_path)
    sdir = snapshot_dir(db_path)
    manifest = _read_manifest(db_path)
    match = next((e for e in manifest["entries"] if e["seq"] == seq), None)
    if match is None:
        raise FileNotFoundError(f"no snapshot with seq {seq} for {Path(db_path).stem!r}")
    snap_file = sdir / match["file"]
    if not snap_file.exists():
        raise FileNotFoundError(f"snapshot file missing: {snap_file}")

    # Snapshot the current state first, labeled so the history reads clearly.
    pre = sqlite3.connect(db_path)
    try:
        create(pre, db_path, f"rollback-to-{seq:06d}")
    finally:
        pre.close()

    src = sqlite3.connect(str(snap_file))
    dst = sqlite3.connect(db_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    return {"restored_seq": seq, "operation": match["operation"]}
