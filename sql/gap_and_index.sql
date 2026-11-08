-- First analysis layer, runnable with DuckDB straight on the CSVs:
--   duckdb -c ".read sql/gap_and_index.sql"

-- 1) Daily FX: official vs Binance P2P median, and the gap
-- Calendar days are Caracas days (UTC-4), set explicitly so results don't
-- depend on the machine's time zone (GitHub Actions runs in UTC).
--
-- The official rate is keyed by its value date (BCV "Fecha Valor"), not by the
-- collection time, because the BCV publishes it ahead of time. Prefer the
-- BCV's own page and fall back to dolarapi. Official rows without value_date
-- (collected before that column existed) are excluded, not guessed.
CREATE OR REPLACE VIEW fx_raw AS
SELECT *,
       CAST(timezone('America/Caracas', CAST(ts_utc AS TIMESTAMPTZ)) AS DATE) AS day_ccs,
       CAST(SUBSTR(value_date, 1, 10) AS DATE)                               AS value_day
FROM read_csv('data/raw/fx_rates.csv', header = true, auto_detect = true,
              types = {'value_date': 'VARCHAR', 'ts_utc': 'VARCHAR'});

CREATE OR REPLACE VIEW official_daily AS
SELECT value_day AS day,
       COALESCE(AVG(rate) FILTER (WHERE source = 'bcv'),
                AVG(rate) FILTER (WHERE source = 'dolarapi')) AS official
FROM fx_raw
WHERE rate_type = 'oficial' AND value_day IS NOT NULL
GROUP BY 1;

CREATE OR REPLACE VIEW p2p_daily AS
SELECT day_ccs AS day, AVG(rate) AS p2p_usdt
FROM fx_raw
WHERE source = 'binance_p2p' AND rate_type = 'usdt_sell'
GROUP BY 1;

-- The official rate "in force" on a day is the latest one whose value date is
-- on or before that day (a Friday rate applies over the weekend). This is the
-- legal rate, not an imputation. official_value_day shows which one was used.
CREATE OR REPLACE VIEW fx_daily AS
SELECT p.day, o.official, o.day AS official_value_day, p.p2p_usdt
FROM p2p_daily p ASOF LEFT JOIN official_daily o ON p.day >= o.day;

CREATE OR REPLACE VIEW fx_gap AS
SELECT day, official, official_value_day, p2p_usdt,
       (p2p_usdt / official - 1) * 100 AS gap_pct,
       LN(p2p_usdt) - LN(LAG(p2p_usdt) OVER (ORDER BY day)) AS p2p_log_return
FROM fx_daily;

-- 2) Basket index in Bs and USD
-- Stores show prices in their own currency (prices.currency). Each price is
-- converted with the official rate in force that Caracas day (same ASOF rule
-- as fx_daily), so every item has both a Bs and a USD price. Only rows with
-- status = 'ok' enter the index: no price, zero price, sold out, HTTP errors
-- and robots-blocked rows are gaps, not zeros. Items are keyed by (store, sku).
CREATE OR REPLACE VIEW prices_raw AS
SELECT *,
       CAST(timezone('America/Caracas', CAST(ts_utc AS TIMESTAMPTZ)) AS DATE) AS day
FROM read_csv('data/raw/prices.csv', header = true, auto_detect = true,
              types = {'ts_utc': 'VARCHAR', 'price': 'DOUBLE', 'http_status': 'INTEGER'});

CREATE OR REPLACE VIEW basket_daily AS
WITH obs AS (
    SELECT day, store, sku, category, currency, AVG(price) AS price
    FROM prices_raw
    WHERE status = 'ok' AND price > 0
    GROUP BY ALL
)
SELECT o.day, o.store, o.sku, o.category, o.currency, o.price,
       r.official,
       CASE o.currency WHEN 'VES' THEN o.price ELSE o.price * r.official END AS price_ves,
       CASE o.currency WHEN 'USD' THEN o.price ELSE o.price / r.official END AS price_usd
FROM obs o ASOF LEFT JOIN official_daily r ON o.day >= r.day;

-- Jevons index: geometric mean of each item's price relative to its own first
-- observation (base = 100). Items that enter later start at their first day.
CREATE OR REPLACE VIEW basket_index AS
WITH base AS (
    SELECT store, sku,
           FIRST(price_ves ORDER BY day) AS p0_ves,
           FIRST(price_usd ORDER BY day) AS p0_usd
    FROM basket_daily GROUP BY ALL
)
SELECT b.day,
       100 * EXP(AVG(LN(b.price_ves / base.p0_ves))) AS index_ves,
       100 * EXP(AVG(LN(b.price_usd / base.p0_usd))) AS index_usd,
       COUNT(*)                                       AS n_items
FROM basket_daily b JOIN base USING (store, sku)
GROUP BY b.day;

CREATE OR REPLACE VIEW basket_vs_fx AS
SELECT i.day, i.index_ves, i.index_usd, i.n_items, g.official, g.p2p_usdt, g.gap_pct
FROM basket_index i LEFT JOIN fx_gap g USING (day);
