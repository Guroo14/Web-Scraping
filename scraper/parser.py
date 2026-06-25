"""HTML parsing utilities for Shopify-like storefront pages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


PRICE_RE = re.compile(r"(?:\$|USD\s*)\s?\d[\d,]*(?:\.\d{2})?")
WHITESPACE_RE = re.compile(r"\s+")
ASSET_ATTRS = ("src", "href", "poster", "data-src", "data-bg", "data-background-image")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".avif")
ASSET_EXTENSIONS = IMAGE_EXTENSIONS + (".css", ".js", ".woff", ".woff2", ".ttf", ".otf", ".eot")


@dataclass(slots=True)
class ParsedPage:
    """Structured representation of one scraped page."""

    page: dict[str, Any]
    products: list[dict[str, Any]]
    categories: list[dict[str, Any]]
    assets: list[str]
    product_urls: list[str]
    page_urls: list[str]


def clean_text(value: str | None) -> str:
    """Normalize visible text while preserving word order."""

    return WHITESPACE_RE.sub(" ", value or "").strip()


def slug_from_url(url: str) -> str:
    """Build a stable slug from a URL path."""

    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return "home"
    return path.split("/")[-1] or "home"


def path_from_url(url: str) -> str:
    """Return a normalized path for routing."""

    parsed = urlparse(url)
    path = parsed.path or "/"
    return "/" if path == "/" else path.rstrip("/")


def absolute_url(base_url: str, value: str | None) -> str | None:
    """Resolve protocol-relative and relative URLs."""

    if not value:
        return None
    value = value.strip()
    if not value or value.startswith(("data:", "mailto:", "tel:", "javascript:")):
        return None
    if value.startswith("//"):
        return "https:" + value
    return urljoin(base_url, value)


def srcset_urls(base_url: str, srcset: str | None) -> list[str]:
    """Extract URLs from an image srcset attribute."""

    if not srcset:
        return []
    urls: list[str] = []
    for part in srcset.split(","):
        candidate = part.strip().split(" ")[0]
        resolved = absolute_url(base_url, candidate)
        if resolved:
            urls.append(resolved)
    return urls


def is_asset_url(url: str) -> bool:
    """Return True for URLs that should be downloaded as local assets."""

    path = urlparse(url).path.lower()
    return path.endswith(ASSET_EXTENSIONS) or "/cdn/" in url or "shopifycdn" in url


def is_internal_url(base_url: str, url: str) -> bool:
    """Check whether a link belongs to the same host."""

    base_host = urlparse(base_url).netloc
    parsed = urlparse(url)
    return not parsed.netloc or parsed.netloc == base_host


def page_type(url: str) -> str:
    """Classify page type based on the path."""

    path = path_from_url(url)
    if path == "/":
        return "home"
    if path.startswith("/collections/"):
        return "category"
    if path.startswith("/products/"):
        return "product"
    return "page"


def extract_meta(soup: BeautifulSoup) -> dict[str, str]:
    """Extract title and meta tag content."""

    meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = tag.get("name") or tag.get("property") or tag.get("http-equiv")
        content = tag.get("content")
        if key and content:
            meta[str(key)] = str(content)
    for rel in ("canonical", "icon", "shortcut icon", "apple-touch-icon"):
        link = soup.find("link", rel=lambda value: value and rel in " ".join(value if isinstance(value, list) else [value]).lower())
        if link and link.get("href"):
            meta[f"link:{rel}"] = str(link["href"])
    return meta


def extract_visible_text(soup: BeautifulSoup) -> str:
    """Return page text with non-visible content removed."""

    clone = BeautifulSoup(str(soup), "html.parser")
    for tag in clone(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return clean_text(clone.get_text(" "))


def extract_headers(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Extract document heading hierarchy."""

    headers: list[dict[str, str]] = []
    for tag in soup.find_all(re.compile("^h[1-6]$")):
        text = clean_text(tag.get_text(" "))
        if text:
            headers.append({"level": tag.name, "text": text})
    return headers


def link_text(anchor: Tag) -> str:
    """Find a readable label for an anchor."""

    aria = anchor.get("aria-label")
    title = anchor.get("title")
    text = clean_text(anchor.get_text(" "))
    return clean_text(str(aria or title or text))


