# Data-quality report

Generated 2026-11-19T23:01:27+00:00 by `python -m observatory.run quality`.
**0 errors, 2 warnings, 3 notes.** Nothing is modified or dropped; flagged rows stay in the raw data.

| level | check | subject | detail |
|---|---|---|---|
| warn | fx_stale | bcv/oficial | last row 2026-11-18T19:27:04+00:00 (28 h ago) |
| warn | product_missing | locatel/ibuprofeno-calox-400-x10 | no valid price on the last 3 collection days (last valid: never, last status: out_of_stock) |
| info | not_in_basket | farmatodo | 4 items with rows (2026-11-08 to 2026-11-08) are no longer in config/basket.yml; their rows are kept and still enter the index on the days they have |
| info | not_in_basket | locatel | 4 items with rows (2026-11-08 to 2026-11-08) are no longer in config/basket.yml; their rows are kept and still enter the index on the days they have |
| info | not_in_basket | sigo | 3 items with rows (2026-11-08 to 2026-11-08) are no longer in config/basket.yml; their rows are kept and still enter the index on the days they have |
