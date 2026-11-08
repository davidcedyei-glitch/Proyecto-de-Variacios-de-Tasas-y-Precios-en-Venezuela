# Data-quality report

Generated 2026-11-08T14:41:09+00:00 by `python -m observatory.run quality`.
**0 errors, 5 warnings, 3 notes.** Nothing is modified or dropped; flagged rows stay in the raw data.

| level | check | subject | detail |
|---|---|---|---|
| warn | fx_stale | bcv/oficial | last row 2026-11-08T01:51:08+00:00 (13 h ago) |
| warn | fx_stale | binance_p2p/usdt_buy | last row 2026-11-08T01:51:09+00:00 (13 h ago) |
| warn | fx_stale | binance_p2p/usdt_sell | last row 2026-11-08T01:51:10+00:00 (13 h ago) |
| warn | fx_stale | dolarapi/oficial | last row 2026-11-08T01:51:09+00:00 (13 h ago) |
| warn | fx_stale | dolarapi/paralelo | last row 2026-11-08T01:51:09+00:00 (13 h ago) |
| info | not_in_basket | farmatodo | 4 items with rows (2026-11-08 to 2026-11-08) are no longer in config/basket.yml; their rows are kept and still enter the index on the days they have |
| info | not_in_basket | locatel | 4 items with rows (2026-11-08 to 2026-11-08) are no longer in config/basket.yml; their rows are kept and still enter the index on the days they have |
| info | not_in_basket | sigo | 3 items with rows (2026-11-08 to 2026-11-08) are no longer in config/basket.yml; their rows are kept and still enter the index on the days they have |
