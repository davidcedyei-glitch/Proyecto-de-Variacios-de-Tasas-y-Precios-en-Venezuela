# Venezuela Price–FX Observatory

**Question:** when the bolívar devalues, how many days does it take for the shock to reach shelf prices, and which categories adjust first?

Official statistics in Venezuela are sparse and often delayed. This project builds an independent, open dataset from public sources and uses it to measure exchange-rate pass-through in near real time.

## Data (collected automatically, append-only)
| Table | Source | Frequency |
|---|---|---|
| `data/raw/fx_rates.csv` | Official rate from bcv.org.ve (primary) and ve.dolarapi.com (backup, also gives the parallel rate); Binance P2P USDT/VES (median of top 10 ads, with min/max and ad count) | every 4 h |
| `data/raw/prices.csv` | Fixed basket of 80 public product pages in 4 stores: two supermarkets (Sigo and Rio Supermarket, priced in USD/REF) and two pharmacy chains that also sell groceries (Farmatodo and Locatel, priced in Bs) across food, drinks, cleaning, personal care, baby and medicines | daily |

`fx_rates.csv` has two time columns: `ts_utc` (when we collected the row) and `value_date` (the date the source says the rate applies to). The BCV publishes its rate ahead of time ("Fecha Valor", usually the next business day), so the official series is keyed by `value_date`. Rows collected before `value_date` was added have it empty. For `binance_p2p` rows, `buy`/`sell` hold the min/max advertised price among the top ads.

The BCV server sends an incomplete TLS certificate chain. Instead of disabling verification, the collector adds the missing public intermediate (`certs/sectigo-public-server-auth-ca-dv-r36.pem`, from Sectigo's AIA URL) to certifi's roots.

`prices.csv` stores each price in the currency the store shows (`currency`); conversion happens in `sql/gap_and_index.sql` with the official rate in force that day. Every configured item yields one row per run, with a `status` (`ok`, `out_of_stock`, `no_price`, `zero_price`, `http_<code>`, `fetch_error`, `robots_disallowed`), so gaps are recorded, not silently dropped. `in_stock` is empty when the store gives no reliable stock signal (Sigo). The same `sku` in two stores means the same product and size.

Collection runs on GitHub Actions (`.github/workflows/collect.yml`) and commits new rows to the repo, so the history is public and auditable.

## Data quality
`python -m observatory.run quality` checks the raw CSVs and writes `data/quality/report.md` (also run by the workflow after each collection). It never modifies or drops data; it only reports:

| level | checks |
|---|---|
| error | exact duplicate rows; `status = ok` with a price or rate <= 0 |
| warn | day-over-day price jump beyond x1.5 or /1.5; USD-equivalent price outside 0.05-500; product without a valid price on its store's last 3 collection days; page answering 404/410; store-day coverage below 80 %; conflicting valid prices for one item on one day; FX series jump > 20 %; BCV vs dolarapi differ > 1 % on the same value date; P2P median from fewer than 5 ads; FX older than 12 h |
| info | zero-price rows per store; rows of items no longer in the basket; configured items never collected |

Thresholds are constants at the top of `src/observatory/quality.py`. Flagged observations are reviewed before analysis, not deleted.

## Method (in progress)
1. **FX gap:** `gap = P2P / official - 1`, where official is the rate in force that Caracas day (latest value date on or before it), plus daily log-returns of the P2P rate (`sql/gap_and_index.sql`).
2. **Basket index:** Jevons (geometric mean of price relatives) in Bs and in USD terms; out-of-stock items excluded.
3. **Pass-through:** distributed-lag regressions of category price changes on FX changes; lag in days per category.
4. **Dashboard:** gap, index and lag heatmap by category.

## Run locally
```bash
pip install -r requirements.txt
PYTHONPATH=src python -m observatory.run fx
PYTHONPATH=src python -m observatory.run prices   # after editing config/basket.yml
PYTHONPATH=src python -m observatory.run quality   # writes data/quality/report.md
pytest
```

## Stores and data use
Stores were chosen because their product pages are public, rendered on the server, allowed by robots.txt and not behind a bot challenge (details and rejected stores in `config/basket.yml`). The collector re-checks robots.txt on every run and honours `Crawl-delay`.

None of the stores' terms forbids automated reading. Sigo, Farmatodo and Locatel restrict reproducing or publicly displaying their site "content" (Sigo: *Propiedad intelectual*; Farmatodo: section on use of content; Locatel: *El Material*); Rio Supermarket's terms say nothing about either. Forum Supermayorista sells online only through the Zupper app, which was unreachable (Cloudflare origin errors) when checked; it is not included yet.

**Publication decision (2026-11-08):** the raw product-level prices in `data/raw/prices.csv` are published, so the dataset stays fully auditable. What is published is limited to factual observations (product name, size, price, currency, availability, timestamp and the public URL); no images, descriptions, logos or other site content are stored or redistributed. The data is for non-commercial research.

**Removal requests:** if a store asks for its data to be removed, its entries are taken out of `config/basket.yml` and its rows removed from the public dataset, and the change is noted here. Contact: open an issue in this repository.

## Limitations
- Rio's recorded price is the regular (non-promotion) price from its structured data; the other stores record the price they show, which may include promotions.
- Rio's server-rendered catalogue is its default store; prices may differ by branch.
- The basket covers a small set of products and stores; it is not a representative CPI.
- Public price pages may change layout; the collector logs failures and leaves gaps instead of imputing.
- P2P prices reflect advertised offers, not executed trades.
- Scraping is limited to public product pages, with an identifiable User-Agent and a delay between requests. Check each store's terms before enabling it.

## Roadmap
- [x] Choose stores and fill `config/basket.yml`
- [ ] 60+ days of history
- [x] Data-quality checks (`observatory.quality`, DuckDB)
- [ ] Pass-through analysis and public dashboard
