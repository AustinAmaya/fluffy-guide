from lore_stack.writeback.engine import (
    WritebackError,
    apply_delta,
    confirm_chunk_fresh,
    deprecate_chunk,
    deprecate_entity,
    deprecate_fact,
    manual_edit_fact,
    resolve_contradiction,
    resolve_merge_suggestion,
    resolve_supersession,
    restore_entity,
)

__all__ = [
    "apply_delta",
    "manual_edit_fact",
    "deprecate_entity",
    "deprecate_fact",
    "deprecate_chunk",
    "confirm_chunk_fresh",
    "restore_entity",
    "resolve_merge_suggestion",
    "resolve_contradiction",
    "resolve_supersession",
    "WritebackError",
]
