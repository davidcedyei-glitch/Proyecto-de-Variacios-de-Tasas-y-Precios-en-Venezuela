"""FX collectors: official BCV rate, parallel rate and Binance P2P USDT/VES.

Each collector returns a list of flat dict rows so they can be appended to the
`fx_rates` table. A collector never raises: on failure it logs and returns [].

Two timestamps per row:
- `ts_utc`: when we collected it.
- `value_date`: the date the source says the rate applies to. The BCV publishes
  the official rate ahead of time ("Fecha Valor", usually the next business
  day), so for lag analysis the official series must be keyed by value_date,
  not by collection time. Empty when the source gives no date (Binance P2P).

Column semantics differ by source and are documented here rather than hidden:
- dolarapi / bcv: `buy`/`sell` are the source's compra/venta (often empty).
- binance_p2p: `buy`/`sell` hold the MIN/MAX advertised price among the top-N
  ads, and `rate` is their median.
"""
from __future__ import annotations

import logging
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import certifi
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

TIMEOUT = 20
HEADERS = {"User-Agent": "venezuela-price-fx-observatory/0.1 (research project)"}

# Public aggregator that republishes BCV + parallel rates. Replace/add sources
# in SOURCES below if it changes; keeping more than one makes the series robust.
DOLARAPI_URL = "https://ve.dolarapi.com/v1/dolares"
BINANCE_P2P_URL = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"

# Primary source for the official rate. robots.txt allows "/" (Crawl-delay 10;
# we make one request every 4 h). The server sends an incomplete TLS chain
# (it omits the "Sectigo Public Server Authentication CA DV R36" intermediate),
# so we verify against certifi + that intermediate instead of disabling TLS
# verification. The intermediate is public, from Sectigo's AIA URL, valid to 2036.
BCV_URL = "https://www.bcv.org.ve/"
BCV_INTERMEDIATE = Path(__file__).resolve().parents[2] / "certs" / "sectigo-public-server-auth-ca-dv-r36.pem"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dolarapi(payload: list[dict]) -> list[dict]:
    """Normalize the dolarapi.com response into rows.

    Expected items look like {"fuente": "oficial", "promedio": 36.5, ...}.
    """
    ts = _now()
    rows = []
    for item in payload:
        rate = item.get("promedio") or item.get("venta") or item.get("compra")
        if rate is None:
            continue
        rows.append(
            {
                "ts_utc": ts,
                "source": "dolarapi",
                "rate_type": str(item.get("fuente", "unknown")).lower(),
                "buy": item.get("compra"),
                "sell": item.get("venta"),
                "rate": float(rate),
                "n_ads": None,
                "value_date": item.get("fechaActualizacion"),
            }
        )
    return rows


def fetch_dolarapi() -> list[dict]:
    try:
        r = requests.get(DOLARAPI_URL, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return parse_dolarapi(r.json())
    except Exception as exc:  # noqa: BLE001
        log.warning("dolarapi failed: %s", exc)
        return []


def _bcv_number(text: str) -> float:
    """BCV uses '.' for thousands and ',' for decimals, with 8 decimals."""
    return float(text.strip().replace(".", "").replace(",", "."))


def parse_bcv(html: str) -> list[dict]:
    """Extract the official USD rate and its 'Fecha Valor' from bcv.org.ve."""
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("#dolar strong")
    if node is None:
        return []
    date_node = soup.select_one("span.date-display-single[content]")
    return [
        {
            "ts_utc": _now(),
            "source": "bcv",
            "rate_type": "oficial",
            "buy": None,
            "sell": None,
            "rate": _bcv_number(node.get_text()),
            "n_ads": None,
            "value_date": date_node["content"] if date_node else None,
        }
    ]


def _bcv_ca_bundle() -> str:
    """certifi roots + the intermediate the BCV server forgets to send."""
    bundle = Path(tempfile.gettempdir()) / "observatory_bcv_ca_bundle.pem"
    bundle.write_text(
        Path(certifi.where()).read_text(encoding="utf-8")
        + "\n"
        + BCV_INTERMEDIATE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return str(bundle)


def fetch_bcv() -> list[dict]:
    try:
        r = requests.get(BCV_URL, headers=HEADERS, timeout=TIMEOUT, verify=_bcv_ca_bundle())
        r.raise_for_status()
        rows = parse_bcv(r.text)
        if not rows:
            log.warning("bcv: page fetched but USD rate not found (layout change?)")
        return rows
    except Exception as exc:  # noqa: BLE001
        log.warning("bcv failed: %s", exc)
        return []


def parse_binance_ads(payload: dict, trade_type: str, top_n: int = 10) -> dict | None:
    """Summarize the first `top_n` P2P ads into a single robust price."""
    ads = (payload or {}).get("data") or []
    prices = []
    for ad in ads[:top_n]:
        try:
            prices.append(float(ad["adv"]["price"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not prices:
        return None
    return {
        "ts_utc": _now(),
        "source": "binance_p2p",
        "rate_type": f"usdt_{trade_type.lower()}",
        "buy": min(prices),
        "sell": max(prices),
        "rate": statistics.median(prices),
        "n_ads": len(prices),
        "value_date": None,
    }


def fetch_binance_p2p(trade_type: str, top_n: int = 10) -> list[dict]:
    """trade_type: 'BUY' (user buys USDT) or 'SELL' (user sells USDT)."""
    body = {
        "fiat": "VES",
        "asset": "USDT",
        "tradeType": trade_type,
        "page": 1,
        "rows": top_n,
        "payTypes": [],
        "publisherType": None,
    }
    try:
        r = requests.post(BINANCE_P2P_URL, json=body, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        row = parse_binance_ads(r.json(), trade_type, top_n)
        return [row] if row else []
    except Exception as exc:  # noqa: BLE001
        log.warning("binance p2p %s failed: %s", trade_type, exc)
        return []


def collect_fx() -> list[dict]:
    rows: list[dict] = []
    # Both official sources are collected every run (not only on failure):
    # they cross-check each other and keep the series alive if one breaks.
    rows += fetch_bcv()
    rows += fetch_dolarapi()
    rows += fetch_binance_p2p("BUY")
    rows += fetch_binance_p2p("SELL")
    return rows
