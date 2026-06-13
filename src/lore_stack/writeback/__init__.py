from lore_stack.writeback.engine import (
    WritebackError,
    apply_delta,
    deprecate_chunk,
    deprecate_entity,
    deprecate_fact,
    manual_edit_fact,
    resolve_contradiction,
    resolve_merge_suggestion,
    restore_entity,
)

__all__ = [
    "apply_delta",
    "manual_edit_fact",
    "deprecate_entity",
    "deprecate_fact",
    "deprecate_chunk",
    "restore_entity",
    "resolve_merge_suggestion",
    "resolve_contradiction",
    "WritebackError",
]
