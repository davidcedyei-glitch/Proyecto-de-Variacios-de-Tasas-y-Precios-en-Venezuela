"""FX parsers tested against real responses saved on 2026-11-07."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observatory.fx import BCV_INTERMEDIATE, parse_bcv, parse_dolarapi
from observatory.storage import FX_COLUMNS, append_rows

FIX = Path(__file__).parent / "fixtures"


def test_parse_bcv_real_page():
    rows = parse_bcv((FIX / "bcv_home_2026-11-07.html").read_text(encoding="utf-8"))
    assert len(rows) == 1
    r = rows[0]
    assert (r["source"], r["rate_type"]) == ("bcv", "oficial")
    assert r["rate"] == pytest.approx(897.5383)  # shown as "897,53830000"
    assert r["value_date"] == "2026-11-09T00:00:00-04:00"


def test_parse_bcv_layout_change_returns_empty():
    assert parse_bcv("<html><body>mantenimiento</body></html>") == []


def test_parse_dolarapi_real_payload_keeps_value_date():
    rows = parse_dolarapi(json.loads((FIX / "dolarapi_dolares_2026-11-07.json").read_text(encoding="utf-8")))
    by_type = {r["rate_type"]: r for r in rows}
    assert set(by_type) == {"oficial", "paralelo"}
    assert by_type["oficial"]["value_date"].startswith("2026-11-0")
    assert by_type["oficial"]["rate"] > 0


def test_bcv_intermediate_cert_is_shipped():
    assert "BEGIN CERTIFICATE" in BCV_INTERMEDIATE.read_text(encoding="utf-8")


def test_append_rows_migrates_old_header(tmp_path):
    f = tmp_path / "fx.csv"
    old = FX_COLUMNS[:-1]
    f.write_text(",".join(old) + "\nt,dolarapi,oficial,,,896.1,\n", encoding="utf-8")
    row = dict(zip(FX_COLUMNS, ["t2", "bcv", "oficial", "", "", 897.5, "", "2026-11-09"]))
    append_rows(f, FX_COLUMNS, [row])
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == ",".join(FX_COLUMNS)
    assert lines[1] == "t,dolarapi,oficial,,,896.1,,"  # old row kept, new column empty
    assert lines[2].endswith(",2026-11-09")


def test_append_rows_rejects_incompatible_header(tmp_path):
    f = tmp_path / "fx.csv"
    f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        append_rows(f, FX_COLUMNS, [{"ts_utc": "t"}])
