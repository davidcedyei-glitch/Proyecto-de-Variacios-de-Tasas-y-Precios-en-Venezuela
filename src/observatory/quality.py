"""Data-quality checks on the raw CSVs.

    python -m observatory.run quality

Checks never modify or drop data: they only report. Each finding has a level:
- error: the data is wrong as stored (exact duplicates, non-positive prices or
  rates). Should not happen; investigate the collector.
- warn:  plausible but suspicious (absurd jumps, implausible USD prices, a
  product that stopped returning a price, low coverage, stale FX, official
  sources that disagree). Needs a human look before analysis.
- info:  context (rows of items no longer in the basket, zero-price counts).

Thresholds are module constants so they are visible and easy to tune.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

SQL_FILE = Path(__file__).resolve().parents[2] / "sql" / "gap_and_index.sql"

PRICE_JUMP = 1.5            # day-over-day x1.5 up or /1.5 down in own currency
USD_MIN, USD_MAX = 0.05, 500.0   # plausible USD range for one basket item
MISSING_DAYS = 3            # no valid price on the store's last N collection days
MIN_COVERAGE = 0.8          # share of configured items with a valid price per store-day
DUP_PRICE_SPREAD = 0.05     # same item, same day, prices differ by more than 5 %
FX_JUMP = 0.20              # consecutive observations of one series differ > 20 %
OFFICIAL_DISAGREE = 0.01    # bcv vs dolarapi on the same value date differ > 1 %
FX_STALE_HOURS = 12         # last FX row older than this
MIN_P2P_ADS = 5


@dataclass(frozen=True)
class Finding:
    level: str
    check: str
    subject: str
    detail: str


def _connect(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """Load the analysis views from sql/gap_and_index.sql on `data_dir`."""
    con = duckdb.connect()
    sql = SQL_FILE.read_text(encoding="utf-8").replace("data/raw/", data_dir.as_posix().rstrip("/") + "/")
    have_prices = (data_dir / "prices.csv").exists()
    for stmt in sql.split(";"):
        if not stmt.strip():
            continue
        if not have_prices and ("prices" in stmt or "basket" in stmt):
            continue
        con.execute(stmt)
    return con


def _rows(con, sql: str, params: list | None = None) -> list[tuple]:
    return con.execute(sql, params or []).fetchall()


def check_prices(con, config: dict) -> list[Finding]:
    out: list[Finding] = []

    for store, sku, ts, n in _rows(con, """
        SELECT store, sku, ts_utc, COUNT(*) FROM prices_raw
        GROUP BY ALL HAVING COUNT(*) > 1"""):
        out.append(Finding("error", "duplicate_row", f"{store}/{sku}", f"{n} rows with ts_utc={ts}"))

    for store, sku, day, lo, hi in _rows(con, """
        SELECT store, sku, day, MIN(price), MAX(price) FROM prices_raw
        WHERE status = 'ok' GROUP BY ALL
        HAVING COUNT(*) > 1 AND MAX(price) > MIN(price) * (1 + ?)""", [DUP_PRICE_SPREAD]):
        out.append(Finding("warn", "same_day_conflict", f"{store}/{sku}",
                           f"{day}: valid prices from {lo} to {hi} on the same day"))

    for store, sku, ts, price in _rows(con, """
        SELECT store, sku, ts_utc, price FROM prices_raw
        WHERE status = 'ok' AND (price IS NULL OR price <= 0)"""):
        out.append(Finding("error", "non_positive_price", f"{store}/{sku}", f"status ok but price={price} at {ts}"))

    for store, n in _rows(con, """
        SELECT store, COUNT(*) FROM prices_raw WHERE status = 'zero_price' GROUP BY 1 ORDER BY 1"""):
        out.append(Finding("info", "zero_price_rows", store, f"{n} rows where the store showed a price of 0 (kept as gaps)"))

    for store, sku, day, cur, prev, price, usd_prev, usd in _rows(con, """
        WITH s AS (
            SELECT store, sku, day, currency, price, price_usd,
                   LAG(price) OVER w AS prev, LAG(price_usd) OVER w AS usd_prev
            FROM basket_daily WINDOW w AS (PARTITION BY store, sku ORDER BY day))
        SELECT store, sku, day, currency, prev, price, usd_prev, price_usd FROM s
        WHERE prev IS NOT NULL AND ABS(LN(price / prev)) > LN(?)
        ORDER BY store, sku, day""", [PRICE_JUMP]):
        usd_txt = f"; in USD {usd_prev:.2f} -> {usd:.2f}" if usd_prev and usd else ""
        out.append(Finding("warn", "price_jump", f"{store}/{sku}",
                           f"{day}: {prev} -> {price} {cur} (x{price / prev:.2f}){usd_txt}"))

    for store, sku, day, price, cur, usd in _rows(con, """
        SELECT store, sku, day, price, currency, price_usd FROM basket_daily
        WHERE price_usd IS NOT NULL AND (price_usd < ? OR price_usd > ?)
        ORDER BY store, sku, day""", [USD_MIN, USD_MAX]):
        out.append(Finding("warn", "implausible_usd_price", f"{store}/{sku}",
                           f"{day}: {price} {cur} = USD {usd:.3f} (outside {USD_MIN}-{USD_MAX})"))

    # Products that disappear: configured items without a valid price on the
    # store's last MISSING_DAYS collection days, or whose last answer was 404/410.
    configured = [(s["name"], i["sku"]) for s in config.get("stores", []) for i in s.get("items", [])]
    con.execute("CREATE OR REPLACE TEMP TABLE cfg(store VARCHAR, sku VARCHAR)")
    if configured:
        con.executemany("INSERT INTO cfg VALUES (?, ?)", configured)

    for store, sku, last_ok, last_status in _rows(con, """
        WITH store_days AS (
            SELECT store, day, DENSE_RANK() OVER (PARTITION BY store ORDER BY day DESC) AS k
            FROM (SELECT DISTINCT store, day FROM prices_raw)),
        recent AS (SELECT store, day FROM store_days WHERE k <= ?),
        n_recent AS (SELECT store, COUNT(*) AS n FROM recent GROUP BY 1),
        ok_recent AS (
            SELECT DISTINCT p.store, p.sku FROM prices_raw p JOIN recent r USING (store, day)
            WHERE p.status = 'ok' AND p.price > 0),
        last_ok AS (SELECT store, sku, MAX(day) AS d FROM prices_raw WHERE status = 'ok' AND price > 0 GROUP BY ALL),
        last_status AS (SELECT store, sku, LAST(status ORDER BY ts_utc) AS st FROM prices_raw GROUP BY ALL)
        SELECT c.store, c.sku, lo.d, ls.st
        FROM cfg c JOIN n_recent n ON n.store = c.store AND n.n >= ?
        LEFT JOIN ok_recent o ON o.store = c.store AND o.sku = c.sku
        LEFT JOIN last_ok lo ON lo.store = c.store AND lo.sku = c.sku
        LEFT JOIN last_status ls ON ls.store = c.store AND ls.sku = c.sku
        WHERE o.sku IS NULL ORDER BY 1, 2""", [MISSING_DAYS, MISSING_DAYS]):
        out.append(Finding("warn", "product_missing", f"{store}/{sku}",
                           f"no valid price on the last {MISSING_DAYS} collection days "
                           f"(last valid: {last_ok or 'never'}, last status: {last_status or 'never collected'})"))

    for store, sku, st, ts in _rows(con, """
        SELECT store, sku, LAST(status ORDER BY ts_utc), MAX(ts_utc) FROM prices_raw GROUP BY ALL
        HAVING LAST(status ORDER BY ts_utc) IN ('http_404', 'http_410') ORDER BY 1, 2"""):
        out.append(Finding("warn", "product_removed", f"{store}/{sku}", f"last answer {st} at {ts}: page gone, update the URL or retire the item"))

    for store, sku in _rows(con, """
        SELECT c.store, c.sku FROM cfg c
        WHERE c.store IN (SELECT DISTINCT store FROM prices_raw)
          AND NOT EXISTS (SELECT 1 FROM prices_raw p WHERE p.store = c.store AND p.sku = c.sku)
        ORDER BY 1, 2"""):
        out.append(Finding("info", "never_collected", f"{store}/{sku}", "configured but no rows yet (added recently?)"))

    for store, n, first, last in _rows(con, """
        SELECT p.store, COUNT(DISTINCT p.sku), MIN(p.day), MAX(p.day) FROM prices_raw p
        LEFT JOIN cfg c USING (store, sku) WHERE c.sku IS NULL GROUP BY 1 ORDER BY 1"""):
        out.append(Finding("info", "not_in_basket", store,
                           f"{n} items with rows ({first} to {last}) are no longer in config/basket.yml; their rows are kept and still enter the index on the days they have"))

    for store, day, ok, total in _rows(con, """
        WITH per_day AS (
            SELECT p.store, p.day, COUNT(DISTINCT p.sku) FILTER (WHERE p.status = 'ok' AND p.price > 0) AS ok
            FROM prices_raw p JOIN cfg c USING (store, sku) GROUP BY ALL),
        size AS (SELECT store, COUNT(*) AS total FROM cfg GROUP BY 1)
        SELECT d.store, d.day, d.ok, s.total FROM per_day d JOIN size s USING (store)
        WHERE d.ok < ? * s.total ORDER BY 1, 2""", [MIN_COVERAGE]):
        out.append(Finding("warn", "low_coverage", f"{store}",
                           f"{day}: valid prices for {ok}/{total} configured items (< {MIN_COVERAGE:.0%}); layout change or block?"))
    return out


def check_fx(con, now: datetime | None = None) -> list[Finding]:
    out: list[Finding] = []
    now = now or datetime.now(timezone.utc)

    for ts, src, rt, n in _rows(con, """
        SELECT ts_utc, source, rate_type, COUNT(*) FROM fx_raw GROUP BY ALL HAVING COUNT(*) > 1"""):
        out.append(Finding("error", "fx_duplicate_row", f"{src}/{rt}", f"{n} rows with ts_utc={ts}"))

    for ts, src, rt, rate in _rows(con, "SELECT ts_utc, source, rate_type, rate FROM fx_raw WHERE rate IS NULL OR rate <= 0"):
        out.append(Finding("error", "fx_non_positive_rate", f"{src}/{rt}", f"rate={rate} at {ts}"))

    for ts, src, rt, prev, rate in _rows(con, """
        WITH s AS (
            SELECT ts_utc, source, rate_type, rate,
                   LAG(rate) OVER (PARTITION BY source, rate_type ORDER BY ts_utc) AS prev
            FROM fx_raw WHERE rate > 0)
        SELECT ts_utc, source, rate_type, prev, rate FROM s
        WHERE prev IS NOT NULL AND ABS(rate / prev - 1) > ? ORDER BY 2, 3, 1""", [FX_JUMP]):
        out.append(Finding("warn", "fx_jump", f"{src}/{rt}", f"{ts}: {prev} -> {rate} ({rate / prev - 1:+.1%})"))

    for day, bcv, api in _rows(con, """
        SELECT value_day,
               AVG(rate) FILTER (WHERE source = 'bcv'), AVG(rate) FILTER (WHERE source = 'dolarapi')
        FROM fx_raw WHERE rate_type = 'oficial' AND value_day IS NOT NULL GROUP BY 1
        HAVING COUNT(*) FILTER (WHERE source = 'bcv') > 0 AND COUNT(*) FILTER (WHERE source = 'dolarapi') > 0
           AND ABS(AVG(rate) FILTER (WHERE source = 'bcv') / AVG(rate) FILTER (WHERE source = 'dolarapi') - 1) > ?
        ORDER BY 1""", [OFFICIAL_DISAGREE]):
        out.append(Finding("warn", "official_sources_disagree", "oficial", f"value date {day}: bcv {bcv} vs dolarapi {api}"))

    for ts, rt, n in _rows(con, """
        SELECT ts_utc, rate_type, n_ads FROM fx_raw WHERE source = 'binance_p2p' AND n_ads < ? ORDER BY 1""",
                           [MIN_P2P_ADS]):
        out.append(Finding("warn", "p2p_few_ads", f"binance_p2p/{rt}", f"{ts}: only {n} ads in the median"))

    for src, rt, last in _rows(con, "SELECT source, rate_type, MAX(ts_utc) FROM fx_raw GROUP BY ALL ORDER BY 1, 2"):
        age_h = (now - datetime.fromisoformat(last)).total_seconds() / 3600
        if age_h > FX_STALE_HOURS:
            out.append(Finding("warn", "fx_stale", f"{src}/{rt}", f"last row {last} ({age_h:.0f} h ago)"))
    return out


def run_checks(data_dir: Path, config_path: Path, now: datetime | None = None) -> list[Finding]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    has_fx, has_prices = (data_dir / "fx_rates.csv").exists(), (data_dir / "prices.csv").exists()
    if not has_fx:
        # The price views convert currencies with the FX table, so nothing runs without it.
        return [Finding("warn", "fx_missing", str(data_dir), "fx_rates.csv not found; no checks run")] if has_prices else []
    con = _connect(data_dir)
    findings = check_fx(con, now)
    if has_prices:
        findings += check_prices(con, config)
    order = {"error": 0, "warn": 1, "info": 2}
    return sorted(findings, key=lambda f: (order[f.level], f.check, f.subject))


def to_markdown(findings: list[Finding], generated: datetime | None = None) -> str:
    generated = generated or datetime.now(timezone.utc)
    counts = {lvl: sum(f.level == lvl for f in findings) for lvl in ("error", "warn", "info")}
    lines = [
        "# Data-quality report",
        "",
        f"Generated {generated.isoformat(timespec='seconds')} by `python -m observatory.run quality`.",
        f"**{counts['error']} errors, {counts['warn']} warnings, {counts['info']} notes.** "
        "Nothing is modified or dropped; flagged rows stay in the raw data.",
        "",
    ]
    if not findings:
        lines.append("No findings.")
    else:
        lines += ["| level | check | subject | detail |", "|---|---|---|---|"]
        lines += [f"| {f.level} | {f.check} | {f.subject} | {f.detail.replace('|', '/')} |" for f in findings]
    return "\n".join(lines) + "\n"

