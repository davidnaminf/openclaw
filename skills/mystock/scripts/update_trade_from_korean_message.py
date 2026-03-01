#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
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

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "mystock.db"


@dataclass
class ParsedTrade:
    account_id: str | None
    account_no_raw: str | None
    side: str | None
    ticker: str | None
    name: str | None
    quantity: float | None
    price: float | None
    amount: float | None
    currency: str | None
    fee: float | None
    tax: float | None


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_number(text: str | None) -> float | None:
    if text is None:
        return None
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def only_digits(text: str) -> str:
    return re.sub(r"[^0-9]", "", text)


def normalize_name(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text or "").casefold()


def detect_account_id(text: str, override_account_id: str | None) -> tuple[str | None, str | None]:
    if override_account_id:
        return override_account_id, None

    upper_text = text.upper()
    if "DC 계좌" in text or "퇴직연금" in text and "DC" in upper_text:
        return "DC", None
    if "IRP" in upper_text:
        return "IRP", None
    if "USSTOCK" in upper_text or "US STOCK" in upper_text:
        return "USStock", None
    if "FUND" in upper_text:
        return "FUND", None
    # Support compact broker message headers like "DC, 250301".
    if re.search(r"(?<![A-Z0-9])DC(?![A-Z0-9])", upper_text):
        return "DC", None
    if re.search(r"(?<![A-Z0-9])IRP(?![A-Z0-9])", upper_text):
        return "IRP", None
    if re.search(r"(?<![A-Z0-9])FUND(?![A-Z0-9])", upper_text):
        return "FUND", None
    if re.search(r"(?<![A-Z0-9])US[ _-]?STOCK(?![A-Z0-9])", upper_text):
        return "USStock", None

    candidates = re.findall(r"[0-9\*]{2,}(?:-[0-9\*]{2,}){2,4}", text)
    for raw in candidates:
        if "*" not in raw and raw in ACCOUNT_NO_TO_ID:
            return ACCOUNT_NO_TO_ID[raw], raw

        prefix_digits = only_digits(raw.split("*", 1)[0]) if "*" in raw else only_digits(raw)
        suffix_digits = only_digits(raw.rsplit("*", 1)[-1]) if "*" in raw else ""

        for known_no, account_id in ACCOUNT_NO_TO_ID.items():
            known_digits = only_digits(known_no)
            if prefix_digits and not known_digits.startswith(prefix_digits):
                continue
            if suffix_digits and not known_digits.endswith(suffix_digits):
                continue
            return account_id, raw

    return None, None


def detect_side(text: str) -> str | None:
    m = re.search(r"매매구분\s*[:：]?\s*(매수|매도)", text)
    if m:
        return "buy" if m.group(1) == "매수" else "sell"
    if "매수체결" in text or " 매수 " in text:
        return "buy"
    if "매도체결" in text or " 매도 " in text:
        return "sell"
    return None


