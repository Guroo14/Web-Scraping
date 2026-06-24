"""HTML parsing logic for Shopify-style catalog pages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from utils import (
    absolute_image_url,
    canonical_path,
    clean_text,
    detect_currency,
    extract_json_ld,
    normalize_url,
    parse_price,
    same_site,
    slug_from_url,
    slugify,
    soup_text,
)


SKIP_PATH_PARTS = {
    "cart",
    "checkout",
    "account",
    "challenge",
    "policies",
    "privacy-policy",
    "terms-of-service",
}


@dataclass
class ParsedLink:
    url: str
    text: str


class SiteScraper:
    """Parse pages into normalized crawler records."""

    def __init__(self, base_url: str) -> None:
        self.base_url = normalize_url(base_url, base_url)

    def parse(self, url: str, html: str, depth: int = 0, discovered_from: str = "") -> dict[str, Any]:
        url = normalize_url(url, self.base_url)
        soup = BeautifulSoup(html or "", "html.parser")
        json_ld = extract_json_ld(soup)
        title = self._page_title(soup)
        breadcrumbs = self._extract_breadcrumbs(soup, url, json_ld)
        page_type = self._page_type(url, soup, json_ld)

        categories = self._extract_categories(soup, url, breadcrumbs)
        current_category_slug = self._current_category_slug(url, categories)
        products = self._extract_listing_products(soup, url, current_category_slug)
        if page_type == "product":
            detail = self._extract_product_detail(soup, url, breadcrumbs, json_ld)
            if detail:
                products = [detail, *[product for product in products if product["slug"] != detail["slug"]]]

        links = self._extract_links(soup, url)
        return {
            "page": {
                "url": url,
                "page_type": page_type,
                "title": title,
                "status_code": 200,
                "depth": depth,
                "discovered_from": discovered_from,
                "metadata": {"canonical_path": canonical_path(url), "breadcrumbs": breadcrumbs},
            },
            "categories": categories,
            "products": products,
            "links": [link.__dict__ for link in links],
        }

    def _page_title(self, soup: BeautifulSoup) -> str:
        meta_title = soup.select_one('meta[property="og:title"], meta[name="twitter:title"]')
        if meta_title and meta_title.get("content"):
            return clean_text(meta_title["content"])
        h1 = soup.select_one("h1")
        if h1:
            return soup_text(h1)
        if soup.title:
            return clean_text(soup.title.get_text(" ", strip=True))
        return "Karma Items"

    def _page_type(self, url: str, soup: BeautifulSoup, json_ld: list[dict[str, Any]]) -> str:
        path = urlparse(url).path
        if "/products/" in path or any(self._json_type(item) == "product" for item in json_ld):
            return "product"
        if "/collections/" in path or "/categories/" in path:
            return "collection"
        if "/search" in path:
            return "search"
        if soup.select("a[href*='/products/']"):
            return "collection"
        if path in ("", "/"):
            return "home"
        return "page"

    def _extract_links(self, soup: BeautifulSoup, current_url: str) -> list[ParsedLink]:
        links: list[ParsedLink] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            raw_href = anchor.get("href", "")
            if raw_href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            normalized = normalize_url(raw_href, current_url)
            parsed = urlparse(normalized)
            if not same_site(normalized, self.base_url):
                continue
            if any(part in SKIP_PATH_PARTS for part in parsed.path.strip("/").split("/")):
                continue
            if self._looks_like_asset(parsed.path):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            links.append(ParsedLink(url=normalized, text=soup_text(anchor)))
        return links

    def _extract_breadcrumbs(
        self, soup: BeautifulSoup, url: str, json_ld: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        crumbs: list[dict[str, str]] = []
        for item in json_ld:
            if self._json_type(item) != "breadcrumblist":
                continue
            for element in item.get("itemListElement", []):
                if not isinstance(element, dict):
                    continue
                nested = element.get("item") if isinstance(element.get("item"), dict) else {}
                name = clean_text(element.get("name") or nested.get("name"))
                item_url = nested.get("@id") or nested.get("url") or element.get("item")
                if name:
                    crumbs.append({"name": name, "url": normalize_url(str(item_url or ""), url)})

        if crumbs:
            return self._dedupe_crumbs(crumbs)

        selectors = [
            "[aria-label='breadcrumb'] a",
            ".breadcrumb a",
            ".breadcrumbs a",
            "nav.breadcrumb a",
            "nav[role='navigation'] a",
        ]
        for selector in selectors:
            for anchor in soup.select(selector):
                name = soup_text(anchor)
                if name:
                    crumbs.append({"name": name, "url": normalize_url(anchor.get("href", ""), url)})
            if crumbs:
                break

        current = soup.select_one("[aria-current='page'], .breadcrumb .active, .breadcrumbs .current")
        if current:
            current_name = soup_text(current)
            if current_name:
                crumbs.append({"name": current_name, "url": url})

        return self._dedupe_crumbs(crumbs)

    def _dedupe_crumbs(self, crumbs: list[dict[str, str]]) -> list[dict[str, str]]:
        cleaned: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for crumb in crumbs:
            name = clean_text(crumb.get("name"))
            if not name:
                continue
            key = (name.lower(), crumb.get("url", ""))
            if key in seen:
                continue
            seen.add(key)
            cleaned.append({"name": name, "url": crumb.get("url", "")})
        return cleaned

    def _extract_categories(
        self, soup: BeautifulSoup, current_url: str, breadcrumbs: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        categories: list[dict[str, Any]] = []
        seen: set[str] = set()

        chain = [crumb for crumb in breadcrumbs if self._is_category_url(crumb.get("url", ""))]
        parent_slug: str | None = None
        path_parts: list[str] = []
        for depth, crumb in enumerate(chain):
            category = self._category_from_link(crumb["name"], crumb.get("url", ""), parent_slug, depth, path_parts)
            if category["slug"] not in seen:
                categories.append(category)
                seen.add(category["slug"])
            parent_slug = category["slug"]
            path_parts.append(category["slug"])

        if self._is_category_url(current_url):
            current_name = self._category_name_from_page(soup, current_url)
            category = self._category_from_link(current_name, current_url, parent_slug, len(path_parts), path_parts)
            if category["slug"] not in seen:
                categories.append(category)
                seen.add(category["slug"])

        nav_selectors = [
            "header a[href*='/collections/']",
            "nav a[href*='/collections/']",
            ".menu a[href*='/collections/']",
            ".navigation a[href*='/collections/']",
            "a[href*='/collections/']",
            "a[href*='/categories/']",
        ]
        sort_order = len(categories)
        for selector in nav_selectors:
            for anchor in soup.select(selector):
                name = soup_text(anchor)
                href = anchor.get("href", "")
                if not name or not href:
                    continue
                full_url = normalize_url(href, current_url)
                if not self._is_category_url(full_url):
                    continue
                category = self._category_from_link(name, full_url, None, 0, [])
                category["sort_order"] = sort_order
                sort_order += 1
                if category["slug"] in seen:
                    continue
                categories.append(category)
                seen.add(category["slug"])
        return categories

    def _category_from_link(
        self,
        name: str,
        url: str,
        parent_slug: str | None,
        depth: int,
        parent_path: list[str],
    ) -> dict[str, Any]:
        slug = slug_from_url(url, fallback=slugify(name)) if url else slugify(" ".join([*parent_path, name]))
        return {
            "name": clean_text(name) or slug.replace("-", " ").title(),
            "slug": slug,
            "url": url,
            "parent_slug": parent_slug,
            "path": " / ".join([*parent_path, slug]),
            "depth": depth,
        }

    def _category_name_from_page(self, soup: BeautifulSoup, current_url: str) -> str:
        heading = soup.select_one("h1, .collection-hero__title, .collection-title")
        if heading:
            return soup_text(heading)
        slug = slug_from_url(current_url, "collection")
        return slug.replace("-", " ").title()

    def _current_category_slug(self, current_url: str, categories: list[dict[str, Any]]) -> str | None:
        if not self._is_category_url(current_url):
            return None
        current_slug = slug_from_url(current_url, "collection")
        for category in categories:
            if category["slug"] == current_slug:
                return current_slug
        return current_slug

    def _extract_listing_products(
        self, soup: BeautifulSoup, current_url: str, current_category_slug: str | None
    ) -> list[dict[str, Any]]:
        products: list[dict[str, Any]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href*='/products/']"):
            product_url = normalize_url(anchor.get("href", ""), current_url)
            slug = slug_from_url(product_url, fallback=soup_text(anchor) or "product")
            if slug in seen:
                continue
            seen.add(slug)
            card = self._find_product_card(anchor)
            title = self._first_text(
                [
                    card.select_one(".card__heading") if card else None,
                    card.select_one(".product-card__title") if card else None,
                    card.select_one("[class*='title']") if card else None,
                    anchor,
                ]
            )
            price_text = self._first_text(
                [
                    card.select_one(".price") if card else None,
                    card.select_one("[class*='price']") if card else None,
                    card,
                ]
            )
            image = self._first_image(card or anchor, current_url)
            if not title or title.lower() in {"quick view", "view product", "add to cart"}:
                title = slug.replace("-", " ").title()
            products.append(
                {
                    "title": title,
                    "slug": slug,
                    "url": product_url,
                    "description": "",
                    "price": parse_price(price_text),
                    "currency": detect_currency(price_text),
                    "primary_image": image.get("url", ""),
                    "images": [image] if image.get("url") else [],
                    "source_path": canonical_path(current_url),
                    "category_slugs": [current_category_slug] if current_category_slug else [],
                    "metadata": {"summary_source": "listing"},
                }
            )
        return products

    def _extract_product_detail(
        self,
        soup: BeautifulSoup,
        current_url: str,
        breadcrumbs: list[dict[str, str]],
        json_ld: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        json_product = next((item for item in json_ld if self._json_type(item) == "product"), {})
        title = clean_text(json_product.get("name")) or self._first_text(
            [
                soup.select_one("h1"),
                soup.select_one(".product__title"),
                soup.select_one("[class*='product-title']"),
                soup.select_one('meta[property="og:title"]'),
            ]
        )
        if not title:
            return None

        offers = json_product.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price_text = self._first_text(
            [
                soup.select_one(".price"),
                soup.select_one("[class*='price']"),
                soup.select_one('meta[property="product:price:amount"]'),
            ]
        )
        description = clean_text(json_product.get("description")) or self._description(soup)
        images = self._product_images(soup, current_url, json_product)
        category_slugs = [
            slug_from_url(crumb["url"], fallback=slugify(crumb["name"]))
            for crumb in breadcrumbs
            if self._is_category_url(crumb.get("url", ""))
        ]

        return {
            "title": title,
            "slug": slug_from_url(current_url, fallback=title),
            "url": current_url,
            "description": description,
            "price": parse_price(offers.get("price")) or parse_price(price_text),
            "compare_at_price": parse_price(offers.get("highPrice")),
            "currency": offers.get("priceCurrency") or detect_currency(price_text),
            "vendor": clean_text(json_product.get("brand", {}).get("name") if isinstance(json_product.get("brand"), dict) else json_product.get("brand")),
            "sku": clean_text(json_product.get("sku")),
            "availability": clean_text(str(offers.get("availability", ""))).split("/")[-1],
            "primary_image": images[0]["url"] if images else "",
            "images": images,
            "source_path": " > ".join(crumb["name"] for crumb in breadcrumbs),
            "category_slugs": category_slugs,
            "metadata": {"breadcrumbs": breadcrumbs, "json_ld": bool(json_product)},
        }

    def _description(self, soup: BeautifulSoup) -> str:
        meta = soup.select_one('meta[name="description"], meta[property="og:description"]')
        if meta and meta.get("content"):
            return clean_text(meta["content"])
        selectors = [
            ".product__description",
            ".product-description",
            "[class*='product'] [class*='description']",
            "#ProductDescription",
        ]
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                return soup_text(element)
        return ""

    def _product_images(
        self, soup: BeautifulSoup, current_url: str, json_product: dict[str, Any]
    ) -> list[dict[str, str]]:
        images: list[dict[str, str]] = []
        seen: set[str] = set()

        raw_images = json_product.get("image", [])
        if isinstance(raw_images, str):
            raw_images = [raw_images]
        for raw_image in raw_images if isinstance(raw_images, list) else []:
            url = absolute_image_url(str(raw_image), current_url)
            if url and url not in seen:
                seen.add(url)
                images.append({"url": url, "alt": clean_text(json_product.get("name"))})

        selectors = [
            ".product img",
            ".product-gallery img",
            ".product__media img",
            "[class*='product'] img",
            'meta[property="og:image"]',
        ]
        for selector in selectors:
            for element in soup.select(selector):
                raw = element.get("content") or element.get("src") or element.get("data-src") or element.get("data-original")
                url = absolute_image_url(raw, current_url)
                if not url or url in seen:
                    continue
                seen.add(url)
                images.append({"url": url, "alt": clean_text(element.get("alt") or json_product.get("name"))})
                if len(images) >= 12:
                    return images
        return images

    def _find_product_card(self, anchor: Any) -> Any:
        class_markers = ("product", "card", "grid__item", "collection")

        def looks_like_card(tag: Any) -> bool:
            classes = " ".join(tag.get("class", [])) if tag and tag.has_attr("class") else ""
            return any(marker in classes.lower() for marker in class_markers)

        return anchor.find_parent(looks_like_card) or anchor.parent

    def _first_text(self, elements: list[Any]) -> str:
        for element in elements:
            if element is None:
                continue
            if getattr(element, "name", "") == "meta":
                value = clean_text(element.get("content"))
            else:
                value = soup_text(element)
            if value:
                return value
        return ""

    def _first_image(self, element: Any, current_url: str) -> dict[str, str]:
        if element is None:
            return {"url": "", "alt": ""}
        image = element if getattr(element, "name", "") == "img" else element.select_one("img")
        if not image:
            return {"url": "", "alt": ""}
        raw = image.get("src") or image.get("data-src") or image.get("data-original")
        return {"url": absolute_image_url(raw, current_url), "alt": clean_text(image.get("alt"))}

    def _is_category_url(self, url: str) -> bool:
        path = urlparse(url).path
        return "/collections/" in path or "/categories/" in path

    def _looks_like_asset(self, path: str) -> bool:
        lowered = path.lower()
        return lowered.endswith((".css", ".js", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".pdf"))

    def _json_type(self, item: dict[str, Any]) -> str:
        raw_type = item.get("@type", "")
        if isinstance(raw_type, list):
            raw_type = raw_type[0] if raw_type else ""
        return str(raw_type).lower()
