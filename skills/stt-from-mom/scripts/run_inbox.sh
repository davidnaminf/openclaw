#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"

exec "$PY" "$BASE_DIR/process_inbox.py" "$@"