def detect_quantity(text: str) -> float | None:
    patterns = [
        r"(?:체결수량|주문수량)\s*[:：]?\s*([0-9,]+(?:\.[0-9]+)?)\s*주",
        r"(?:매도체결|매수체결)\s*[:：]?\s*([0-9,]+(?:\.[0-9]+)?)\s*주",
        r"([0-9,]+(?:\.[0-9]+)?)\s*주",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return parse_number(m.group(1))
    return None


def _detect_currency_from_around(text: str, index: int) -> str | None:
    window = text[max(0, index - 10) : min(len(text), index + 12)]
    if "USD" in window.upper():
        return "USD"
    if "원" in window or "KRW" in window.upper():
        return "KRW"
    return None


def detect_price(text: str) -> tuple[float | None, str | None]:
    m = re.search(r"체결단가\s*[:：]?\s*(USD|KRW)?\s*([0-9,]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if m:
        currency = (m.group(1) or "").upper() or _detect_currency_from_around(text, m.start())
        return parse_number(m.group(2)), currency
    return None, None


def detect_amount(text: str) -> tuple[float | None, str | None]:
    m = re.search(r"체결금액\s*[:：]?\s*(USD|KRW)?\s*([0-9,]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if m:
        currency = (m.group(1) or "").upper() or _detect_currency_from_around(text, m.start())
        return parse_number(m.group(2)), currency

    m = re.search(r"(?:매도체결|매수체결)\s*[:：]?\s*[0-9,]+(?:\.[0-9]+)?\s*주\s*([0-9,]+(?:\.[0-9]+)?)\s*원", text)
    if m:
        return parse_number(m.group(1)), "KRW"

    return None, None


def detect_fee_tax(text: str) -> tuple[float | None, float | None]:
    fee = None
    tax = None
    m_fee = re.search(r"수수료\s*[:：]?\s*([0-9,]+(?:\.[0-9]+)?)", text)
    if m_fee:
        fee = parse_number(m_fee.group(1))
    m_tax = re.search(r"(?:세금|제세금)\s*[:：]?\s*([0-9,]+(?:\.[0-9]+)?)", text)
    if m_tax:
        tax = parse_number(m_tax.group(1))
    return fee, tax


def detect_ticker(text: str) -> str | None:
    m = re.search(r"[\\(（]([A-Z][A-Z0-9.\-]{0,9})[\\)）]", text)
    if m:
        return m.group(1).upper()
    return None


def detect_name(text: str, ticker: str | None) -> str | None:
    m = re.search(
        r"종목명\s*[:：]?\s*(.+?)\s*(?:매매구분|주문수량|체결수량|체결단가|체결금액|$)",
        text,
        re.DOTALL,
    )
    if m:
        name = m.group(1).strip()
        if ticker:
            name = re.sub(rf"[\\(（]\s*{re.escape(ticker)}\s*[\\)）]\s*$", "", name, flags=re.IGNORECASE)
        return name.strip()

    m = re.search(r"계좌\s+[0-9\-\*]+\s+(.+?)\s*(?:매도체결|매수체결)", text)
    if m:
        return m.group(1).strip()

    if ticker:
        m = re.search(rf"(.+?)[\\(（]\s*{re.escape(ticker)}\s*[\\)）]", text, re.IGNORECASE)
        if m:
            return m.group(1).strip().split()[-1]

    # Support compact multiline broker text:
    # [증권사]매도체결
    # DC, 250301
    # TIGER 화장품
    # 체결수량: ...
    m = re.search(
        r"(?:\n|^)\s*([^\n]{2,80})\s*\n\s*(?:체결수량|주문수량)\s*[:：]?",
        text,
        re.IGNORECASE,
    )
    if m:
        candidate = m.group(1).strip()
        if candidate and not re.search(r"(매수체결|매도체결)", candidate):
            return candidate

    return None


def parse_trade_text(raw_text: str, account_id_override: str | None) -> ParsedTrade:
    account_id, account_no_raw = detect_account_id(raw_text, account_id_override)
    side = detect_side(raw_text)
    quantity = detect_quantity(raw_text)
    price, price_currency = detect_price(raw_text)
    amount, amount_currency = detect_amount(raw_text)
    fee, tax = detect_fee_tax(raw_text)
    ticker = detect_ticker(raw_text)
    name = detect_name(raw_text, ticker)

    currency = amount_currency or price_currency
    if not price and amount is not None and quantity and quantity > 0:
        price = amount / quantity
    if not amount and price is not None and quantity is not None:
        amount = price * quantity

    return ParsedTrade(
        account_id=account_id,
        account_no_raw=account_no_raw,
        side=side,
        ticker=ticker,
        name=name,
        quantity=quantity,
        price=price,
        amount=amount,
        currency=currency,
        fee=fee,
        tax=tax,
    )


def resolve_instrument(conn: sqlite3.Connection, trade: ParsedTrade) -> dict[str, Any] | None:
    if trade.ticker:
        row = conn.execute(
            """
            SELECT instrument_id, ticker, name, currency
            FROM instruments
            WHERE UPPER(ticker)=UPPER(?)
            ORDER BY instrument_id
            LIMIT 1
            """,
            (trade.ticker,),
        ).fetchone()
        if row:
            return {
                "instrument_id": int(row[0]),
                "ticker": str(row[1]),
                "name": str(row[2]),
                "currency": str(row[3]).upper(),
            }

    if trade.name:
        rows = conn.execute(
            """
            SELECT instrument_id, ticker, name, currency
            FROM instruments
            ORDER BY instrument_id
            """
        ).fetchall()
        wanted = normalize_name(trade.name)
        for instrument_id, ticker, name, currency in rows:
            if normalize_name(str(name)) == wanted:
                return {
                    "instrument_id": int(instrument_id),
                    "ticker": str(ticker),
                    "name": str(name),
                    "currency": str(currency).upper(),
                }
        for instrument_id, ticker, name, currency in rows:
            if wanted and wanted in normalize_name(str(name)):
                return {
                    "instrument_id": int(instrument_id),
                    "ticker": str(ticker),
                    "name": str(name),
                    "currency": str(currency).upper(),
                }

    return None


def get_latest_fx_usdkrw(conn: sqlite3.Connection) -> float | None:
    row = conn.execute(
        """
        SELECT rate
        FROM fx_rates
        WHERE base_currency='USD' AND quote_currency='KRW'
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return float(row[0])


def load_holding(
    conn: sqlite3.Connection, account_id: str, instrument_id: int
) -> tuple[float, float | None, float | None]:
    row = conn.execute(
        """
        SELECT quantity, avg_buy_price_native, avg_buy_price_krw
        FROM holdings
        WHERE account_id=? AND instrument_id=?
        """,
        (account_id, instrument_id),
    ).fetchone()
    if not row:
        return 0.0, None, None
    return float(row[0] or 0.0), None if row[1] is None else float(row[1]), None if row[2] is None else float(row[2])


def has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == col for r in rows)


def resolve_cash_name(conn: sqlite3.Connection, account_id: str, currency: str) -> str:
    row = conn.execute(
        """
        SELECT cash_name
        FROM cash_balances
        WHERE account_id=? AND currency=?
        ORDER BY cash_name
        LIMIT 1
        """,
        (account_id, currency),
    ).fetchone()
    if row:
        return str(row[0])
    return "미국달러" if currency == "USD" else "예수금"


def apply_trade(
    db_path: Path,
    raw_text: str,
    account_id_override: str | None,
    occurred_at_override: str | None,
    source: str,
    usdkrw_override: float | None,
    apply: bool,
) -> dict[str, Any]:
    parsed = parse_trade_text(raw_text, account_id_override)
    ts_utc = now_utc_iso()
    occurred_at = occurred_at_override or ts_utc
    trade_id = f"telegram_trade_{hashlib.sha1(raw_text.encode('utf-8')).hexdigest()[:20]}"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        instrument = resolve_instrument(conn, parsed) if parsed.account_id else None
        usdkrw = usdkrw_override if usdkrw_override is not None else get_latest_fx_usdkrw(conn)

        warnings: list[str] = []
        status = "parsed"
        if not parsed.account_id:
            warnings.append("계좌 식별 실패")
            status = "unparsed"
        if not parsed.side:
            warnings.append("매수/매도 식별 실패")
            status = "unparsed"
        if parsed.quantity is None or parsed.quantity <= 0:
            warnings.append("수량 식별 실패")
            status = "unparsed"
        if parsed.price is None or parsed.price <= 0:
            warnings.append("단가 식별 실패")
            status = "unparsed"
        if parsed.amount is None or parsed.amount <= 0:
            warnings.append("금액 식별 실패")
            status = "unparsed"
        if not instrument:
            warnings.append("종목 매핑 실패")
            status = "unparsed"

        currency = parsed.currency or (instrument["currency"] if instrument else "KRW")
        currency = str(currency).upper()
        fee = float(parsed.fee or 0.0)
        tax = float(parsed.tax or 0.0)

        preview: dict[str, Any] = {
            "mode": "apply" if apply else "preview",
            "db": str(db_path),
            "trade_id": trade_id,
            "source": source,
            "occurred_at": occurred_at,
            "status": status,
            "warnings": warnings,
            "parsed": {
                "account_id": parsed.account_id,
                "account_no_raw": parsed.account_no_raw,
                "side": parsed.side,
                "ticker": parsed.ticker,
                "name": parsed.name,
                "quantity": parsed.quantity,
                "price": parsed.price,
                "amount": parsed.amount,
                "currency": currency,
                "fee": fee,
                "tax": tax,
            },
            "instrument": instrument,
            "cash_delta": None,
            "holding_delta": None,
            "usdkrw": usdkrw,
        }

        if status == "parsed":
            assert parsed.side is not None
            assert parsed.account_id is not None
            assert instrument is not None
            assert parsed.quantity is not None
            assert parsed.price is not None
            assert parsed.amount is not None

            old_qty, old_avg_native, old_avg_krw = load_holding(
                conn, parsed.account_id, instrument["instrument_id"]
            )
            new_qty = old_qty
            new_avg_native = old_avg_native
            new_avg_krw = old_avg_krw

            if parsed.side == "buy":
                new_qty = old_qty + parsed.quantity
                base_old_avg = old_avg_native if old_avg_native is not None else parsed.price
                new_avg_native = (
                    ((old_qty * base_old_avg) + (parsed.quantity * parsed.price)) / new_qty
                    if new_qty > 0
                    else parsed.price
                )
                if instrument["currency"] == "KRW":
                    new_avg_krw = new_avg_native
                else:
                    new_avg_krw = None
                cash_delta = -(parsed.amount + fee + tax)
            else:
                new_qty = old_qty - parsed.quantity
                if new_qty < -1e-9:
                    status = "needs_review"
                    warnings.append(
                        f"매도 수량 부족: current={old_qty}, sell={parsed.quantity}"
                    )
                else:
                    if abs(new_qty) < 1e-9:
                        new_qty = 0.0
                    cash_delta = parsed.amount - fee - tax

            preview["status"] = status
            if status in {"parsed", "applied"}:
                preview["holding_delta"] = {
                    "account_id": parsed.account_id,
                    "instrument_id": instrument["instrument_id"],
                    "ticker": instrument["ticker"],
                    "name": instrument["name"],
                    "old_quantity": old_qty,
                    "new_quantity": new_qty,
                    "old_avg_native": old_avg_native,
                    "new_avg_native": new_avg_native,
                    "old_avg_krw": old_avg_krw,
                    "new_avg_krw": new_avg_krw,
                }
                preview["cash_delta"] = {
                    "account_id": parsed.account_id,
                    "currency": currency,
                    "delta": cash_delta,
                }

        if not apply:
            return preview

        backup_path = db_path.with_name(
            f"mystock.bak_before_trade_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        )
        shutil.copy2(db_path, backup_path)
        preview["backup_path"] = str(backup_path)

        duplicate = conn.execute(
            "SELECT 1 FROM trade_ledger WHERE trade_id=?",
            (trade_id,),
        ).fetchone()
        if duplicate:
            preview["status"] = "duplicate_ignored"
            return preview

        conn.execute("BEGIN TRANSACTION")

        conn.execute(
            """
            INSERT OR IGNORE INTO raw_events(event_id, account_id, event_type, payload_json, received_at, parse_status, note)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                trade_id,
                parsed.account_id,
                "telegram_trade_message",
                json.dumps({"raw_text": raw_text}, ensure_ascii=False),
                ts_utc,
                preview["status"],
                "; ".join(warnings) if warnings else None,
            ),
        )

        # Keep ledger rows for auditing even when unparsed/needs_review.
        conn.execute(
            """
            INSERT INTO trade_ledger(
              trade_id, account_id, ticker, side, quantity, price, currency,
              occurred_at, source, raw_text, status, note
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade_id,
                parsed.account_id or "",
                parsed.ticker or (instrument["ticker"] if instrument else ""),
                parsed.side or "",
                float(parsed.quantity or 0.0),
                float(parsed.price or 0.0),
                currency,
                occurred_at,
                source,
                raw_text,
                preview["status"],
                "; ".join(warnings) if warnings else None,
            ),
        )

        if preview["status"] == "parsed" and preview["holding_delta"] and preview["cash_delta"]:
            account_id = parsed.account_id or ""
            instrument_id = int(preview["holding_delta"]["instrument_id"])
            new_qty = float(preview["holding_delta"]["new_quantity"])
            new_avg_native = preview["holding_delta"]["new_avg_native"]
            new_avg_krw = preview["holding_delta"]["new_avg_krw"]

            if has_column(conn, "holdings", "avg_buy_price_native"):
                exists = conn.execute(
                    """
                    SELECT 1 FROM holdings
                    WHERE account_id=? AND instrument_id=?
                    """,
                    (account_id, instrument_id),
                ).fetchone()
                if exists:
                    conn.execute(
                        """
                        UPDATE holdings
                        SET quantity=?, avg_buy_price_native=?, avg_buy_price_krw=?, updated_at=?
                        WHERE account_id=? AND instrument_id=?
                        """,
                        (new_qty, new_avg_native, new_avg_krw, ts_utc, account_id, instrument_id),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO holdings(account_id, instrument_id, quantity, avg_buy_price_native, avg_buy_price_krw, updated_at)
                        VALUES(?,?,?,?,?,?)
                        """,
                        (account_id, instrument_id, new_qty, new_avg_native, new_avg_krw, ts_utc),
                    )
            else:
                avg_fallback = new_avg_krw if new_avg_krw is not None else new_avg_native
                exists = conn.execute(
                    """
                    SELECT 1 FROM holdings
                    WHERE account_id=? AND instrument_id=?
                    """,
                    (account_id, instrument_id),
                ).fetchone()
                if exists:
                    conn.execute(
                        """
                        UPDATE holdings
                        SET quantity=?, avg_buy_price_krw=?, updated_at=?
                        WHERE account_id=? AND instrument_id=?
                        """,
                        (new_qty, avg_fallback, ts_utc, account_id, instrument_id),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO holdings(account_id, instrument_id, quantity, avg_buy_price_krw, updated_at)
                        VALUES(?,?,?,?,?)
                        """,
                        (account_id, instrument_id, new_qty, avg_fallback, ts_utc),
                    )

            cash_currency = str(preview["cash_delta"]["currency"]).upper()
            delta = float(preview["cash_delta"]["delta"])
            cash_name = resolve_cash_name(conn, account_id, cash_currency)
            row = conn.execute(
                """
                SELECT amount, avg_price_krw
                FROM cash_balances
                WHERE account_id=? AND cash_name=? AND currency=?
                """,
                (account_id, cash_name, cash_currency),
            ).fetchone()
            old_amount = float(row[0]) if row else 0.0
            avg_price_krw = float(row[1]) if row and row[1] is not None else None
            new_amount = old_amount + delta

            if cash_currency == "USD" and avg_price_krw is None:
                avg_price_krw = usdkrw

            conn.execute(
                """
                INSERT INTO cash_balances(account_id, cash_name, currency, amount, avg_price_krw, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(account_id, cash_name, currency)
                DO UPDATE SET amount=excluded.amount, avg_price_krw=excluded.avg_price_krw, updated_at=excluded.updated_at
                """,
                (account_id, cash_name, cash_currency, new_amount, avg_price_krw, ts_utc),
            )

            preview["cash_delta"]["cash_name"] = cash_name
            preview["cash_delta"]["old_amount"] = old_amount
            preview["cash_delta"]["new_amount"] = new_amount

            if cash_currency == "USD" and usdkrw:
                conn.execute(
                    """
                    INSERT INTO fx_rates(base_currency, quote_currency, rate, source, updated_at)
                    VALUES('USD','KRW',?,?,?)
                    ON CONFLICT(base_currency, quote_currency)
                    DO UPDATE SET rate=excluded.rate, source=excluded.source, updated_at=excluded.updated_at
                    """,
                    (float(usdkrw), "trade_message", ts_utc),
                )

            preview["status"] = "applied"

        conn.commit()
        return preview
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Update MyStock DB from Korean trade message text")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--input-file", default="", help="Input text file path")
    parser.add_argument("--text", default="", help="Raw message text")
    parser.add_argument("--account-id", default="", help="Override account id")
    parser.add_argument("--occurred-at", default="", help="Override occurred_at (ISO8601)")
    parser.add_argument("--source", default="telegram_stock", help="Source label")
    parser.add_argument("--usdkrw", type=float, default=None, help="Override USD/KRW rate")
    parser.add_argument("--apply", action="store_true", help="Apply DB updates")
    parser.add_argument("--pretty", action="store_true", help="Pretty JSON")
    args = parser.parse_args()

    if not args.text and not args.input_file:
        raise SystemExit("--text 또는 --input-file 중 하나는 필요합니다.")

    raw_text = (
        Path(args.input_file).read_text(encoding="utf-8")
        if args.input_file
        else args.text
    )
    result = apply_trade(
        db_path=Path(args.db).expanduser().resolve(),
        raw_text=raw_text,
        account_id_override=(args.account_id or None),
        occurred_at_override=(args.occurred_at or None),
        source=args.source,
        usdkrw_override=args.usdkrw,
        apply=args.apply,
    )

    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