def extract_navigation(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    """Extract header, footer, and generic menu links in document order."""

    nav_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    sections: list[tuple[str, Tag]] = []

    for selector, nav_type in (("header", "header"), ("nav", "header"), ("footer", "footer")):
        for section in soup.select(selector):
            sections.append((nav_type, section))

    if not sections and soup.body:
        sections.append(("header", soup.body))

    position = 0
    for nav_type, section in sections:
        for anchor in section.find_all("a", href=True):
            label = link_text(anchor)
            href = absolute_url(base_url, str(anchor.get("href")))
            if not label or not href:
                continue
            key = (nav_type, label.lower(), href)
            if key in seen:
                continue
            seen.add(key)
            parent_label = None
            parent_li = anchor.find_parent("li")
            if parent_li:
                parent_link = parent_li.find("a", href=True)
                if parent_link and parent_link is not anchor:
                    parent_label = link_text(parent_link)
            nav_items.append(
                {
                    "label": label,
                    "url": href,
                    "local_path": path_from_url(href) if is_internal_url(base_url, href) else href,
                    "parent_label": parent_label,
                    "position": position,
                    "nav_type": nav_type,
                }
            )
            position += 1
    return nav_items


def extract_breadcrumbs(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
    """Extract breadcrumbs from common semantic and class-based patterns."""

    crumbs: list[dict[str, str]] = []
    selectors = [
        '[aria-label*="breadcrumb" i]',
        ".breadcrumb",
        ".breadcrumbs",
        'nav[class*="breadcrumb" i]',
        'ol[class*="breadcrumb" i]',
    ]
    for container in soup.select(", ".join(selectors)):
        for anchor in container.find_all("a", href=True):
            label = link_text(anchor)
            href = absolute_url(base_url, str(anchor.get("href")))
            if label and href:
                crumbs.append({"label": label, "url": href, "local_path": path_from_url(href)})
        current = clean_text(container.get_text(" "))
        if current and not crumbs:
            crumbs.append({"label": current, "url": base_url, "local_path": path_from_url(base_url)})
        if crumbs:
            break
    if not crumbs and path_from_url(base_url) != "/":
        crumbs.append({"label": "Home", "url": urljoin(base_url, "/"), "local_path": "/"})
        crumbs.append({"label": slug_from_url(base_url).replace("-", " ").title(), "url": base_url, "local_path": path_from_url(base_url)})
    return crumbs


def extract_categories(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    """Extract collection/category cards and links."""

    categories: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = absolute_url(base_url, str(anchor.get("href")))
        if not href:
            continue
        path = urlparse(href).path.rstrip("/")
        if not path.startswith("/collections/"):
            continue
        slug = path.split("/")[-1]
        if not slug or slug in seen:
            continue
        seen.add(slug)
        image = first_image(anchor, base_url)
        label = link_text(anchor) or slug.replace("-", " ").title()
        categories.append(
            {
                "name": label,
                "slug": slug,
                "url": href,
                "position": len(categories),
                "image": image,
                "metadata": {"source": "collection-link"},
            }
        )
    return categories


def first_image(container: Tag, base_url: str) -> str | None:
    """Find the first image URL inside a container."""

    image = container.find("img")
    if image:
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            resolved = absolute_url(base_url, image.get(attr))
            if resolved:
                return resolved
        srcset = srcset_urls(base_url, image.get("srcset"))
        if srcset:
            return srcset[-1]
    style = container.get("style")
    if style:
        urls = css_urls(style, base_url)
        if urls:
            return urls[0]
    return None


def css_urls(css_text: str, base_url: str) -> list[str]:
    """Extract URL references from CSS text."""

    urls: list[str] = []
    for match in re.finditer(r"url\((['\"]?)(.*?)\1\)", css_text, flags=re.IGNORECASE):
        resolved = absolute_url(base_url, match.group(2))
        if resolved:
            urls.append(resolved)
    return urls


def nearest_product_card(anchor: Tag) -> Tag:
    """Find the smallest useful product card around a product link."""

    for parent in anchor.parents:
        if not isinstance(parent, Tag):
            continue
        classes = " ".join(parent.get("class", []))
        if any(token in classes.lower() for token in ("product", "card", "grid__item", "collection")):
            return parent
        if parent.name in {"li", "article"}:
            return parent
    return anchor


def extract_price(text: str) -> str | None:
    """Find the first price-like token."""

    match = PRICE_RE.search(text)
    return match.group(0).strip() if match else None


def extract_compare_price(card: Tag) -> str | None:
    """Find a compare-at/original price."""

    selectors = [
        ".price__compare",
        ".price-item--regular",
        "s",
        "del",
        '[class*="compare" i]',
        '[class*="was" i]',
    ]
    for tag in card.select(", ".join(selectors)):
        price = extract_price(clean_text(tag.get_text(" ")))
        if price:
            return price
    return None


def extract_rating(card: Tag) -> tuple[str | None, str | None]:
    """Extract rating and review count from common Shopify review widgets."""

    rating = None
    reviews_count = None
    rating_tag = card.select_one('[aria-label*="star" i], [class*="rating" i], [data-rating]')
    if rating_tag:
        rating = rating_tag.get("data-rating") or rating_tag.get("aria-label") or clean_text(rating_tag.get_text(" "))
        rating = clean_text(str(rating))
    review_tag = card.select_one('[class*="review" i], [data-review-count], [aria-label*="review" i]')
    if review_tag:
        reviews_count = review_tag.get("data-review-count") or clean_text(review_tag.get_text(" "))
        reviews_count = clean_text(str(reviews_count))
    return rating or None, reviews_count or None


def product_name_from_card(card: Tag, anchor: Tag) -> str:
    """Extract a product name from a card."""

    selectors = [
        ".card__heading",
        ".product-title",
        ".product-item__title",
        ".full-unstyled-link",
        "h1",
        "h2",
        "h3",
        "h4",
    ]
    for tag in card.select(", ".join(selectors)):
        text = clean_text(tag.get_text(" "))
        if text:
            return text
    return link_text(anchor) or slug_from_url(str(anchor.get("href"))).replace("-", " ").title()


def product_description_from_card(card: Tag) -> str | None:
    """Extract a short product description."""

    for selector in (".product-description", ".card__information p", ".rte", "[class*='description']"):
        tag = card.select_one(selector)
        if tag:
            text = clean_text(tag.get_text(" "))
            if text:
                return text
    return None


def image_urls_from_container(container: Tag, base_url: str) -> list[str]:
    """Extract image URLs from img tags, srcsets, and inline CSS."""

    urls: list[str] = []
    for image in container.find_all("img"):
        for attr in ("src", "data-src", "data-original", "data-lazy-src"):
            resolved = absolute_url(base_url, image.get(attr))
            if resolved:
                urls.append(resolved)
        urls.extend(srcset_urls(base_url, image.get("srcset")))
    for tag in container.find_all(style=True):
        urls.extend(css_urls(str(tag.get("style")), base_url))
    return dedupe(urls)


def extract_products_from_cards(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    """Extract product listings from rendered HTML cards."""

    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    category_slug = slug_from_url(base_url) if "/collections/" in path_from_url(base_url) else None
    for anchor in soup.find_all("a", href=True):
        href = absolute_url(base_url, str(anchor.get("href")))
        if not href or "/products/" not in urlparse(href).path:
            continue
        clean_href = href.split("?")[0].rstrip("/")
        if clean_href in seen:
            continue
        seen.add(clean_href)
        card = nearest_product_card(anchor)
        text = clean_text(card.get_text(" "))
        price = extract_price(text)
        compare_at = extract_compare_price(card)
        rating, reviews_count = extract_rating(card)
        lowered = text.lower()
        availability = "Sold out" if "sold out" in lowered or "unavailable" in lowered else "Available"
        products.append(
            {
                "product_url": clean_href,
                "slug": slug_from_url(clean_href),
                "name": product_name_from_card(card, anchor),
                "description": product_description_from_card(card),
                "price": price,
                "compare_at_price": compare_at,
                "discount": discount_text(card),
                "rating": rating,
                "reviews_count": reviews_count,
                "availability": availability,
                "images": image_urls_from_container(card, base_url),
                "category_slug": category_slug,
                "position": len(products),
                "raw": {"source": "html-card", "text": text[:2000]},
            }
        )
    return products


def discount_text(card: Tag) -> str | None:
    """Extract visible discount/sale labels."""

    for selector in (".badge", ".sale", "[class*='discount' i]", "[class*='save' i]"):
        tag = card.select_one(selector)
        if tag:
            text = clean_text(tag.get_text(" "))
            if text:
                return text
    text = clean_text(card.get_text(" "))
    match = re.search(r"(?:save|sale|off)\s+[\w\s%$.-]+", text, flags=re.IGNORECASE)
    return clean_text(match.group(0)) if match else None


def extract_json_products(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    """Extract Product objects from JSON-LD and Shopify script blobs."""

    products: list[dict[str, Any]] = []
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        if not text or "Product" not in text:
            continue
        for obj in json_objects_from_script(text):
            products.extend(products_from_json_object(obj, base_url))
    return products


def json_objects_from_script(text: str) -> list[Any]:
    """Parse JSON objects from script content where possible."""

    objects: list[Any] = []
    stripped = text.strip()
    candidates = [stripped]
    for match in re.finditer(r"({.*?})", stripped, flags=re.DOTALL):
        snippet = match.group(1)
        if '"@type"' in snippet or '"Product"' in snippet:
            candidates.append(snippet)
    for candidate in candidates:
        try:
            objects.append(json.loads(candidate))
        except json.JSONDecodeError:
            continue
    return objects


def products_from_json_object(obj: Any, base_url: str) -> list[dict[str, Any]]:
    """Recursively extract products from parsed JSON."""

    products: list[dict[str, Any]] = []
    if isinstance(obj, list):
        for item in obj:
            products.extend(products_from_json_object(item, base_url))
        return products
    if not isinstance(obj, dict):
        return products

    obj_type = obj.get("@type") or obj.get("type")
    if isinstance(obj_type, list):
        is_product = any(str(item).lower() == "product" for item in obj_type)
    else:
        is_product = str(obj_type).lower() == "product"
    if is_product or {"name", "offers", "image"}.issubset(obj.keys()):
        product_url = absolute_url(base_url, obj.get("url")) or base_url
        offers = obj.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        images = obj.get("image") or []
        if isinstance(images, str):
            images = [images]
        products.append(
            {
                "product_url": product_url.rstrip("/"),
                "slug": slug_from_url(product_url),
                "name": clean_text(str(obj.get("name") or slug_from_url(product_url).replace("-", " ").title())),
                "description": clean_text(str(obj.get("description") or "")) or None,
                "price": str(offers.get("price") or offers.get("lowPrice") or "") or None,
                "compare_at_price": None,
                "discount": None,
                "rating": rating_from_json(obj),
                "reviews_count": review_count_from_json(obj),
                "availability": availability_from_json(offers),
                "images": [absolute_url(base_url, image) for image in images if absolute_url(base_url, image)],
                "category_slug": slug_from_url(base_url) if "/collections/" in path_from_url(base_url) else None,
                "position": 0,
                "raw": obj,
            }
        )

    for value in obj.values():
        products.extend(products_from_json_object(value, base_url))
    return products


def rating_from_json(obj: dict[str, Any]) -> str | None:
    """Extract aggregate rating from JSON-LD."""

    aggregate = obj.get("aggregateRating") or {}
    if isinstance(aggregate, dict):
        value = aggregate.get("ratingValue")
        return str(value) if value is not None else None
    return None


def review_count_from_json(obj: dict[str, Any]) -> str | None:
    """Extract review count from JSON-LD."""

    aggregate = obj.get("aggregateRating") or {}
    if isinstance(aggregate, dict):
        count = aggregate.get("reviewCount") or aggregate.get("ratingCount")
        return str(count) if count is not None else None
    return None


def availability_from_json(offers: dict[str, Any]) -> str | None:
    """Normalize schema.org availability values."""

    value = offers.get("availability") if isinstance(offers, dict) else None
    if not value:
        return None
    return str(value).split("/")[-1].replace("_", " ")


def merge_products(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge product lists by URL, preserving the richer values."""

    merged: dict[str, dict[str, Any]] = {}
    for product in primary + secondary:
        url = product["product_url"].rstrip("/")
        existing = merged.get(url, {})
        merged[url] = {**product, **{key: value for key, value in existing.items() if value}}
        for key, value in product.items():
            if value and not merged[url].get(key):
                merged[url][key] = value
        merged[url]["product_url"] = url
    for index, product in enumerate(merged.values()):
        product["position"] = product.get("position", index) or index
    return list(merged.values())


def extract_banners(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    """Extract hero, banner, and slideshow content."""

    banners: list[dict[str, Any]] = []
    selectors = [
        ".banner",
        ".slideshow",
        ".hero",
        '[class*="hero" i]',
        '[class*="slideshow" i]',
        '[class*="banner" i]',
    ]
    for section in soup.select(", ".join(selectors)):
        text = clean_text(section.get_text(" "))
        if not text and not first_image(section, base_url):
            continue
        title_tag = section.find(re.compile("^h[1-3]$"))
        subtitle_tag = section.find(["p", "span"])
        link = section.find("a", href=True)
        banners.append(
            {
                "title": clean_text(title_tag.get_text(" ")) if title_tag else None,
                "subtitle": clean_text(subtitle_tag.get_text(" ")) if subtitle_tag else None,
                "image_url": first_image(section, base_url),
                "link_url": absolute_url(base_url, str(link.get("href"))) if link else None,
                "html": str(section),
                "position": len(banners),
                "metadata": {"text": text[:1000]},
            }
        )
    return banners


def extract_assets(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Collect image, CSS, JavaScript, icon, SVG, and background URLs."""

    urls: list[str] = []
    for tag in soup.find_all(True):
        for attr in ASSET_ATTRS:
            value = tag.get(attr)
            if not value:
                continue
            resolved = absolute_url(base_url, str(value))
            if resolved and is_asset_url(resolved):
                urls.append(resolved)
        urls.extend(srcset_urls(base_url, tag.get("srcset")))
        style = tag.get("style")
        if style:
            urls.extend(css_urls(str(style), base_url))
    for style in soup.find_all("style"):
        urls.extend(css_urls(style.get_text(), base_url))
    return [url for url in dedupe(urls) if is_asset_url(url)]


def extract_page_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Collect internal links needed for navigation and pagination."""

    links: list[str] = []
    allowed_prefixes = ("/collections/", "/products/", "/pages/", "/apps/", "/policies/", "/search", "/")
    for anchor in soup.find_all("a", href=True):
        href = absolute_url(base_url, str(anchor.get("href")))
        if not href or not is_internal_url(base_url, href):
            continue
        parsed = urlparse(href)
        if parsed.path.startswith(allowed_prefixes):
            normalized = parsed._replace(fragment="").geturl().rstrip("/")
            links.append(normalized or f"{parsed.scheme}://{parsed.netloc}/")
    return dedupe(links)


def footer_html(soup: BeautifulSoup) -> str | None:
    """Return footer HTML for rendering when available."""

    footer = soup.find("footer")
    return str(footer) if footer else None


def content_sections(soup: BeautifulSoup) -> dict[str, Any]:
    """Extract high-level sections and body classes."""

    sections: list[dict[str, Any]] = []
    main = soup.find("main") or soup.body
    if main:
        for index, section in enumerate(main.find_all(["section", "article", "div"], recursive=False)):
            text = clean_text(section.get_text(" "))
            if text or section.find("img"):
                sections.append(
                    {
                        "tag": section.name,
                        "classes": section.get("class", []),
                        "id": section.get("id"),
                        "text": text[:2000],
                        "position": index,
                    }
                )
    return {
        "body_classes": soup.body.get("class", []) if soup.body else [],
        "sections": sections[:80],
    }


def dedupe(values: list[str]) -> list[str]:
    """Return unique values in original order."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


class PageParser:
    """Parse one HTML document into clone-friendly data."""

    def parse(self, html: str, url: str) -> ParsedPage:
        """Parse a page and return structured data."""

        soup = BeautifulSoup(html, "html.parser")
        title = clean_text(soup.title.get_text(" ")) if soup.title else ""
        headers = extract_headers(soup)
        navigation = extract_navigation(soup, url)
        categories = extract_categories(soup, url)
        products = merge_products(extract_products_from_cards(soup, url), extract_json_products(soup, url))
        breadcrumbs = extract_breadcrumbs(soup, url)
        banners = extract_banners(soup, url)
        assets = extract_assets(soup, url)
        for category in categories:
            if category.get("image"):
                assets.append(category["image"])
        for product in products:
            assets.extend(product.get("images", []))
        for banner in banners:
            if banner.get("image_url"):
                assets.append(banner["image_url"])

        page = {
            "url": url.rstrip("/") if path_from_url(url) != "/" else urljoin(url, "/"),
            "slug": slug_from_url(url),
            "path": path_from_url(url),
            "page_type": page_type(url),
            "title": title,
            "meta": extract_meta(soup),
            "headers": headers,
            "menus": [item for item in navigation if item["nav_type"] == "header"],
            "navigation": navigation,
            "breadcrumbs": breadcrumbs,
            "categories": categories,
            "content": {**content_sections(soup), "banners": banners},
            "footer_html": footer_html(soup),
            "text_content": extract_visible_text(soup),
        }
        return ParsedPage(
            page=page,
            products=products,
            categories=categories,
            assets=dedupe(assets),
            product_urls=dedupe([product["product_url"] for product in products]),
            page_urls=extract_page_links(soup, url),
        )
