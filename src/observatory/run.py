"""CLI entry point.

    python -m observatory.run fx
    python -m observatory.run prices
    python -m observatory.run quality [--strict]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .fx import collect_fx
from .prices import collect_prices
from .storage import FX_COLUMNS, PRICE_COLUMNS, append_rows

DATA = Path("data/raw")
QUALITY_REPORT = Path("data/quality/report.md")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("job", choices=["fx", "prices", "quality"])
    p.add_argument("--config", default="config/basket.yml")
    p.add_argument("--strict", action="store_true", help="quality: exit 1 if any error-level finding")
    args = p.parse_args()

    if args.job == "quality":
        from .quality import run_checks, to_markdown

        findings = run_checks(DATA, Path(args.config))
        QUALITY_REPORT.parent.mkdir(parents=True, exist_ok=True)
        QUALITY_REPORT.write_text(to_markdown(findings), encoding="utf-8")
        for f in findings:
            if f.level != "info":
                logging.warning("%s %s %s: %s", f.level, f.check, f.subject, f.detail)
        n_err = sum(f.level == "error" for f in findings)
        logging.info("quality: %d findings (%d errors) -> %s", len(findings), n_err, QUALITY_REPORT)
        if args.strict and n_err:
            sys.exit(1)
        return

    if args.job == "fx":
        n = append_rows(DATA / "fx_rates.csv", FX_COLUMNS, collect_fx())
    else:
        n = append_rows(DATA / "prices.csv", PRICE_COLUMNS, collect_prices(args.config))
    logging.info("%s: %d rows appended", args.job, n)


if __name__ == "__main__":
    main()
