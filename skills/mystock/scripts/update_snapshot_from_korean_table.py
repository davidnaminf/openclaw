#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))

ACCOUNT_NO_TO_ID = {
    "230-5002-8842-0": "DC",
    "364-5400-8768-0": "FUND",
    "001-1024-2048-0": "IRP",
    "079-99-012313": "USStock",
}

CASH_NAMES = {
    "미래에셋증권현금성자산",
    "예수금",
    "미국달러",
    "CMA RP_개인",
}

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "mystock.db"


@dataclass
class ParsedRow:
    name: str
    qty: float | None
    current_price: float | None
    avg_buy: float | None
    buy_amount: float | None
    eval_amount: float | None


def now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone(KST)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_name(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "").lower()


def parse_number(s: str | None) -> float | None:
    if s is None:
        return None
    v = str(s).strip()
    if not v or v in {"-", "--", "—", "NA", "N/A"}:
        return None
    v = v.replace(",", "").replace("%", "")
    try:
        return float(v)
    except ValueError:
        return None


def split_line(line: str) -> list[str]:
    if "\t" in line:
        cols = [c.strip() for c in line.split("\t")]
        return [c for c in cols if c != ""]
    cols = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
    return [c for c in cols if c != ""]


def detect_account_no(text: str) -> str | None:
    m = re.search(r"\[\s*([0-9\-]+)\s*\]", text)
    return m.group(1) if m else None


def parse_table(text: str) -> tuple[list[ParsedRow], dict[str, int]]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    header_idx = -1
    header_cols: list[str] = []
    for i, ln in enumerate(lines):
        if "상품명" in ln and ("보유수량" in ln or "잔고수량" in ln):
            header_idx = i
            header_cols = split_line(ln)
            break

    if header_idx < 0:
        raise ValueError("표 헤더(상품명/보유수량)를 찾지 못했습니다.")

    # Column alias mapping
    aliases = {
        "name": {"상품명", "종목명", "Name"},
        "qty": {"보유수량", "잔고수량", "수량"},
        "current_price": {"현재가", "현재 가격", "현재가격"},
        "avg_buy": {"평균매입가", "매입단가", "평균매입단가"},
        "buy_amount": {"매입금액", "투자원금", "매입원금"},
        "eval_amount": {"평가금액", "현재가치", "평가 금액"},
    }

    col_index: dict[str, int] = {}
    for key, names in aliases.items():
        for idx, c in enumerate(header_cols):
            if c in names:
                col_index[key] = idx
                break

    required = ["name", "qty"]
    for r in required:
        if r not in col_index:
            raise ValueError(f"필수 컬럼이 없습니다: {r}")

    rows: list[ParsedRow] = []
    for ln in lines[header_idx + 1 :]:
        cols = split_line(ln)
        if len(cols) < 2:
            continue

        def pick(key: str) -> str | None:
            idx = col_index.get(key)
            if idx is None or idx >= len(cols):
                return None
            return cols[idx]

        name = (pick("name") or "").strip()
        if not name:
            continue

        rows.append(
            ParsedRow(
                name=name,
                qty=parse_number(pick("qty")),
                current_price=parse_number(pick("current_price")),
                avg_buy=parse_number(pick("avg_buy")),
                buy_amount=parse_number(pick("buy_amount")),
                eval_amount=parse_number(pick("eval_amount")),
            )
        )

    return rows, col_index


def infer_usdkrw(rows: list[ParsedRow], conn: sqlite3.Connection) -> float:
    for r in rows:
        if r.name == "미국달러" and r.current_price and r.current_price > 500:
            return float(r.current_price)

    row = conn.execute(
        """
        SELECT rate FROM fx_rates
        WHERE base_currency='USD' AND quote_currency='KRW'
        ORDER BY updated_at DESC LIMIT 1
        """
    ).fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return 1400.0


def has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == col for r in rows)


