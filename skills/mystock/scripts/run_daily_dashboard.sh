#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
DB_PATH="$ROOT_DIR/mystock/data/mystock.db"
OUT_DIR="/Users/assa_david/Documents/MyAswomeVault/10.WorkFlow/04.Investment/01.Status"

exec python3 "$ROOT_DIR/mystock/scripts/generate_daily_account_dashboard.py" \
  --db "$DB_PATH" \
  --output-dir "$OUT_DIR" \
  --snapshot-mode ignore \
  --fx-source "daily_dashboard_live"
