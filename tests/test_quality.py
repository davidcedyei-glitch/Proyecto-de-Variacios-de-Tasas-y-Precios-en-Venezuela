"""Each data-quality check, triggered on purpose with tiny synthetic CSVs."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observatory.quality import run_checks, to_markdown

FX = """ts_utc,source,rate_type,buy,sell,rate,n_ads,value_date
2026-11-05T15:00:00+00:00,bcv,oficial,,,100.0,,2026-11-05T00:00:00-04:00
2026-11-05T15:00:00+00:00,dolarapi,oficial,,,103.0,,2026-11-05T00:00:00-04:00
2026-11-05T15:00:00+00:00,binance_p2p,usdt_sell,,,120.0,10,
2026-11-05T15:00:00+00:00,binance_p2p,usdt_sell,,,120.0,10,
2026-11-06T15:00:00+00:00,binance_p2p,usdt_sell,,,150.0,3,
2026-11-07T15:00:00+00:00,binance_p2p,usdt_buy,,,0,10,
"""

HEADER = "ts_utc,store,sku,category,name,price,currency,in_stock,status,method,http_status,url\n"


def prow(day, sku, price, status="ok", store="s", currency="USD", hour=15):
    p = "" if price is None else price
    return f"2026-11-{day:02d}T{hour:02d}:00:00+00:00,{store},{sku},alimentos,{sku},{p},{currency},,{status},css,200,u\n"


def build(tmp_path, price_rows, config_skus, fx=FX):
    d = tmp_path / "raw"
    d.mkdir()
    (d / "fx_rates.csv").write_text(fx, encoding="utf-8")
    (d / "prices.csv").write_text(HEADER + "".join(price_rows), encoding="utf-8")
    cfg = tmp_path / "basket.yml"
    cfg.write_text(yaml.safe_dump({"stores": [{"name": "s", "currency": "USD", "items": [
        {"sku": k, "category": "alimentos", "name": k, "url": "u"} for k in config_skus]}]}), encoding="utf-8")
    now = datetime(2026, 11, 8, 15, tzinfo=timezone.utc)
    return run_checks(d, cfg, now=now)


def by_check(findings):
    out = {}
    for f in findings:
        out.setdefault(f.check, []).append(f)
    return out


def test_price_checks(tmp_path):
    rows = [
        # "stable": fine every day -> no findings
        *[prow(d, "stable", 1.0) for d in (5, 6, 7, 8)],
        # "jumpy": x3 overnight
        prow(5, "jumpy", 1.0), prow(6, "jumpy", 3.0), prow(7, "jumpy", 3.0), prow(8, "jumpy", 3.0),
        # "cheap": implausible USD 0.01
        *[prow(d, "cheap", 0.01) for d in (5, 6, 7, 8)],
        # "gone": valid once, then nothing valid for the last 3 days, last answer 404
        prow(5, "gone", 2.0), prow(6, "gone", None, "no_price"), prow(7, "gone", None, "zero_price"),
        prow(8, "gone", None, "http_404"),
        # exact duplicate row + same-day conflict
        prow(5, "dup", 1.0), prow(5, "dup", 1.0), prow(5, "dup", 1.2, hour=18),
        *[prow(d, "dup", 1.0) for d in (6, 7, 8)],
        # a status=ok row with price 0 can only come from a collector bug
        prow(8, "bug", 0),
        # item no longer configured
        prow(5, "retired", 1.0),
    ]
    f = by_check(build(tmp_path, rows, ["stable", "jumpy", "cheap", "gone", "dup", "bug", "new"]))
    assert [x.subject for x in f["price_jump"]] == ["s/jumpy"]
    assert "x3.00" in f["price_jump"][0].detail
    assert {x.subject for x in f["implausible_usd_price"]} == {"s/cheap"}
    assert {x.subject for x in f["product_missing"]} == {"s/gone", "s/bug", "s/new"}
    assert [x.subject for x in f["product_removed"]] == ["s/gone"]
    assert [x.subject for x in f["duplicate_row"]] == ["s/dup"] and f["duplicate_row"][0].level == "error"
    assert [x.subject for x in f["same_day_conflict"]] == ["s/dup"]
    assert [x.subject for x in f["non_positive_price"]] == ["s/bug"]
    assert [x.subject for x in f["never_collected"]] == ["s/new"]
    assert f["not_in_basket"][0].subject == "s" and "1 items" in f["not_in_basket"][0].detail
    assert "zero_price_rows" in f
    # 7 configured items; on day 6 only stable, jumpy, cheap, dup are valid = 4/7 < 80 %
    assert any("2026-11-06" in x.detail for x in f["low_coverage"])
    assert "stable" not in " ".join(x.subject for x in sum(f.values(), []))


def test_fx_checks(tmp_path):
    f = by_check(build(tmp_path, [prow(8, "x", 1.0)], ["x"]))
    assert [x.subject for x in f["fx_duplicate_row"]] == ["binance_p2p/usdt_sell"]
    assert [x.subject for x in f["fx_non_positive_rate"]] == ["binance_p2p/usdt_buy"]
    assert [x.subject for x in f["fx_jump"]] == ["binance_p2p/usdt_sell"]  # 120 -> 150 = +25 %
    assert "2026-11-05" in f["official_sources_disagree"][0].detail      # 100 vs 103 = 3 %
    assert [x.detail.split(": ")[1] for x in f["p2p_few_ads"]] == ["only 3 ads in the median"]
    assert {x.subject for x in f["fx_stale"]} >= {"bcv/oficial", "dolarapi/oficial"}


def test_clean_data_has_no_warnings_and_report_renders(tmp_path):
    fx = """ts_utc,source,rate_type,buy,sell,rate,n_ads,value_date
2026-11-08T12:00:00+00:00,bcv,oficial,,,100.0,,2026-11-08T00:00:00-04:00
2026-11-08T12:00:00+00:00,binance_p2p,usdt_sell,,,110.0,10,
"""
    findings = build(tmp_path, [prow(8, "a", 1.0), prow(8, "b", 2.0)], ["a", "b"], fx=fx)
    assert [f for f in findings if f.level != "info"] == []
    md = to_markdown(findings)
    assert md.startswith("# Data-quality report") and "0 errors, 0 warnings" in md


def test_missing_files_are_tolerated(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    assert run_checks(d, tmp_path / "nope.yml") == []
