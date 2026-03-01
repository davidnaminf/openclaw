# MyStock Daily Dashboard

## What this does
- Calculates **daily account total value in KRW** from `mystock.db`.
- Uses **live USD/KRW** and converts USD assets/cash to KRW at runtime.
- Tracks account return vs `D-1`, `D-3`, `D-7`, `D-30` from `account_value_snapshots`.
- Compares `TOTAL` return against benchmarks (`KOSPI`, `KOSDAQ`, `NASDAQ`, `S&P500`).
- Writes Obsidian pages:
  - `DASHBOARD_DAILY_YYYYMMDD.md`
  - `DASHBOARD_DAILY_LATEST.md`

## Scripts
- `skills/mystock/scripts/generate_daily_account_dashboard.py`
- `skills/mystock/scripts/run_daily_dashboard.sh`
- `skills/mystock/scripts/install_launch_agent.sh`
- `skills/mystock/scripts/update_snapshot_from_korean_table.py`
- `skills/mystock/scripts/update_trade_from_korean_message.py`
- `skills/mystock/scripts/query_mystock.py`

## Manual run
```bash
bash skills/mystock/scripts/run_daily_dashboard.sh
```

## Install daily scheduler (macOS launchd)
```bash
bash skills/mystock/scripts/install_launch_agent.sh
```

Default schedule: `02:05` KST every day.

## Snapshot write mode
`generate_daily_account_dashboard.py` supports:
- `--snapshot-mode upsert` (default): overwrite same-day snapshot if rerun.
- `--snapshot-mode ignore`: keep existing same-day snapshot.

## Telegram table update workflow
Preview first:
```bash
python3 skills/mystock/scripts/update_snapshot_from_korean_table.py \
  --db skills/mystock/data/mystock.db \
  --input-file /tmp/telegram_stock_input.txt \
  --pretty
```

Apply after confirmation:
```bash
python3 skills/mystock/scripts/update_snapshot_from_korean_table.py \
  --db skills/mystock/data/mystock.db \
  --input-file /tmp/telegram_stock_input.txt \
  --apply \
  --pretty
```

## Telegram trade-message update workflow
Preview first:
```bash
python3 skills/mystock/scripts/update_trade_from_korean_message.py \
  --db skills/mystock/data/mystock.db \
  --input-file /tmp/telegram_trade_input.txt \
  --pretty
```

Apply after confirmation:
```bash
python3 skills/mystock/scripts/update_trade_from_korean_message.py \
  --db skills/mystock/data/mystock.db \
  --input-file /tmp/telegram_trade_input.txt \
  --apply \
  --pretty
```

## Quick DB queries
Latest account total:
```bash
python3 skills/mystock/scripts/query_mystock.py totals \
  --db skills/mystock/data/mystock.db \
  --account DC \
  --pretty
```

Position lookup:
```bash
python3 skills/mystock/scripts/query_mystock.py position \
  --db skills/mystock/data/mystock.db \
  --account USStock \
  --symbol STX \
  --pretty
```
