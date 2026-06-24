"""Async Playwright crawler that indexes karmaitems.com into SQLite."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from config import (
    CRAWLER_DELAY_SECONDS,
    CRAWLER_MAX_DEPTH,
    CRAWLER_MAX_PAGES,
    DATABASE_PATH,
    REQUEST_TIMEOUT_MS,
    TARGET_SITE,
    USER_AGENT,
)
from database import export_catalog, get_connection, ingest_parse_result, init_db, upsert_category, upsert_product
from scraper import SiteScraper
from utils import absolute_image_url, clean_text, normalize_url, same_site, slugify


@dataclass
class CrawlItem:
    url: str
    depth: int
    discovered_from: str = ""


class SiteCrawler:
    """Crawl a Shopify-style store and persist discovered catalog data."""

    def __init__(
        self,
        base_url: str = TARGET_SITE,
        max_pages: int = CRAWLER_MAX_PAGES,
        max_depth: int = CRAWLER_MAX_DEPTH,
        delay_seconds: float = CRAWLER_DELAY_SECONDS,
        database_path: str = str(DATABASE_PATH),
    ) -> None:
        self.base_url = normalize_url(base_url, base_url)
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.delay_seconds = delay_seconds
        self.database_path = database_path
        self.scraper = SiteScraper(self.base_url)

    async def crawl(self) -> dict[str, Any]:
        init_db(self.database_path)
        stats = {"pages_crawled": 0, "pages_failed": 0, "products_from_json": 0}

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1440, "height": 1200},
                java_script_enabled=True,
            )
            await context.route("**/*", self._route_filter)

            seed_urls = await self._seed_urls(context)
            queue: deque[CrawlItem] = deque(CrawlItem(url=url, depth=0) for url in seed_urls)
            visited: set[str] = set()

            with get_connection(self.database_path) as conn:
                stats["products_from_json"] = await self._index_shopify_products_json(context, conn)

            while queue and stats["pages_crawled"] < self.max_pages:
                item = queue.popleft()
                if item.url in visited or item.depth > self.max_depth:
                    continue
                visited.add(item.url)

                page = await context.new_page()
                try:
                    result = await self._crawl_page(page, item)
                except Exception as exc:  # pragma: no cover - defensive for unpredictable remote pages.
                    stats["pages_failed"] += 1
                    print(f"[crawler] failed {item.url}: {exc}")
                    await page.close()
                    continue
                await page.close()

                with get_connection(self.database_path) as conn:
                    ingest_parse_result(conn, result)

                stats["pages_crawled"] += 1
                for link in self._prioritized_links(result.get("links", [])):
                    next_url = normalize_url(link["url"], self.base_url)
                    if next_url not in visited and same_site(next_url, self.base_url):
                        queue.append(CrawlItem(url=next_url, depth=item.depth + 1, discovered_from=item.url))

                if self.delay_seconds:
                    await asyncio.sleep(self.delay_seconds)

            await browser.close()

        stats["exports"] = export_catalog(self.database_path)
        return stats

    async def _route_filter(self, route: Any) -> None:
        resource_type = route.request.resource_type
        if resource_type in {"font", "media"}:
            await route.abort()
            return
        await route.continue_()

    async def _crawl_page(self, page: Page, item: CrawlItem) -> dict[str, Any]:
        print(f"[crawler] {item.depth:02d} {item.url}")
        response = await page.goto(item.url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeoutError:
            pass
        html = await page.content()
        result = self.scraper.parse(item.url, html, depth=item.depth, discovered_from=item.discovered_from)
        result["page"]["status_code"] = response.status if response else 0
        return result

    async def _seed_urls(self, context: BrowserContext) -> list[str]:
        seeds = [
            self.base_url,
            urljoin(self.base_url, "/collections"),
            urljoin(self.base_url, "/collections/all"),
            urljoin(self.base_url, "/search"),
        ]
        sitemap_urls = await self._sitemap_urls(context)
        seeds.extend(sitemap_urls)
        return self._dedupe_urls(seeds)[: self.max_pages]

    async def _sitemap_urls(self, context: BrowserContext) -> list[str]:
        sitemap_url = urljoin(self.base_url, "/sitemap.xml")
        try:
            response = await context.request.get(sitemap_url, timeout=REQUEST_TIMEOUT_MS)
            if not response.ok:
                return []
            text = await response.text()
        except Exception:
            return []

        soup = BeautifulSoup(text, "xml")
        urls: list[str] = []
        for loc in soup.select("loc"):
            url = normalize_url(loc.get_text(strip=True), self.base_url)
            path = urlparse(url).path
            if not same_site(url, self.base_url):
                continue
            if any(part in path for part in ("/products/", "/collections/", "/pages/")) or path in ("", "/"):
                urls.append(url)
        return urls

    async def _index_shopify_products_json(self, context: BrowserContext, conn: Any) -> int:
        products_url = urljoin(self.base_url, "/products.json?limit=250")
        try:
            response = await context.request.get(products_url, timeout=REQUEST_TIMEOUT_MS)
            if not response.ok:
                return 0
            payload = await response.json()
        except Exception:
            return 0

        count = 0
        for item in payload.get("products", []):
            product_url = urljoin(self.base_url, f"/products/{item.get('handle')}")
            category_slugs: list[str] = []
            product_type = clean_text(item.get("product_type"))
            if product_type:
                category_slug = slugify(product_type)
                upsert_category(
                    conn,
                    {
                        "name": product_type,
                        "slug": category_slug,
                        "url": urljoin(self.base_url, f"/collections/{category_slug}"),
                        "path": category_slug,
                        "depth": 0,
                    },
                )
                category_slugs.append(category_slug)

            variants = item.get("variants") or []
            first_variant = variants[0] if variants else {}
            images = [
                {
                    "url": absolute_image_url(image.get("src"), self.base_url),
                    "alt": clean_text(image.get("alt") or item.get("title")),
                }
                for image in item.get("images", [])
                if image.get("src")
            ]
            upsert_product(
                conn,
                {
                    "title": clean_text(item.get("title")),
                    "slug": item.get("handle") or slugify(item.get("title", "product")),
                    "url": product_url,
                    "description": BeautifulSoup(item.get("body_html") or "", "html.parser").get_text(" ", strip=True),
                    "price": first_variant.get("price"),
                    "compare_at_price": first_variant.get("compare_at_price"),
                    "currency": "USD",
                    "vendor": clean_text(item.get("vendor")),
                    "sku": clean_text(first_variant.get("sku")),
                    "availability": "InStock" if first_variant.get("available", True) else "OutOfStock",
                    "primary_image": images[0]["url"] if images else "",
                    "images": images,
                    "source_path": "products.json",
                    "category_slugs": category_slugs,
                    "metadata": {"tags": item.get("tags", []), "source": "products.json"},
                },
            )
            count += 1
        return count

    def _prioritized_links(self, links: list[dict[str, str]]) -> list[dict[str, str]]:
        def priority(link: dict[str, str]) -> tuple[int, str]:
            path = urlparse(link["url"]).path
            if "/collections/" in path:
                return (0, link["url"])
            if "/products/" in path:
                return (1, link["url"])
            return (2, link["url"])

        return sorted(links, key=priority)

    def _dedupe_urls(self, urls: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for url in urls:
            normalized = normalize_url(url, self.base_url)
            if normalized in seen or not same_site(normalized, self.base_url):
                continue
            seen.add(normalized)
            result.append(normalized)
        return result


async def crawl_site(
    base_url: str = TARGET_SITE,
    max_pages: int = CRAWLER_MAX_PAGES,
    max_depth: int = CRAWLER_MAX_DEPTH,
    database_path: str = str(DATABASE_PATH),
) -> dict[str, Any]:
    crawler = SiteCrawler(base_url=base_url, max_pages=max_pages, max_depth=max_depth, database_path=database_path)
    return await crawler.crawl()


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl and index a Shopify-style catalog.")
    parser.add_argument("--base-url", default=TARGET_SITE)
    parser.add_argument("--max-pages", type=int, default=CRAWLER_MAX_PAGES)
    parser.add_argument("--max-depth", type=int, default=CRAWLER_MAX_DEPTH)
    parser.add_argument("--database", default=str(DATABASE_PATH))
    args = parser.parse_args()

    stats = asyncio.run(
        crawl_site(
            base_url=args.base_url,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            database_path=args.database,
        )
    )
    print(f"[crawler] complete: {stats}")


if __name__ == "__main__":
    main()
