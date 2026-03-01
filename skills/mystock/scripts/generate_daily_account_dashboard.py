#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
WINDOWS: list[tuple[str, int]] = [("D-1", 1), ("D-3", 3), ("D-7", 7), ("D-30", 30)]
BENCHMARKS = {
    "KOSPI": "^KS11",
    "KOSDAQ": "^KQ11",
    "NASDAQ": "^IXIC",
    "S&P500": "^GSPC",
}

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "mystock.db"
DEFAULT_OUTPUT_DIR = Path("/Users/assa_david/Documents/MyAswomeVault/10.WorkFlow/04.Investment/01.Status")


@dataclass
class Account:
    account_id: str
    account_type: str


@dataclass
class Holding:
    account_id: str
    ticker: str
    name: str
    currency: str
    quantity: float
    avg_buy_native: float | None
    avg_buy_krw: float | None


@dataclass
class Cash:
    account_id: str
    cash_name: str
    currency: str
    amount: float


@dataclass
class Baseline:
    snapshot_date: str
    value_krw: float


@dataclass
class DetailRow:
    account_id: str
    name: str
    ticker: str
    row_type: str
    currency: str
    quantity: float
    avg_buy_native: float | None
    current_price_native: float | None
    current_price_krw: float
    invested_krw: float | None
    current_value_krw: float
    pnl_krw: float | None
    pnl_pct: float | None


def now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone(KST)


def fmt_money(v: float | None) -> str:
    if v is None:
        return "NA"
    return f"{round(v):,}"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "NA"
    return f"{v:.2f}%"


def to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_yahoo_daily_closes(symbol: str, range_days: str = "1y") -> list[tuple[datetime, float]]:
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range={range_days}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return []

    result = (((data.get("chart") or {}).get("result")) or [None])[0]
    if not result:
        return []

    timestamps = result.get("timestamp") or []
    quotes = ((((result.get("indicators") or {}).get("quote")) or [None])[0]) or {}
    closes = quotes.get("close") or []

    out: list[tuple[datetime, float]] = []
    for ts, close_val in zip(timestamps, closes):
        if close_val is None:
            continue
        try:
            dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            out.append((dt, float(close_val)))
        except Exception:
            continue
    return out


def pick_last_close_point_before(
    closes: list[tuple[datetime, float]], target_utc: datetime
) -> tuple[datetime, float] | None:
    for dt, px in reversed(closes):
        if dt <= target_utc:
            return (dt, px)
    return None


def choose_kr_symbol_close(ticker: str, target_utc: datetime) -> tuple[datetime, float] | None:
    if not (ticker.isdigit() and len(ticker) == 6):
        return None
    ks = pick_last_close_point_before(fetch_yahoo_daily_closes(f"{ticker}.KS", "1y"), target_utc)
    kq = pick_last_close_point_before(fetch_yahoo_daily_closes(f"{ticker}.KQ", "1y"), target_utc)
    if ks and kq:
        return ks if ks[0] >= kq[0] else kq
    return ks or kq


def load_accounts(conn: sqlite3.Connection) -> list[Account]:
    rows = conn.execute(
        "SELECT account_id, account_type FROM accounts ORDER BY account_id"
    ).fetchall()
    return [Account(str(a), str(t)) for a, t in rows]


def load_holdings(conn: sqlite3.Connection) -> list[Holding]:
    rows = conn.execute(
        """
        SELECT h.account_id,
               i.ticker,
               i.name,
               i.currency,
               h.quantity,
               h.avg_buy_price_native,
               h.avg_buy_price_krw
        FROM holdings h
        JOIN instruments i ON i.instrument_id=h.instrument_id
        ORDER BY h.account_id, i.name
        """
    ).fetchall()
    out: list[Holding] = []
    for account_id, ticker, name, currency, qty, avg_native, avg_krw in rows:
        out.append(
            Holding(
                account_id=str(account_id),
                ticker=str(ticker or ""),
                name=str(name or ""),
                currency=str(currency or "KRW").upper(),
                quantity=float(qty or 0.0),
                avg_buy_native=None if avg_native is None else float(avg_native),
                avg_buy_krw=None if avg_krw is None else float(avg_krw),
            )
        )
    return out


