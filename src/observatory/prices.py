"""Basket price collector, driven by config/basket.yml.

Each store declares how to read a product page:
- method "jsonld": read the schema.org Product/Offer block that the page embeds
  for search engines (price, priceCurrency, availability). This is the most
  stable option because it does not depend on visual CSS classes.
- method "css": read the price from `price_selector` (optionally from an
  attribute such as `content`), and stock from `out_of_stock_selector`
  (present only when sold out) or `in_stock_selector` (present only when
  the product can be bought).
For jsonld stores, `price_selector` is a fallback used only when the JSON-LD
block is missing; the row's `method` column says which one produced the value.

Prices are stored in the currency the store shows (`currency` = VES or USD).
Conversion to the other currency happens at analysis time with the official
rate in force that day, never at collection time.

Every configured item produces one row per run, even when it fails, so gaps
are documented rather than silent. `status` is one of:
  ok, out_of_stock, no_price, zero_price, http_<code>, fetch_error, robots_disallowed
`price` is empty unless the page showed a positive price.

Politeness: identifiable User-Agent, robots.txt checked on every run (Disallow
and Crawl-delay honoured), at least REQUEST_DELAY seconds between requests to
the same store, one pass per day, public product pages only.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urlsplit

import requests
import yaml
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = "venezuela-price-fx-observatory/0.1 (research project)"
HEADERS = {"User-Agent": USER_AGENT}
REQUEST_DELAY = 3.0
RETRY_DELAY = 30.0
TIMEOUT = 25

# "REF" (referencia) is how Venezuelan stores label prices in US dollars that
# are paid in Bs at the BCV rate; "Bs" labels are current bolivars (VES).
# VEF (the pre-2018 bolivar) is deliberately NOT mapped: different scale.
CURRENCY_ALIASES = {"REF": "USD", "Ref": "USD", "BS": "VES", "Bs": "VES"}


def parse_price(text: str) -> float | None:
    """Parse '1.234,56', '1,234.56', 'Bs. 85,5' or '$ 3.20' into a float."""
    m = re.search(r"\d[\d.,]*", text or "")
    if not m:
        return None
    s = m.group(0).rstrip(".,")
    if "," in s and "." in s:
        # whichever separator comes last is the decimal one
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = head.replace(",", "") + "." + tail if len(tail) <= 2 else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.split(".")[1]) == 3):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _jsonld_products(soup: BeautifulSoup) -> list[dict]:
    found = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("@type") == "Product":
                    found.append(node)
                stack.extend(node.get("@graph", []))
    return found


def extract_jsonld(html: str) -> tuple[float | None, str | None, bool | None]:
    """(price, currency, in_stock) from the first schema.org Product offer.

    AggregateOffer: uses `price` when present, else `lowPrice`. in_stock is
    None when the page does not state availability.
    """
    soup = BeautifulSoup(html, "html.parser")
    for product in _jsonld_products(soup):
        offer = product.get("offers")
        if isinstance(offer, list):
            offer = offer[0] if offer else None
        if not isinstance(offer, dict):
            continue
        inner = offer.get("offers")
        if isinstance(inner, list) and inner and "price" not in offer:
            offer = {**offer, **inner[0]}
        spec = offer.get("priceSpecification")
        if isinstance(spec, list):
            spec = spec[0] if spec else None
        if isinstance(spec, dict) and "price" not in offer:
            # Instaleap stores (Rio): price lives in priceSpecification
            offer = {**offer, "price": spec.get("price"), "priceCurrency": spec.get("priceCurrency")}
        raw = offer.get("price", offer.get("lowPrice"))
        try:
            price = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            price = parse_price(str(raw))
        avail = str(offer.get("availability", ""))
        in_stock = None if not avail else avail.rstrip("/").endswith(("InStock", "LimitedAvailability"))
        currency = offer.get("priceCurrency")
        return price, CURRENCY_ALIASES.get(currency, currency), in_stock
    return None, None, None


def extract_css(
    html: str,
    price_selector: str,
    out_of_stock_selector: str | None = None,
    price_attr: str | None = None,
    in_stock_selector: str | None = None,
) -> tuple[float | None, bool]:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one(price_selector)
    price = None
    if node is not None:
        raw = node.get(price_attr) if price_attr else node.get_text(" ", strip=True)
        price = parse_price(raw) if raw is not None else None
    # None = the page gives no reliable stock signal (unknown, not "in stock").
    in_stock = None
    if out_of_stock_selector or in_stock_selector:
        in_stock = True
        if out_of_stock_selector and soup.select_one(out_of_stock_selector) is not None:
            in_stock = False
        if in_stock_selector and soup.select_one(in_stock_selector) is None:
            in_stock = False
    return price, in_stock


# Backwards-compatible name used by older tests.
def extract_price(html: str, price_selector: str, stock_selector: str | None = None):
    return extract_css(html, price_selector, stock_selector)


def parse_product(html: str, store: dict) -> dict:
    """Turn a product page into {price, currency, in_stock, method, status}."""
    currency = store["currency"]
    method = store.get("method", "css")
    price = in_stock = None
    if method == "jsonld":
        price, ld_currency, in_stock = extract_jsonld(html)
        if ld_currency and ld_currency != currency:
            log.warning("%s: page says %s, config says %s", store["name"], ld_currency, currency)
            currency = ld_currency
        if price is None and store.get("price_selector"):
            method = "css_fallback"
    if method != "jsonld":
        css_price, css_stock = extract_css(
            html,
            store["price_selector"],
            store.get("out_of_stock_selector"),
            store.get("price_attr"),
            store.get("in_stock_selector"),
        )
        price = css_price
        in_stock = css_stock if in_stock is None else in_stock
    if in_stock is None and store.get("out_of_stock_selector"):
        in_stock = BeautifulSoup(html, "html.parser").select_one(store["out_of_stock_selector"]) is None

    if price is None:
        status = "no_price"
    elif price <= 0:
        status, price = "zero_price", None
    elif in_stock is False:
        status = "out_of_stock"
    else:
        status = "ok"
    return {"price": price, "currency": currency, "in_stock": in_stock, "method": method, "status": status}


class Robots:
    """robots.txt per host, fetched once per run with our own User-Agent."""

    def __init__(self, session: requests.Session):
        self.session = session
        self.cache: dict[str, robotparser.RobotFileParser | None] = {}

    def _get(self, url: str) -> robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self.cache:
            rp = robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=TIMEOUT)
                if r.status_code >= 400:
                    rp.parse([])  # no robots.txt: everything allowed
                else:
                    rp.parse(r.text.splitlines())
                self.cache[base] = rp
            except requests.RequestException as exc:
                log.warning("robots.txt unavailable for %s: %s", base, exc)
                self.cache[base] = None  # be conservative: skip the host
        return self.cache[base]

    def allowed(self, url: str) -> bool:
        rp = self._get(url)
        return bool(rp and rp.can_fetch(USER_AGENT, url))

    def delay(self, url: str) -> float:
        rp = self._get(url)
        crawl = rp.crawl_delay(USER_AGENT) if rp else None
        return max(REQUEST_DELAY, float(crawl or 0))


def collect_prices(config_path: str, session: requests.Session | None = None, sleep=time.sleep) -> list[dict]:
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    session = session or requests.Session()
    session.headers.update(HEADERS)
    robots = Robots(session)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict] = []
    for store in cfg.get("stores", []):
        for item in store.get("items", []):
            url = item["url"]
            row = {
                "ts_utc": ts,
                "store": store["name"],
                "sku": item["sku"],
                "category": item["category"],
                "name": item["name"],
                "price": None,
                "currency": store["currency"],
                "in_stock": None,
                "method": None,
                "status": None,
                "http_status": None,
                "url": url,
            }
            if not robots.allowed(url):
                row["status"] = "robots_disallowed"
                rows.append(row)
                log.warning("robots.txt disallows %s", url)
                continue
            try:
                try:
                    r = session.get(url, timeout=TIMEOUT)
                except (requests.ConnectionError, requests.Timeout) as exc:
                    # One retry for transient network failures (resets, DNS
                    # blips). HTTP errors are answers, not blips: no retry.
                    log.info("retrying %s after %s", item["sku"], type(exc).__name__)
                    sleep(RETRY_DELAY)
                    r = session.get(url, timeout=TIMEOUT)
                row["http_status"] = r.status_code
                if r.status_code != 200:
                    row["status"] = f"http_{r.status_code}"
                else:
                    row.update(parse_product(r.text, store))
            except requests.RequestException as exc:
                row["status"] = "fetch_error"
                log.warning("price fetch failed for %s: %s", item["sku"], exc)
            if row["status"] != "ok":
                log.info("%s %s -> %s", store["name"], item["sku"], row["status"])
            rows.append(row)
            sleep(robots.delay(url))
    return rows
