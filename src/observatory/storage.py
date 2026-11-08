"""Append-only storage. CSV is the source of truth (git-friendly, diffable);
DuckDB reads it directly for analysis, so no binary DB needs to be committed.

Schema evolution: columns may only be ADDED at the end. When the file on disk
has an older header that is a prefix of the current one, it is rewritten once
with the new header; old rows keep their values and the new columns stay
empty (we never back-fill or impute). Any other mismatch raises, so a bad
schema change can't silently shift values into the wrong columns.
"""
from __future__ import annotations

import csv
from pathlib import Path

FX_COLUMNS = ["ts_utc", "source", "rate_type", "buy", "sell", "rate", "n_ads", "value_date"]
PRICE_COLUMNS = [
    "ts_utc", "store", "sku", "category", "name", "price", "currency",
    "in_stock", "status", "method", "http_status", "url",
]


def _migrate_header(path: Path, columns: list[str]) -> None:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        body = list(reader)
    if header == columns:
        return
    if columns[: len(header)] != header:
        raise ValueError(f"{path}: header {header} is not a prefix of {columns}")
    pad = [""] * (len(columns) - len(header))
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(columns)
        w.writerows(row + pad for row in body)
    tmp.replace(path)


def append_rows(path: Path, columns: list[str], rows: list[dict]) -> int:
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    if not new_file:
        _migrate_header(path, columns)
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        if new_file:
            w.writeheader()
        w.writerows(rows)
    return len(rows)
