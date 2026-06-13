#!/usr/bin/env bash
# Thin Hermes skill shell over the lore-stack CLI. No lore logic lives here.
set -euo pipefail
PY="${LORE_STACK_PYTHON:-python}"
COMMAND="$1"; DB="$2"
case "$COMMAND" in
  init-db)         exec "$PY" -m lore_stack.cli init-db --db "$DB" ;;
  ingest-delta)    exec "$PY" -m lore_stack.cli ingest-delta --db "$DB" --file "$3" ;;
  compile-context) exec "$PY" -m lore_stack.cli compile-context --db "$DB" --query "$3" --out "$4" ;;
  *) echo "unknown command: $COMMAND" >&2; exit 1 ;;
esac
