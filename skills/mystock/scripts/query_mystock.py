#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "mystock.db"


def parse_snapshot_date(value: str) -> str:
    v = value.strip()
    if len(v) == 8 and v.isdigit():
        return v
    try:
        dt = datetime.strptime(v, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"지원하지 않는 날짜 형식: {value}") from exc
    return dt.strftime("%Y%m%d")


def fetch_account_snapshot(
    conn: sqlite3.Connection, account_id: str, target_date: str | None
) -> tuple[str, float] | None:
    if target_date:
        row = conn.execute(
            """
            SELECT snapshot_date, value_krw
            FROM account_value_snapshots
            WHERE account_id=? AND snapshot_date<=?
            ORDER BY snapshot_date DESC
            LIMIT 1
            """,
            (account_id, target_date),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT snapshot_date, value_krw
            FROM account_value_snapshots
            WHERE account_id=?
            ORDER BY snapshot_date DESC
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()
    if not row:
        return None
    return (str(row[0]), float(row[1]))


def cmd_totals(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    target_date = parse_snapshot_date(args.date) if args.date else None
    if args.account:
        account_ids = [args.account]
    else:
        rows = conn.execute("SELECT account_id FROM accounts ORDER BY account_id").fetchall()
        account_ids = [str(r[0]) for r in rows]

    accounts: list[dict[str, Any]] = []
    for account_id in account_ids:
        snap = fetch_account_snapshot(conn, account_id, target_date)
        if not snap:
            continue
        snap_date, value_krw = snap
        accounts.append(
            {
                "account_id": account_id,
                "snapshot_date": snap_date,
                "value_krw": value_krw,
            }
        )

    total_krw = sum(a["value_krw"] for a in accounts)
    return {
        "query": "totals",
        "target_date": target_date,
        "accounts": accounts,
        "total_krw": total_krw,
    }


def cmd_position(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT h.account_id,
               i.ticker,
               i.name,
               i.currency,
               h.quantity,
               h.avg_buy_price_native,
               h.avg_buy_price_krw,
               h.updated_at
        FROM holdings h
        JOIN instruments i ON i.instrument_id=h.instrument_id
        ORDER BY h.account_id, i.name
        """
    ).fetchall()

    symbol = (args.symbol or "").strip()
    symbol_upper = symbol.upper()
    symbol_lc = symbol.casefold()

    out: list[dict[str, Any]] = []
    for account_id, ticker, name, currency, qty, avg_native, avg_krw, updated_at in rows:
        a = str(account_id)
        t = str(ticker or "")
        n = str(name or "")
        if args.account and a != args.account:
            continue
        if symbol:
            if t.upper() != symbol_upper and symbol_lc not in n.casefold():
                continue
        out.append(
            {
                "account_id": a,
                "ticker": t,
                "name": n,
                "currency": str(currency or "KRW").upper(),
                "quantity": float(qty or 0.0),
                "avg_buy_price_native": None if avg_native is None else float(avg_native),
                "avg_buy_price_krw": None if avg_krw is None else float(avg_krw),
                "updated_at": str(updated_at or ""),
            }
        )

    return {
        "query": "position",
        "account": args.account,
        "symbol": symbol,
        "rows": out,
    }


def cmd_trades(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    conditions: list[str] = []
    values: list[Any] = []

    if args.account:
        conditions.append("account_id=?")
        values.append(args.account)

    symbol = (args.symbol or "").strip()
    if symbol:
        conditions.append("(UPPER(ticker)=UPPER(?) OR raw_text LIKE ?)")
        values.extend([symbol, f"%{symbol}%"])

    if args.date_from:
        dt_from = parse_snapshot_date(args.date_from)
        conditions.append("REPLACE(SUBSTR(occurred_at,1,10),'-','')>=?")
        values.append(dt_from)

    if args.date_to:
        dt_to = parse_snapshot_date(args.date_to)
        conditions.append("REPLACE(SUBSTR(occurred_at,1,10),'-','')<=?")
        values.append(dt_to)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit = max(1, int(args.limit))
    rows = conn.execute(
        f"""
        SELECT trade_id, account_id, ticker, side, quantity, price, currency, occurred_at, source, status, note
        FROM trade_ledger
        {where}
        ORDER BY occurred_at DESC
        LIMIT {limit}
        """,
        values,
    ).fetchall()

    out: list[dict[str, Any]] = []
    for trade_id, account_id, ticker, side, qty, price, currency, occurred_at, source, status, note in rows:
        out.append(
            {
                "trade_id": str(trade_id),
                "account_id": str(account_id),
                "ticker": str(ticker or ""),
                "side": str(side or ""),
                "quantity": float(qty or 0.0),
                "price": float(price or 0.0),
                "currency": str(currency or ""),
                "occurred_at": str(occurred_at or ""),
                "source": str(source or ""),
                "status": str(status or ""),
                "note": str(note or ""),
            }
        )

    return {
        "query": "trades",
        "account": args.account,
        "symbol": symbol,
        "date_from": args.date_from,
        "date_to": args.date_to,
        "limit": limit,
        "rows": out,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query MyStock SQLite database")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--pretty", action="store_true", help="Pretty JSON output")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_totals = sub.add_parser("totals", help="Get account totals from snapshots")
    p_totals.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    p_totals.add_argument("--pretty", action="store_true", help="Pretty JSON output")
    p_totals.add_argument("--account", default="", help="Account id (DC/FUND/IRP/USStock)")
    p_totals.add_argument("--date", default="", help="Target date (YYYYMMDD or YYYY-MM-DD)")

    p_pos = sub.add_parser("position", help="Get holdings by account/symbol")
    p_pos.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    p_pos.add_argument("--pretty", action="store_true", help="Pretty JSON output")
    p_pos.add_argument("--account", default="", help="Account id (optional)")
    p_pos.add_argument("--symbol", default="", help="Ticker or name fragment (optional)")

    p_trades = sub.add_parser("trades", help="Get trade history")
    p_trades.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    p_trades.add_argument("--pretty", action="store_true", help="Pretty JSON output")
    p_trades.add_argument("--account", default="", help="Account id (optional)")
    p_trades.add_argument("--symbol", default="", help="Ticker or text filter (optional)")
    p_trades.add_argument("--date-from", default="", help="From date YYYYMMDD or YYYY-MM-DD")
    p_trades.add_argument("--date-to", default="", help="To date YYYYMMDD or YYYY-MM-DD")
    p_trades.add_argument("--limit", type=int, default=30, help="Row limit")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    conn = sqlite3.connect(db_path)
    try:
        if args.cmd == "totals":
            result = cmd_totals(conn, args)
        elif args.cmd == "position":
            result = cmd_position(conn, args)
        elif args.cmd == "trades":
            result = cmd_trades(conn, args)
        else:
            raise ValueError(f"Unsupported command: {args.cmd}")
    finally:
        conn.close()

    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
