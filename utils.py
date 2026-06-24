"""Shared utility helpers for crawling, parsing, and rendering."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str, fallback: str = "item") -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"&", " and ", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    if value:
        return value[:160]
    digest = hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:10]
    return f"{fallback}-{digest}"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url: str, base_url: str) -> str:
    absolute = urljoin(base_url, url or "")
    parsed = urlparse(absolute)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    kept_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lower_key = key.lower()
        if lower_key in TRACKING_QUERY_KEYS:
            continue
        if any(lower_key.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        kept_query.append((key, value))

    return urlunparse((scheme, netloc, path, "", urlencode(kept_query), ""))


def same_site(url: str, base_url: str) -> bool:
    return urlparse(normalize_url(url, base_url)).netloc == urlparse(base_url).netloc


def canonical_path(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path or "home"


def slug_from_url(url: str, fallback: str = "item") -> str:
    path = canonical_path(url)
    if not path or path == "home":
        return fallback
    return slugify(path.split("/")[-1], fallback=fallback)


def parse_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = clean_text(str(value))
    if not text:
        return None
    match = re.search(r"(\d+(?:[\s,]\d{3})*(?:\.\d{1,2})?|\d+)", text)
    if not match:
        return None
    normalized = match.group(1).replace(",", "").replace(" ", "")
    try:
        return float(Decimal(normalized))
    except (InvalidOperation, ValueError):
        return None


def detect_currency(value: str | None, default: str = "USD") -> str:
    text = value or ""
    if "$" in text:
        return "USD"
    if "\u20ac" in text:
        return "EUR"
    if "\u00a3" in text:
        return "GBP"
    return default


def soup_text(element: Any) -> str:
    if element is None:
        return ""
    return clean_text(element.get_text(" ", strip=True))


def absolute_image_url(src: str | None, base_url: str) -> str:
    if not src:
        return ""
    src = src.strip()
    if src.startswith("//"):
        return f"https:{src}"
    return urljoin(base_url, src)


def extract_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text("", strip=True)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            items.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            graph = parsed.get("@graph")
            if isinstance(graph, list):
                items.extend(item for item in graph if isinstance(item, dict))
            items.append(parsed)
    return items


def json_dumps(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def json_loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return {} if default is None else default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {} if default is None else default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
