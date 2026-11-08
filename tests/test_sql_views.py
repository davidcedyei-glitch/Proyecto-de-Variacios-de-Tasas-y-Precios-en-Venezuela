"""The FX views in sql/gap_and_index.sql, run on a tiny synthetic CSV."""
from pathlib import Path

import duckdb
import pytest

SQL = Path(__file__).resolve().parents[1] / "sql" / "gap_and_index.sql"

CSV = """ts_utc,source,rate_type,buy,sell,rate,n_ads,value_date
2026-11-07T01:00:00+00:00,dolarapi,oficial,,,999.0,,
2026-11-07T15:00:00+00:00,bcv,oficial,,,100.0,,2026-11-06T00:00:00-04:00
2026-11-07T15:00:00+00:00,dolarapi,oficial,,,101.0,,2026-11-06T00:00:00-04:00
2026-11-08T02:00:00+00:00,binance_p2p,usdt_sell,,,120.0,10,
2026-11-08T05:00:00+00:00,binance_p2p,usdt_sell,,,130.0,10,
2026-11-08T15:00:00+00:00,bcv,oficial,,,110.0,,2026-11-09T00:00:00-04:00
"""


def test_fx_gap_uses_caracas_day_and_rate_in_force(tmp_path, monkeypatch):
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "raw" / "fx_rates.csv").write_text(CSV, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    con = duckdb.connect()
    for stmt in SQL.read_text(encoding="utf-8").split(";"):
        if stmt.strip() and "basket" not in stmt and "prices" not in stmt:
            con.execute(stmt)
    rows = con.execute("SELECT day::VARCHAR, official, official_value_day::VARCHAR, p2p_usdt FROM fx_gap ORDER BY day").fetchall()
    # 02:00Z on the 8th is 22:00 on the 7th in Caracas; 05:00Z is 01:00 on the 8th.
    # Both days use the Friday (value date 11-06) BCV rate, not dolarapi or the
    # undated row, and not the rate published early for Monday 11-09.
    assert rows == [("2026-11-07", 100.0, "2026-11-06", 120.0), ("2026-11-08", 100.0, "2026-11-06", 130.0)]
    gap = con.execute("SELECT gap_pct FROM fx_gap WHERE day = DATE '2026-11-07'").fetchone()[0]
    assert gap == pytest.approx(20.0)


PRICES = """ts_utc,store,sku,category,name,price,currency,in_stock,status,method,http_status,url
2026-11-07T15:00:00+00:00,sigo,x,alimentos,X,1.0,USD,,ok,css,200,u
2026-11-07T15:00:00+00:00,farmatodo,y,aseo_personal,Y,200.0,VES,True,ok,jsonld,200,u
2026-11-07T15:00:00+00:00,farmatodo,z,aseo_personal,Z,,VES,,http_404,,404,u
2026-11-10T15:00:00+00:00,sigo,x,alimentos,X,1.0,USD,,ok,css,200,u
2026-11-10T15:00:00+00:00,farmatodo,y,aseo_personal,Y,220.0,VES,True,ok,jsonld,200,u
2026-11-10T15:00:00+00:00,farmatodo,z,aseo_personal,Z,,VES,,zero_price,css,200,u
"""


def test_basket_index_converts_currencies_and_skips_gaps(tmp_path, monkeypatch):
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "fx_rates.csv").write_text(CSV, encoding="utf-8")
    (raw / "prices.csv").write_text(PRICES, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    con = duckdb.connect()
    for stmt in SQL.read_text(encoding="utf-8").split(";"):
        if stmt.strip():
            con.execute(stmt)
    # 11-07 uses the 11-06 rate (100), 11-10 uses the 11-09 rate (110).
    x = con.execute("SELECT day::VARCHAR, price_ves, price_usd FROM basket_daily WHERE sku='x' ORDER BY day").fetchall()
    assert x == [("2026-11-07", 100.0, 1.0), ("2026-11-10", 110.0, 1.0)]
    idx = con.execute("SELECT day::VARCHAR, index_ves, index_usd, n_items FROM basket_index ORDER BY day").fetchall()
    assert idx[0] == ("2026-11-07", pytest.approx(100.0), pytest.approx(100.0), 2)
    # Bs: x +10 %, y +10 % -> 110. USD: x 0 %, y (220/110)/(200/100) = 0 % -> 100.
    assert idx[1][0] == "2026-11-10" and idx[1][3] == 2
    assert idx[1][1] == pytest.approx(110.0) and idx[1][2] == pytest.approx(100.0)
