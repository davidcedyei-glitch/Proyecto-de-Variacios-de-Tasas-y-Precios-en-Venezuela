"""Store parsers tested against real product pages saved on 2026-11-07/08.

The store configs come from config/basket.yml, so a selector change there is
tested against the same HTML the scraper saw.
"""
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from observatory.prices import Robots, collect_prices, extract_css, extract_jsonld, parse_product

FIX = Path(__file__).parent / "fixtures"
STORES = {s["name"]: s for s in yaml.safe_load((ROOT / "config" / "basket.yml").read_text(encoding="utf-8"))["stores"]}


def page(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_basket_config_shape():
    assert set(STORES) == {"sigo", "farmatodo", "locatel", "rio"}
    items = [(s, i) for s in STORES.values() for i in s["items"]]
    assert 40 <= len(items) <= 80
    keys = [(s["name"], i["sku"]) for s, i in items]
    assert len(keys) == len(set(keys)), "duplicate (store, sku)"
    cats = {i["category"] for _, i in items}
    assert cats == {"alimentos", "bebidas", "limpieza", "aseo_personal", "bebe", "medicamentos"}
    for s in STORES.values():
        assert s["currency"] in {"VES", "USD"}


def test_sigo_usd_price_from_microdata():
    r = parse_product(page("sigo_arroz-premium-mary_2026-11-07.html"), STORES["sigo"])
    assert r == {"price": 1.38, "currency": "USD", "in_stock": None, "method": "css", "status": "ok"}


def test_sigo_zero_price_is_a_gap_not_a_price():
    r = parse_product(page("sigo_caraota-zero-price_2026-11-08.html"), STORES["sigo"])
    assert r["status"] == "zero_price" and r["price"] is None


def test_farmatodo_jsonld_matches_visible_price():
    html = page("farmatodo_crema-bacc_2026-11-07.html")
    r = parse_product(html, STORES["farmatodo"])
    assert r == {"price": 4494.0, "currency": "VES", "in_stock": True, "method": "jsonld", "status": "ok"}
    visible, _ = extract_css(html, STORES["farmatodo"]["price_selector"])
    assert visible == r["price"]  # visible "Bs.4.494,00"


def test_locatel_jsonld_aggregate_offer():
    r = parse_product(page("locatel_miovit_2026-11-07.html"), STORES["locatel"])
    assert r == {"price": 7416.15, "currency": "VES", "in_stock": True, "method": "jsonld", "status": "ok"}


def test_rio_regular_price_from_price_specification_in_ref():
    r = parse_product(page("rio_harina-pan-mezcla_2026-11-08.html"), STORES["rio"])
    # Page shows promo "REF 0,99 (REF 1,17)"; JSON-LD carries the regular 1.1682 in "REF" (= USD).
    assert r == {"price": 1.1682, "currency": "USD", "in_stock": True, "method": "jsonld", "status": "ok"}


def test_jsonld_out_of_stock_and_css_fallback():
    oos = ('<script type="application/ld+json">{"@type":"Product","offers":{"@type":"Offer",'
           '"price":"10.5","priceCurrency":"VES","availability":"https://schema.org/OutOfStock"}}</script>')
    assert extract_jsonld(oos) == (10.5, "VES", False)
    assert parse_product(oos, STORES["farmatodo"])["status"] == "out_of_stock"
    no_ld = '<span class="product-all-price__normal">Bs.1.234,50</span>'
    r = parse_product(no_ld, STORES["farmatodo"])
    assert (r["price"], r["method"]) == (1234.5, "css_fallback")
    assert parse_product("<html></html>", STORES["locatel"])["status"] == "no_price"


class FakeResponse:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class FakeSession:
    """Serves robots.txt and product pages from memory; records requests."""

    def __init__(self, pages):
        self.pages, self.headers, self.calls = pages, {}, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self.pages.get(url, FakeResponse(404))


def test_collect_prices_records_every_item_and_honours_robots(tmp_path):
    cfg = tmp_path / "basket.yml"
    cfg.write_text(yaml.safe_dump({"stores": [{
        "name": "sigo", "currency": "USD", "method": "css",
        "price_selector": "span[itemprop=price]", "price_attr": "content",
        "items": [
            {"sku": "a", "category": "alimentos", "name": "A", "url": "https://s.test/a"},
            {"sku": "gone", "category": "alimentos", "name": "Gone", "url": "https://s.test/gone"},
            {"sku": "priv", "category": "alimentos", "name": "P", "url": "https://s.test/private/x"},
        ],
    }]}), encoding="utf-8")
    session = FakeSession({
        "https://s.test/robots.txt": FakeResponse(200, "User-agent: *\nDisallow: /private/\nCrawl-delay: 7\n"),
        "https://s.test/a": FakeResponse(200, page("sigo_arroz-premium-mary_2026-11-07.html")),
    })
    sleeps = []
    rows = collect_prices(str(cfg), session=session, sleep=sleeps.append)
    by_sku = {r["sku"]: r for r in rows}
    assert by_sku["a"]["status"] == "ok" and by_sku["a"]["price"] == 1.38
    assert by_sku["gone"]["status"] == "http_404" and by_sku["gone"]["price"] is None
    assert by_sku["priv"]["status"] == "robots_disallowed"
    assert "https://s.test/private/x" not in session.calls
    assert sleeps == [7.0, 7.0]  # Crawl-delay wins over the 3 s default
    assert session.headers["User-Agent"].startswith("venezuela-price-fx-observatory")


def test_robots_unreachable_skips_host():
    class Down(FakeSession):
        def get(self, url, timeout=None):
            import requests
            raise requests.ConnectionError("down")
    assert Robots(Down({})).allowed("https://x.test/p") is False


def test_transient_network_error_is_retried_once(tmp_path):
    import requests
    cfg = tmp_path / "basket.yml"
    cfg.write_text(yaml.safe_dump({"stores": [{
        "name": "rio", "currency": "USD", "method": "jsonld",
        "items": [{"sku": "h", "category": "alimentos", "name": "H", "url": "https://r.test/p/h"},
                  {"sku": "d", "category": "alimentos", "name": "D", "url": "https://r.test/p/d"}],
    }]}), encoding="utf-8")

    class Flaky(FakeSession):
        def get(self, url, timeout=None):
            self.calls.append(url)
            if url.endswith("/h") and self.calls.count(url) == 1:
                raise requests.ConnectionError("reset by peer")
            if url.endswith("/d"):
                raise requests.ConnectionError("still down")
            return self.pages.get(url, FakeResponse(404))

    session = Flaky({
        "https://r.test/robots.txt": FakeResponse(404),
        "https://r.test/p/h": FakeResponse(200, page("rio_harina-pan-mezcla_2026-11-08.html")),
    })
    rows = {r["sku"]: r for r in collect_prices(str(cfg), session=session, sleep=lambda s: None)}
    assert rows["h"]["status"] == "ok" and session.calls.count("https://r.test/p/h") == 2
    assert rows["d"]["status"] == "fetch_error" and session.calls.count("https://r.test/p/d") == 2