def load_holdings_for_account(conn: sqlite3.Connection, account_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT h.instrument_id, i.name, i.ticker, i.currency,
               h.quantity, h.avg_buy_price_krw,
               h.avg_buy_price_native
        FROM holdings h
        JOIN instruments i ON i.instrument_id=h.instrument_id
        WHERE h.account_id=?
        """,
        (account_id,),
    ).fetchall()
    out = []
    for instrument_id, name, ticker, currency, qty, avg_krw, avg_native in rows:
        out.append(
            {
                "instrument_id": int(instrument_id),
                "name": str(name),
                "ticker": str(ticker),
                "currency": str(currency).upper(),
                "quantity": float(qty or 0.0),
                "avg_buy_price_krw": None if avg_krw is None else float(avg_krw),
                "avg_buy_price_native": None if avg_native is None else float(avg_native),
            }
        )
    return out


def resolve_row_to_holding(row: ParsedRow, holdings: list[dict[str, Any]]) -> dict[str, Any] | None:
    # Exact first
    for h in holdings:
        if h["name"] == row.name:
            return h
    # Normalized fallback
    want = normalize_name(row.name)
    for h in holdings:
        if normalize_name(h["name"]) == want:
            return h
    return None


def compute_avg_buy_krw(row: ParsedRow, qty: float | None, fallback: float | None) -> float | None:
    if row.avg_buy is not None:
        return float(row.avg_buy)
    if row.buy_amount is not None and qty is not None and qty > 0:
        return float(row.buy_amount) / qty
    return fallback


def upsert_cash_rows(
    conn: sqlite3.Connection,
    account_id: str,
    rows: list[ParsedRow],
    usdkrw: float,
    ts_utc: str,
) -> list[dict[str, Any]]:
    cash_updates: list[dict[str, Any]] = []
    cash_rows = [r for r in rows if r.name in CASH_NAMES]
    if not cash_rows:
        return cash_updates

    conn.execute("DELETE FROM cash_balances WHERE account_id=?", (account_id,))

    for r in cash_rows:
        currency = "USD" if r.name == "미국달러" else "KRW"
        if currency == "USD":
            amount = r.qty if r.qty is not None else (r.eval_amount / usdkrw if r.eval_amount else 0.0)
            avg_price_krw = usdkrw
        else:
            amount = r.eval_amount if r.eval_amount is not None else (r.qty if r.qty is not None else 0.0)
            avg_price_krw = None

        conn.execute(
            """
            INSERT INTO cash_balances(account_id,cash_name,currency,amount,avg_price_krw,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(account_id,cash_name,currency)
            DO UPDATE SET amount=excluded.amount, avg_price_krw=excluded.avg_price_krw, updated_at=excluded.updated_at
            """,
            (account_id, r.name, currency, float(amount), avg_price_krw, ts_utc),
        )

        cash_updates.append({
            "name": r.name,
            "currency": currency,
            "amount": float(amount),
        })

    return cash_updates


def apply_update(
    db_path: Path,
    raw_text: str,
    account_id_override: str | None,
    account_no_override: str | None,
    total_krw_override: float | None,
    snapshot_date_override: str | None,
    apply: bool,
) -> dict[str, Any]:
    account_no = account_no_override or detect_account_no(raw_text)
    if account_id_override:
        account_id = account_id_override
    else:
        if not account_no:
            raise ValueError("계좌번호를 찾지 못했습니다. --account-id 또는 [계좌번호]를 제공하세요.")
        account_id = ACCOUNT_NO_TO_ID.get(account_no)
        if not account_id:
            raise ValueError(f"계좌번호 매핑이 없습니다: {account_no}")

    rows, col_map = parse_table(raw_text)
    if not rows:
        raise ValueError("파싱된 행이 없습니다.")

    snapshot_date = snapshot_date_override or now_kst().strftime("%Y%m%d")
    ts_utc = now_utc_iso()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        usdkrw = infer_usdkrw(rows, conn)
        has_native = has_column(conn, "holdings", "avg_buy_price_native")

        holdings = load_holdings_for_account(conn, account_id)
        holding_updates: list[dict[str, Any]] = []
        unmatched_rows: list[str] = []

        for r in rows:
            if r.name in CASH_NAMES:
                continue
            h = resolve_row_to_holding(r, holdings)
            if h is None:
                unmatched_rows.append(r.name)
                continue

            qty = r.qty if r.qty is not None else h["quantity"]
            avg_krw = compute_avg_buy_krw(r, qty, h["avg_buy_price_krw"])

            if h["currency"] == "USD":
                if avg_krw is None:
                    avg_krw = h["avg_buy_price_krw"] if h["avg_buy_price_krw"] is not None else (
                        (h["avg_buy_price_native"] * usdkrw) if h["avg_buy_price_native"] is not None else None
                    )
                avg_native = (avg_krw / usdkrw) if (avg_krw is not None and usdkrw > 0) else h["avg_buy_price_native"]
                new_avg_krw = None
            else:
                avg_native = avg_krw
                new_avg_krw = avg_krw

            holding_updates.append(
                {
                    "instrument_id": h["instrument_id"],
                    "name": h["name"],
                    "currency": h["currency"],
                    "quantity": float(qty or 0.0),
                    "avg_native": None if avg_native is None else float(avg_native),
                    "avg_krw": None if new_avg_krw is None else float(new_avg_krw),
                }
            )

        cash_updates: list[dict[str, Any]] = []

        # Snapshot total from table eval sum unless override provided
        eval_sum = sum(float(r.eval_amount or 0.0) for r in rows)
        snapshot_total_krw = float(total_krw_override) if total_krw_override is not None else float(eval_sum)

        summary: dict[str, Any] = {
            "db": str(db_path),
            "account_no": account_no,
            "account_id": account_id,
            "snapshot_date": snapshot_date,
            "usdkrw": usdkrw,
            "rows_parsed": len(rows),
            "unmatched_rows": unmatched_rows,
            "holding_updates": holding_updates,
            "snapshot_total_krw": snapshot_total_krw,
            "cash_updates": [],
            "mode": "apply" if apply else "preview",
            "header_columns": col_map,
        }

        if not apply:
            return summary

        backup_path = db_path.with_name(f"mystock.bak_before_telegram_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(db_path, backup_path)

        conn.execute("BEGIN TRANSACTION")

        # Account metadata
        source = f"telegram_snapshot:[{account_no}]" if account_no else "telegram_snapshot"
        conn.execute(
            """
            UPDATE accounts
            SET as_of_date=?, source_file=?, updated_at=?
            WHERE account_id=?
            """,
            (snapshot_date, source, ts_utc, account_id),
        )

        # FX update
        conn.execute(
            """
            INSERT INTO fx_rates(base_currency, quote_currency, rate, source, updated_at)
            VALUES('USD','KRW',?,?,?)
            ON CONFLICT(base_currency, quote_currency)
            DO UPDATE SET rate=excluded.rate, source=excluded.source, updated_at=excluded.updated_at
            """,
            (usdkrw, "telegram_table", ts_utc),
        )

        # Holdings update
        for u in holding_updates:
            if has_native:
                conn.execute(
                    """
                    UPDATE holdings
                    SET quantity=?, avg_buy_price_native=?, avg_buy_price_krw=?, updated_at=?
                    WHERE account_id=? AND instrument_id=?
                    """,
                    (
                        u["quantity"],
                        u["avg_native"],
                        u["avg_krw"],
                        ts_utc,
                        account_id,
                        u["instrument_id"],
                    ),
                )
            else:
                # Legacy schema fallback
                conn.execute(
                    """
                    UPDATE holdings
                    SET quantity=?, avg_buy_price_krw=?, updated_at=?
                    WHERE account_id=? AND instrument_id=?
                    """,
                    (
                        u["quantity"],
                        u["avg_krw"] if u["avg_krw"] is not None else u["avg_native"],
                        ts_utc,
                        account_id,
                        u["instrument_id"],
                    ),
                )

        cash_updates = upsert_cash_rows(conn, account_id, rows, usdkrw, ts_utc)

        # Snapshot upsert
        conn.execute(
            """
            INSERT INTO account_value_snapshots(snapshot_date, account_id, snapshot_at_utc, value_krw, created_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(snapshot_date, account_id)
            DO UPDATE SET snapshot_at_utc=excluded.snapshot_at_utc, value_krw=excluded.value_krw, created_at=excluded.created_at
            """,
            (snapshot_date, account_id, ts_utc, snapshot_total_krw, ts_utc),
        )

        conn.commit()

        summary["cash_updates"] = cash_updates
        summary["backup_path"] = str(backup_path)
        return summary

    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Update MyStock DB from pasted Korean account table")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--input-file", default="", help="Input text file path")
    parser.add_argument("--text", default="", help="Raw text payload")
    parser.add_argument("--account-id", default="", help="Override account id (DC/FUND/IRP/USStock)")
    parser.add_argument("--account-no", default="", help="Override raw account number")
    parser.add_argument("--total-krw", type=float, default=None, help="Override snapshot total KRW")
    parser.add_argument("--snapshot-date", default="", help="Override snapshot date YYYYMMDD")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: preview only)")
    parser.add_argument("--pretty", action="store_true", help="Pretty JSON")
    args = parser.parse_args()

    if not args.text and not args.input_file:
        raise SystemExit("--text 또는 --input-file 중 하나는 필요합니다.")

    if args.input_file:
        raw_text = Path(args.input_file).read_text(encoding="utf-8")
    else:
        raw_text = args.text

    summary = apply_update(
        db_path=Path(args.db).expanduser().resolve(),
        raw_text=raw_text,
        account_id_override=(args.account_id or None),
        account_no_override=(args.account_no or None),
        total_krw_override=args.total_krw,
        snapshot_date_override=(args.snapshot_date or None),
        apply=args.apply,
    )

    if args.pretty:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
