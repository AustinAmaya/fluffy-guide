#!/usr/bin/env bash
# Thin Hermes skill shell over the lore-stack CLI (storage half). No lore logic here.
set -euo pipefail
PY="${LORE_STACK_PYTHON:-python}"
COMMAND="$1"; DB="$2"
# Embedder comes from $LORE_STACK_EMBEDDER (the CLI reads it directly).
case "$COMMAND" in
  init-db)         exec "$PY" -m lore_stack.cli init-db --db "$DB" ;;
  ingest-delta)
    CANON=""
    [ "${4:-}" = "canon" ] && CANON="--canon"
    exec "$PY" -m lore_stack.cli ingest-delta --db "$DB" --file "$3" $CANON ;;
  stage-delta)     exec "$PY" -m lore_stack.cli stage-delta --db "$DB" --file "$3" ;;
  compile-context) exec "$PY" -m lore_stack.cli compile-context --db "$DB" --query "$3" --out "$4" ;;
  *) echo "unknown command: $COMMAND" >&2; exit 1 ;;
esac