def load_cash(conn: sqlite3.Connection) -> list[Cash]:
    rows = conn.execute(
        """
        SELECT account_id, cash_name, currency, amount
        FROM cash_balances
        ORDER BY account_id, cash_name, currency
        """
    ).fetchall()
    return [
        Cash(str(a), str(n), str(c or "KRW").upper(), float(amt or 0.0))
        for a, n, c, amt in rows
    ]


def ensure_snapshot_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS account_value_snapshots (
            snapshot_date TEXT NOT NULL,
            account_id TEXT NOT NULL,
            snapshot_at_utc TEXT NOT NULL,
            value_krw REAL NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(snapshot_date, account_id)
        );
        CREATE INDEX IF NOT EXISTS idx_account_value_snapshots_account_date
            ON account_value_snapshots(account_id, snapshot_date);
        """
    )


def upsert_live_fx(conn: sqlite3.Connection, usdkrw: float, source: str, ts_utc: str) -> None:
    conn.execute(
        """
        INSERT INTO fx_rates(base_currency, quote_currency, rate, source, updated_at)
        VALUES('USD','KRW',?,?,?)
        ON CONFLICT(base_currency, quote_currency)
        DO UPDATE SET rate=excluded.rate, source=excluded.source, updated_at=excluded.updated_at
        """,
        (float(usdkrw), source, ts_utc),
    )


def insert_or_update_snapshots(
    conn: sqlite3.Connection,
    snapshot_date: str,
    snapshot_at_utc: str,
    account_values_krw: dict[str, float],
    mode: str,
) -> None:
    rows = [
        (snapshot_date, account_id, snapshot_at_utc, float(val), snapshot_at_utc)
        for account_id, val in account_values_krw.items()
    ]
    if mode == "ignore":
        conn.executemany(
            """
            INSERT OR IGNORE INTO account_value_snapshots
            (snapshot_date, account_id, snapshot_at_utc, value_krw, created_at)
            VALUES(?,?,?,?,?)
            """,
            rows,
        )
        return

    conn.executemany(
        """
        INSERT INTO account_value_snapshots
        (snapshot_date, account_id, snapshot_at_utc, value_krw, created_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(snapshot_date, account_id)
        DO UPDATE SET
          snapshot_at_utc=excluded.snapshot_at_utc,
          value_krw=excluded.value_krw,
          created_at=excluded.created_at
        """,
        rows,
    )


def fetch_usdkrw_spot(target_kst: datetime, fallback: float) -> float:
    pt = pick_last_close_point_before(
        fetch_yahoo_daily_closes("KRW=X", range_days="1y"),
        target_kst.astimezone(timezone.utc),
    )
    if pt is None:
        return fallback
    return float(pt[1])


def latest_fx_fallback(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        """
        SELECT rate FROM fx_rates
        WHERE base_currency='USD' AND quote_currency='KRW'
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return 1400.0


def get_price_krw(
    holding: Holding,
    target_kst: datetime,
    usdkrw: float,
    us_cache: dict[str, float],
    kr_cache: dict[str, float],
) -> float:
    target_utc = target_kst.astimezone(timezone.utc)
    ccy = holding.currency
    tk = holding.ticker.strip().upper()

    if ccy == "USD" and tk:
        if tk not in us_cache:
            pt = pick_last_close_point_before(fetch_yahoo_daily_closes(tk, "1y"), target_utc)
            us_cache[tk] = float(pt[1]) if pt else float("nan")
        px_usd = us_cache[tk]
        if px_usd == px_usd:
            return px_usd * usdkrw

        if holding.avg_buy_native is not None:
            return float(holding.avg_buy_native) * usdkrw
        if holding.avg_buy_krw is not None:
            return float(holding.avg_buy_krw)
        return 0.0

    if ccy == "KRW":
        if tk.isdigit() and len(tk) == 6:
            if tk not in kr_cache:
                pt = choose_kr_symbol_close(tk, target_utc)
                kr_cache[tk] = float(pt[1]) if pt else float("nan")
            px_krw = kr_cache[tk]
            if px_krw == px_krw:
                return px_krw

        if holding.avg_buy_native is not None:
            return float(holding.avg_buy_native)
        if holding.avg_buy_krw is not None:
            return float(holding.avg_buy_krw)
        return 0.0

    if holding.avg_buy_native is not None:
        return float(holding.avg_buy_native)
    if holding.avg_buy_krw is not None:
        return float(holding.avg_buy_krw)
    return 0.0


def compute_account_values_krw(
    accounts: list[Account],
    holdings: list[Holding],
    cash_rows: list[Cash],
    target_kst: datetime,
    usdkrw: float,
) -> tuple[dict[str, float], dict[str, int]]:
    account_values = {a.account_id: 0.0 for a in accounts}
    missing_price_count = {a.account_id: 0 for a in accounts}

    us_cache: dict[str, float] = {}
    kr_cache: dict[str, float] = {}

    for h in holdings:
        qty = float(h.quantity or 0.0)
        if abs(qty) < 1e-12:
            continue
        price_krw = get_price_krw(h, target_kst, usdkrw, us_cache, kr_cache)
        if price_krw <= 0:
            missing_price_count[h.account_id] = missing_price_count.get(h.account_id, 0) + 1
        account_values[h.account_id] = account_values.get(h.account_id, 0.0) + (qty * price_krw)

    for c in cash_rows:
        if c.currency == "USD":
            account_values[c.account_id] = account_values.get(c.account_id, 0.0) + (c.amount * usdkrw)
        else:
            account_values[c.account_id] = account_values.get(c.account_id, 0.0) + c.amount

    return account_values, missing_price_count


def get_price_native(
    holding: Holding,
    target_kst: datetime,
    us_cache: dict[str, float],
    kr_cache: dict[str, float],
) -> float | None:
    target_utc = target_kst.astimezone(timezone.utc)
    ccy = holding.currency
    tk = holding.ticker.strip().upper()

    if ccy == "USD" and tk:
        if tk not in us_cache:
            pt = pick_last_close_point_before(fetch_yahoo_daily_closes(tk, "1y"), target_utc)
            us_cache[tk] = float(pt[1]) if pt else float("nan")
        px = us_cache[tk]
        if px == px:
            return px
        return holding.avg_buy_native

    if ccy == "KRW":
        if tk.isdigit() and len(tk) == 6:
            if tk not in kr_cache:
                pt = choose_kr_symbol_close(tk, target_utc)
                kr_cache[tk] = float(pt[1]) if pt else float("nan")
            px = kr_cache[tk]
            if px == px:
                return px
        if holding.avg_buy_native is not None:
            return holding.avg_buy_native
        return holding.avg_buy_krw

    return holding.avg_buy_native if holding.avg_buy_native is not None else holding.avg_buy_krw


def build_detail_rows(
    accounts: list[Account],
    holdings: list[Holding],
    cash_rows: list[Cash],
    target_kst: datetime,
    usdkrw: float,
) -> dict[str, list[DetailRow]]:
    out: dict[str, list[DetailRow]] = {a.account_id: [] for a in accounts}
    us_cache: dict[str, float] = {}
    kr_cache: dict[str, float] = {}

    for h in holdings:
        qty = float(h.quantity or 0.0)
        if abs(qty) < 1e-12:
            continue
        px_native = get_price_native(h, target_kst, us_cache, kr_cache)
        if px_native is None:
            px_native = 0.0
        if h.currency == "USD":
            px_krw = float(px_native) * usdkrw
            avg_buy_krw = (
                float(h.avg_buy_native) * usdkrw if h.avg_buy_native is not None
                else (float(h.avg_buy_krw) if h.avg_buy_krw is not None else 0.0)
            )
        else:
            px_krw = float(px_native)
            avg_buy_krw = (
                float(h.avg_buy_native) if h.avg_buy_native is not None
                else (float(h.avg_buy_krw) if h.avg_buy_krw is not None else 0.0)
            )
        invested_krw = qty * avg_buy_krw if avg_buy_krw > 0 else None
        current_value_krw = qty * px_krw
        pnl_krw = None if invested_krw is None else (current_value_krw - invested_krw)
        pnl_pct = None if (invested_krw is None or invested_krw <= 0) else (pnl_krw / invested_krw) * 100.0
        out.setdefault(h.account_id, []).append(
            DetailRow(
                account_id=h.account_id,
                name=h.name,
                ticker=h.ticker,
                row_type="HOLDING",
                currency=h.currency,
                quantity=qty,
                avg_buy_native=h.avg_buy_native if h.avg_buy_native is not None else h.avg_buy_krw,
                current_price_native=float(px_native),
                current_price_krw=px_krw,
                invested_krw=invested_krw,
                current_value_krw=current_value_krw,
                pnl_krw=pnl_krw,
                pnl_pct=pnl_pct,
            )
        )

    for c in cash_rows:
        if abs(c.amount) < 1e-12:
            continue
        if c.currency == "USD":
            current_price_native = 1.0
            current_price_krw = usdkrw
            avg_buy_native = 1.0
        else:
            current_price_native = 1.0
            current_price_krw = 1.0
            avg_buy_native = 1.0
        out.setdefault(c.account_id, []).append(
            DetailRow(
                account_id=c.account_id,
                name=c.cash_name,
                ticker=c.currency,
                row_type="CASH",
                currency=c.currency,
                quantity=float(c.amount),
                avg_buy_native=avg_buy_native,
                current_price_native=current_price_native,
                current_price_krw=current_price_krw,
                invested_krw=None,
                current_value_krw=float(c.amount) * current_price_krw,
                pnl_krw=None,
                pnl_pct=None,
            )
        )

    for account_id in out:
        out[account_id].sort(key=lambda r: (r.row_type == "CASH", r.name))

    return out


def load_latest_baseline_for_account(
    conn: sqlite3.Connection, account_id: str, target_date: str
) -> Baseline | None:
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
    if not row:
        return None
    return Baseline(snapshot_date=str(row[0]), value_krw=float(row[1]))


def load_latest_baseline_for_total(
    conn: sqlite3.Connection, account_ids: list[str], target_date: str
) -> Baseline | None:
    if not account_ids:
        return None
    q_ids = ",".join("?" for _ in account_ids)
    row = conn.execute(
        f"""
        SELECT snapshot_date,
               SUM(value_krw) AS total_value,
               COUNT(DISTINCT account_id) AS n_accounts
        FROM account_value_snapshots
        WHERE snapshot_date<=?
          AND account_id IN ({q_ids})
        GROUP BY snapshot_date
        HAVING n_accounts=?
        ORDER BY snapshot_date DESC
        LIMIT 1
        """,
        [target_date, *account_ids, len(account_ids)],
    ).fetchone()
    if not row:
        return None
    return Baseline(snapshot_date=str(row[0]), value_krw=float(row[1]))


def calc_return(curr: float, base: float | None) -> float | None:
    if base is None or base <= 0:
        return None
    return ((curr / base) - 1.0) * 100.0


def build_benchmark_returns(generated_kst: datetime) -> dict[str, dict[str, float | None]]:
    target_utc = generated_kst.astimezone(timezone.utc)
    out: dict[str, dict[str, float | None]] = {}

    for name, symbol in BENCHMARKS.items():
        closes = fetch_yahoo_daily_closes(symbol, range_days="1y")
        curr_pt = pick_last_close_point_before(closes, target_utc)
        ret_map: dict[str, float | None] = {}
        for label, days in WINDOWS:
            if curr_pt is None:
                ret_map[label] = None
                continue
            past_pt = pick_last_close_point_before(closes, target_utc - timedelta(days=days))
            if past_pt is None or past_pt[1] == 0:
                ret_map[label] = None
            else:
                ret_map[label] = ((curr_pt[1] / past_pt[1]) - 1.0) * 100.0
        out[name] = ret_map

    return out


def render_markdown(
    generated_kst: datetime,
    db_path: Path,
    usdkrw: float,
    accounts: list[Account],
    account_values_krw: dict[str, float],
    missing_price_count: dict[str, int],
    returns_by_account: dict[str, dict[str, float | None]],
    baseline_date_by_account: dict[str, dict[str, str | None]],
    total_returns: dict[str, float | None],
    total_baseline_date: dict[str, str | None],
    benchmark_returns: dict[str, dict[str, float | None]],
    detail_rows_map: dict[str, list[DetailRow]],
) -> str:
    today_s = generated_kst.strftime("%Y-%m-%d")
    lines: list[str] = []
    lines.append("---")
    lines.append(f"Date: {today_s}")
    lines.append(f"Time: {generated_kst.strftime('%H:%M:%S')}")
    lines.append("Folder: 10.WorkFlow/04.Investment/01.Status")
    lines.append("Report Type: total-account-monitor")
    lines.append(f"Snapshot Time: {generated_kst.strftime('%Y-%m-%d %H:%M:%S')} KST")
    lines.append(f"Data Source: {db_path.as_posix()}")
    lines.append(f"USD/KRW: {usdkrw:.4f}")
    lines.append("Benchmarks: [KOSPI, KOSDAQ, NASDAQ, S&P500]")
    lines.append("---")
    lines.append("")
    lines.append(f"# Daily Investment Status ({today_s})")
    lines.append("")
    lines.append("- 기준: **개별 종목 등락이 아니라 계좌 총액(KRW) 변동률**")
    lines.append("- US 자산/현금은 당일 USD/KRW로 KRW 환산")
    lines.append("")

    lines.append("## 1) 계좌 총액 변동")
    lines.append("")
    lines.append("| Account | Current (KRW) | D-1 | D-3 | D-7 | D-30 | Missing Price Count |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for a in accounts:
        aid = a.account_id
        ret_map = returns_by_account.get(aid, {})
        lines.append(
            f"| {aid} | {fmt_money(account_values_krw.get(aid, 0.0))} | "
            f"{fmt_pct(ret_map.get('D-1'))} | {fmt_pct(ret_map.get('D-3'))} | "
            f"{fmt_pct(ret_map.get('D-7'))} | {fmt_pct(ret_map.get('D-30'))} | "
            f"{missing_price_count.get(aid, 0)} |"
        )

    total_now = sum(account_values_krw.get(a.account_id, 0.0) for a in accounts)
    lines.append(
        f"| **TOTAL** | **{fmt_money(total_now)}** | **{fmt_pct(total_returns.get('D-1'))}** | "
        f"**{fmt_pct(total_returns.get('D-3'))}** | **{fmt_pct(total_returns.get('D-7'))}** | "
        f"**{fmt_pct(total_returns.get('D-30'))}** | - |"
    )
    lines.append("")

    lines.append("## 2) 비교 기준일 (실제 사용된 스냅샷 날짜)")
    lines.append("")
    lines.append("| Account | D-1 Baseline | D-3 Baseline | D-7 Baseline | D-30 Baseline |")
    lines.append("| --- | --- | --- | --- | --- |")
    for a in accounts:
        aid = a.account_id
        b = baseline_date_by_account.get(aid, {})
        lines.append(
            f"| {aid} | {b.get('D-1') or 'NA'} | {b.get('D-3') or 'NA'} | {b.get('D-7') or 'NA'} | {b.get('D-30') or 'NA'} |"
        )
    lines.append(
        f"| **TOTAL(full coverage)** | **{total_baseline_date.get('D-1') or 'NA'}** | "
        f"**{total_baseline_date.get('D-3') or 'NA'}** | **{total_baseline_date.get('D-7') or 'NA'}** | "
        f"**{total_baseline_date.get('D-30') or 'NA'}** |"
    )
    lines.append("")

    lines.append("## 3) 지수 등락률")
    lines.append("")
    lines.append("| Window | KOSPI | KOSDAQ | NASDAQ | S&P500 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for label, _ in WINDOWS:
        lines.append(
            f"| {label} | {fmt_pct(benchmark_returns.get('KOSPI', {}).get(label))} | "
            f"{fmt_pct(benchmark_returns.get('KOSDAQ', {}).get(label))} | "
            f"{fmt_pct(benchmark_returns.get('NASDAQ', {}).get(label))} | "
            f"{fmt_pct(benchmark_returns.get('S&P500', {}).get(label))} |"
        )
    lines.append("")

    lines.append("## 4) TOTAL vs 지수 (Alpha)")
    lines.append("")
    lines.append("| Window | vs KOSPI | vs KOSDAQ | vs NASDAQ | vs S&P500 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for label, _ in WINDOWS:
        t = total_returns.get(label)
        vals: list[str] = []
        for bm in ["KOSPI", "KOSDAQ", "NASDAQ", "S&P500"]:
            b = benchmark_returns.get(bm, {}).get(label)
            vals.append(fmt_pct(None if (t is None or b is None) else (t - b)))
        lines.append(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} | {vals[3]} |")
    lines.append("")

    lines.append("## 5) 자동화 메모")
    lines.append("")
    lines.append("- 이 리포트는 `mystock.db` 기준으로 매일 재생성")
    lines.append("- 파일: `DASHBOARD_DAILY_YYYYMMDD.md` + `DASHBOARD_DAILY_LATEST.md`")
    lines.append("")

    lines.append("## 6) 오늘 시점 계좌 세부 내역 (전체)")
    lines.append("")
    lines.append(f"- 기준시각: `{generated_kst.strftime('%Y-%m-%d %H:%M:%S')} KST`")
    lines.append("- 환산: USD 항목은 오늘 USD/KRW 적용")
    lines.append("")
    for a in accounts:
        lines.append(f"### {a.account_id}")
        lines.append(
            "| Name | Ticker | Type | Currency | Quantity | Avg Buy (Native) | Current Price (Native) | Current Price (KRW) | Current Value (KRW) | P/L (KRW) | P/L (%) |"
        )
        lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        rows = detail_rows_map.get(a.account_id, [])
        if not rows:
            lines.append("| NA | NA | NA | NA | NA | NA | NA | NA | NA | NA | NA |")
            lines.append("")
            continue
        subtotal = 0.0
        for r in rows:
            avg_native = "NA" if r.avg_buy_native is None else f"{r.avg_buy_native:,.4f}".rstrip("0").rstrip(".")
            cur_native = (
                "NA"
                if r.current_price_native is None
                else f"{r.current_price_native:,.4f}".rstrip("0").rstrip(".")
            )
            qty_s = f"{r.quantity:,.4f}".rstrip("0").rstrip(".")
            lines.append(
                f"| {r.name} | {r.ticker} | {r.row_type} | {r.currency} | {qty_s} | "
                f"{avg_native} | {cur_native} | {fmt_money(r.current_price_krw)} | {fmt_money(r.current_value_krw)} | "
                f"{fmt_money(r.pnl_krw)} | {fmt_pct(r.pnl_pct)} |"
            )
            subtotal += r.current_value_krw
        lines.append(f"| **Total** | - | - | KRW | - | - | - | - | **{fmt_money(subtotal)}** | - | - |")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily total-account dashboard from mystock.db")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Obsidian output directory")
    parser.add_argument("--filename", default="", help="Optional output filename")
    parser.add_argument("--latest-filename", default="DASHBOARD_DAILY_LATEST.md", help="Latest file name")
    parser.add_argument(
        "--snapshot-mode",
        choices=["upsert", "ignore"],
        default="upsert",
        help="How to write today's account_value_snapshots rows",
    )
    parser.add_argument(
        "--fx-source",
        default="daily_dashboard_live",
        help="source field to store in fx_rates",
    )
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    generated = now_kst()
    snapshot_date = generated.strftime("%Y%m%d")
    snapshot_at_utc = to_iso_z(generated)

    conn = sqlite3.connect(db_path)
    try:
        accounts = load_accounts(conn)
        holdings = load_holdings(conn)
        cash_rows = load_cash(conn)

        usdkrw_default = latest_fx_fallback(conn)
        usdkrw_live = fetch_usdkrw_spot(generated, fallback=usdkrw_default)

        account_values_krw, missing_price_count = compute_account_values_krw(
            accounts=accounts,
            holdings=holdings,
            cash_rows=cash_rows,
            target_kst=generated,
            usdkrw=usdkrw_live,
        )
        detail_rows_map = build_detail_rows(
            accounts=accounts,
            holdings=holdings,
            cash_rows=cash_rows,
            target_kst=generated,
            usdkrw=usdkrw_live,
        )

        ensure_snapshot_schema(conn)
        upsert_live_fx(conn, usdkrw_live, args.fx_source, snapshot_at_utc)
        insert_or_update_snapshots(
            conn,
            snapshot_date=snapshot_date,
            snapshot_at_utc=snapshot_at_utc,
            account_values_krw=account_values_krw,
            mode=args.snapshot_mode,
        )
        conn.commit()

        returns_by_account: dict[str, dict[str, float | None]] = {}
        baseline_date_by_account: dict[str, dict[str, str | None]] = {}
        total_returns: dict[str, float | None] = {}
        total_baseline_date: dict[str, str | None] = {}

        account_ids = [a.account_id for a in accounts]
        total_now = sum(account_values_krw.get(aid, 0.0) for aid in account_ids)

        for a in accounts:
            aid = a.account_id
            curr = account_values_krw.get(aid, 0.0)
            returns_by_account[aid] = {}
            baseline_date_by_account[aid] = {}
            for label, days in WINDOWS:
                target_date = (generated - timedelta(days=days)).strftime("%Y%m%d")
                baseline = load_latest_baseline_for_account(conn, aid, target_date)
                baseline_date_by_account[aid][label] = baseline.snapshot_date if baseline else None
                returns_by_account[aid][label] = calc_return(curr, baseline.value_krw if baseline else None)

        for label, days in WINDOWS:
            target_date = (generated - timedelta(days=days)).strftime("%Y%m%d")
            baseline = load_latest_baseline_for_total(conn, account_ids, target_date)
            total_baseline_date[label] = baseline.snapshot_date if baseline else None
            total_returns[label] = calc_return(total_now, baseline.value_krw if baseline else None)

    finally:
        conn.close()

    benchmark_returns = build_benchmark_returns(generated)

    md = render_markdown(
        generated_kst=generated,
        db_path=db_path,
        usdkrw=usdkrw_live,
        accounts=accounts,
        account_values_krw=account_values_krw,
        missing_price_count=missing_price_count,
        returns_by_account=returns_by_account,
        baseline_date_by_account=baseline_date_by_account,
        total_returns=total_returns,
        total_baseline_date=total_baseline_date,
        benchmark_returns=benchmark_returns,
        detail_rows_map=detail_rows_map,
    )

    filename = args.filename or f"DASHBOARD_DAILY_{generated.strftime('%Y%m%d')}.md"
    out_file = out_dir / filename
    out_file.write_text(md, encoding="utf-8")

    latest_file = out_dir / args.latest_filename
    latest_file.write_text(md, encoding="utf-8")

    print(out_file.as_posix())


if __name__ == "__main__":
    main()
