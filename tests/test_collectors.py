import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observatory.fx import parse_binance_ads, parse_dolarapi
from observatory.prices import extract_price, parse_price
from observatory.storage import FX_COLUMNS, append_rows


def test_parse_price_formats():
    assert parse_price("Bs. 1.234,56") == 1234.56
    assert parse_price("$ 3.20") == 3.2
    assert parse_price("1,234.56") == 1234.56
    assert parse_price("85,5") == 85.5
    assert parse_price("2.500") == 2500.0
    assert parse_price("sin precio") is None


def test_extract_price_and_stock():
    html = '<div><span class="price">Bs. 120,50</span></div>'
    assert extract_price(html, "span.price", ".agotado") == (120.5, True)
    html_oos = html + '<p class="agotado">Agotado</p>'
    assert extract_price(html_oos, "span.price", ".agotado") == (120.5, False)


def test_parse_dolarapi():
    rows = parse_dolarapi(
        [{"fuente": "oficial", "promedio": 50.1}, {"fuente": "paralelo", "promedio": 60.0}, {"fuente": "x"}]
    )
    assert [r["rate_type"] for r in rows] == ["oficial", "paralelo"]
    assert rows[0]["rate"] == 50.1


def test_parse_binance_median():
    payload = {"data": [{"adv": {"price": p}} for p in ("60.0", "61.0", "100.0")]}
    row = parse_binance_ads(payload, "SELL")
    assert row["rate"] == 61.0 and row["buy"] == 60.0 and row["sell"] == 100.0 and row["n_ads"] == 3
    assert parse_binance_ads({"data": []}, "SELL") is None


def test_append_rows(tmp_path):
    f = tmp_path / "fx.csv"
    r = {"ts_utc": "t", "source": "s", "rate_type": "x", "buy": 1, "sell": 2, "rate": 1.5, "n_ads": 3}
    append_rows(f, FX_COLUMNS, [r])
    append_rows(f, FX_COLUMNS, [r])
    assert len(f.read_text().strip().splitlines()) == 3  # header + 2 rows


def test_append_rows_writes_lf_only(tmp_path):
    f = tmp_path / "fx.csv"
    append_rows(f, FX_COLUMNS, [{"ts_utc": "t", "source": "s", "rate_type": "x", "rate": 1.0}])
    assert b"\r\n" not in f.read_bytes()
