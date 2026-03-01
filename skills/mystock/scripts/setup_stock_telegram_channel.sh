#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <TELEGRAM_BOT_TOKEN_FOR_STOCK>"
  exit 1
fi

TOKEN="$1"

openclaw channels add --channel telegram --account stock --token "$TOKEN"
openclaw agents bind --agent stock --bind telegram:stock >/dev/null || true
openclaw channels status --probe
openclaw agents bindings
