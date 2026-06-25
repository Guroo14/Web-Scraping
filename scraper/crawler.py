"""Playwright crawler for scraping Karma Items pages into the local clone database."""

from __future__ import annotations

import argparse
import logging
import re
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from database.db import (
    DEFAULT_DB_PATH,
    get_connection,
    init_db,
    replace_banners,
    replace_design_tokens,
    replace_navigation,
    upsert_category,
    upsert_page,
    upsert_product,
)
from scraper.downloader import AssetDownloader
from scraper.extractor import DesignExtractor, css_link_urls
from scraper.parser import PageParser, path_from_url, slug_from_url


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTML_ARCHIVE = PROJECT_ROOT / "data" / "pages"

TARGET_URLS = {
    "home": "https://karmaitems.com/",
    "best_sellers": "https://karmaitems.com/collections/best-sellers",
    "mala_necklaces": "https://karmaitems.com/collections/mala-necklace-collection",
    "bracelets": "https://karmaitems.com/collections/bracelet-collection",
    "pendant_necklaces": "https://karmaitems.com/collections/pendant-necklaces",
    "spiritual_artifacts": "https://karmaitems.com/collections/spiritual-artefacts",
    "track_your_order": "https://karmaitems.com/apps/trackingmore",
}

COMPUTED_STYLE_SCRIPT = """
(selectors) => {
  const properties = [
    'color', 'background-color', 'font-family', 'font-size', 'font-weight',
    'line-height', 'letter-spacing', 'margin-top', 'margin-right',
    'margin-bottom', 'margin-left', 'padding-top', 'padding-right',
    'padding-bottom', 'padding-left', 'border-radius', 'box-shadow',
    'display', 'grid-template-columns', 'grid-template-rows', 'gap',
    'column-gap', 'row-gap', 'justify-content', 'align-items',
    'flex-direction'
  ];
  const result = [];
  for (const selector of selectors) {
    const elements = Array.from(document.querySelectorAll(selector)).slice(0, 8);
    for (const element of elements) {
      const styles = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const collected = {};
      for (const property of properties) {
        collected[property] = styles.getPropertyValue(property);
      }
      result.push({
        selector,
        tag: element.tagName.toLowerCase(),
        classes: Array.from(element.classList).slice(0, 8),
        rect: { width: rect.width, height: rect.height },
        styles: collected
      });
    }
  }
  return result;
}
"""

RESOURCE_SCRIPT = """
() => performance.getEntriesByType('resource').map((entry) => entry.name)
"""


class KarmaItemsCrawler:
    """Scrape the configured site pages and discovered product/navigation pages."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, max_pages: int = 200, headless: bool = True) -> None:
        self.db_path = db_path
        self.max_pages = max_pages
        self.headless = headless
        self.parser = PageParser()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            }
        )

    def crawl(self, seeds: list[str] | None = None) -> None:
        """Run the crawler end-to-end."""

        init_db(self.db_path)
        queue: deque[str] = deque(seeds or list(TARGET_URLS.values()))
        visited: set[str] = set()

        with get_connection(self.db_path) as conn:
            downloader = AssetDownloader(conn=conn)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                context = browser.new_context(
                    viewport={"width": 1440, "height": 1400},
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                while queue and len(visited) < self.max_pages:
                    url = self.normalize_page_url(queue.popleft())
                    if not url or url in visited:
                        continue
                    if not self.should_crawl(url):
                        continue
                    visited.add(url)
                    LOGGER.info("Scraping %s", url)
                    try:
                        result = self.scrape_one(page, url, conn, downloader)
                    except Exception as exc:
                        LOGGER.exception("Failed to scrape %s: %s", url, exc)
                        continue

                    for next_url in result["next_urls"]:
                        normalized = self.normalize_page_url(next_url)
                        if normalized and normalized not in visited and self.should_crawl(normalized):
                            queue.append(normalized)
                context.close()
                browser.close()
            conn.commit()

    def scrape_one(self, browser_page: Any, url: str, conn: Any, downloader: AssetDownloader) -> dict[str, Any]:
        """Scrape one URL and write extracted data to SQLite."""

        browser_page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            browser_page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            LOGGER.debug("Network idle timed out for %s; continuing with current DOM.", url)
        self.auto_scroll(browser_page)

        html = browser_page.content()
        parsed = self.parser.parse(html, url)
        resource_urls = self.browser_resource_urls(browser_page)
        computed_snapshot = self.computed_style_snapshot(browser_page)
        css_urls = css_link_urls(html, url)

        asset_urls = list(dict.fromkeys(parsed.assets + resource_urls + css_urls))
        url_map = downloader.download_many(asset_urls, source_page_url=url)
        local_html = downloader.rewrite_html(html, url_map)
        html_path = self.write_html_archive(parsed.page["slug"], local_html)
        parsed.page["local_html"] = local_html
        parsed.page["html_path"] = str(html_path.relative_to(PROJECT_ROOT))

        page_id = upsert_page(conn, parsed.page)
        replace_navigation(conn, page_id, parsed.page["navigation"])

        for category in parsed.categories:
            image_url = category.get("image")
            category.setdefault("metadata", {})["image"] = image_url
            category["image_asset_id"] = self.asset_id_for_url(conn, image_url) if image_url else None
            upsert_category(conn, category)

        products = parsed.products
        if parsed.page["page_type"] == "product" and not products:
            products = [self.product_from_product_page(parsed.page, local_html)]
        for product in products:
            product["page_id"] = page_id if parsed.page["page_type"] == "product" else product.get("page_id")
            if product.get("images"):
                product["image_asset_id"] = self.asset_id_for_url(conn, product["images"][0])
            upsert_product(conn, product)

        banners = parsed.page["content"].get("banners", [])
        for banner in banners:
            image_url = banner.get("image_url")
            banner["image_asset_id"] = self.asset_id_for_url(conn, image_url) if image_url else None
        replace_banners(conn, page_id, banners)

        tokens = self.design_tokens(html, url, css_urls, computed_snapshot, downloader)
        replace_design_tokens(conn, page_id, tokens)
        conn.commit()

        next_urls = self.next_urls(parsed.product_urls, parsed.page_urls)
        return {"page_id": page_id, "next_urls": next_urls}

    def design_tokens(
        self,
        html: str,
        url: str,
        css_urls: list[str],
        computed_snapshot: list[dict[str, Any]],
        downloader: AssetDownloader,
    ) -> list[dict[str, Any]]:
        """Extract and combine design metadata from multiple sources."""

        extractor = DesignExtractor(url)
        tokens = extractor.extract_from_html(html)
        tokens.extend(extractor.extract_from_computed_snapshot(computed_snapshot))
        for css_url in css_urls:
            local = downloader.url_map.get(css_url)
            css_text = None
            if local:
                css_path = PROJECT_ROOT / local.lstrip("/")
                if css_path.exists():
                    css_text = css_path.read_text(encoding="utf-8", errors="ignore")
            if css_text is None:
                css_text = self.fetch_text(css_url)
            if css_text:
                tokens.extend(extractor.extract_from_css(css_text, source_url=css_url))
        return tokens[:5000]

    def fetch_text(self, url: str) -> str | None:
        """Fetch a text resource."""

        try:
            response = self.session.get(url, timeout=20)
            response.raise_for_status()
        except requests.RequestException:
            return None
        return response.text

    def auto_scroll(self, browser_page: Any) -> None:
        """Scroll through the page to trigger lazy-loaded images and products."""

        browser_page.evaluate(
            """
            async () => {
              await new Promise((resolve) => {
                let totalHeight = 0;
                const distance = 500;
                const timer = setInterval(() => {
                  window.scrollBy(0, distance);
                  totalHeight += distance;
                  if (totalHeight >= document.body.scrollHeight) {
                    clearInterval(timer);
                    window.scrollTo(0, 0);
                    resolve();
                  }
                }, 120);
              });
            }
            """
        )

    def browser_resource_urls(self, browser_page: Any) -> list[str]:
        """Read browser-discovered resource URLs."""

        try:
            urls = browser_page.evaluate(RESOURCE_SCRIPT)
        except Exception:
            return []
        return [url for url in urls if isinstance(url, str)]

    def computed_style_snapshot(self, browser_page: Any) -> list[dict[str, Any]]:
        """Collect representative computed styles from the rendered page."""

        selectors = [
            "body",
            "header",
            "nav",
            "main",
            "footer",
            "h1",
            "h2",
            "p",
            "a",
            "button",
            ".banner",
            ".hero",
            ".slideshow",
            ".grid",
            ".product-card",
            ".card",
            ".collection",
            ".footer",
        ]
        try:
            return browser_page.evaluate(COMPUTED_STYLE_SCRIPT, selectors)
        except Exception:
            return []

    def write_html_archive(self, slug: str, html: str) -> Path:
        """Save a local copy of rewritten HTML."""

        HTML_ARCHIVE.mkdir(parents=True, exist_ok=True)
        safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug).strip("-") or "page"
        path = HTML_ARCHIVE / f"{safe_slug}.html"
        path.write_text(html, encoding="utf-8")
        return path

    def asset_id_for_url(self, conn: Any, url: str | None) -> int | None:
        """Look up a downloaded asset id by original URL."""

        if not url:
            return None
        row = conn.execute("SELECT id FROM assets WHERE original_url = ?", (url,)).fetchone()
        return int(row["id"]) if row else None

    def product_from_product_page(self, page: dict[str, Any], html: str) -> dict[str, Any]:
        """Fallback product record for product pages without JSON-LD."""

        title = page.get("title") or slug_from_url(page["url"]).replace("-", " ").title()
        return {
            "product_url": page["url"].rstrip("/"),
            "slug": slug_from_url(page["url"]),
            "name": title,
            "description": page.get("text_content", "")[:1000],
            "price": None,
            "compare_at_price": None,
            "discount": None,
            "rating": None,
            "reviews_count": None,
            "availability": None,
            "images": [],
            "category_slug": None,
            "position": 0,
            "raw": {"source": "product-page-fallback", "html_length": len(html)},
        }

    def next_urls(self, product_urls: list[str], page_urls: list[str]) -> list[str]:
        """Prioritize product URLs, collection pagination, and internal navigation pages."""

        urls: list[str] = []
        for url in product_urls + page_urls:
            parsed = urlparse(url)
            path = parsed.path.rstrip("/") or "/"
            if path == "/" or path.startswith(("/collections/", "/products/", "/pages/", "/apps/", "/policies/")):
                urls.append(url)
        return list(dict.fromkeys(urls))

    def normalize_page_url(self, url: str | None) -> str | None:
        """Normalize crawl URLs."""

        if not url:
            return None
        parsed = urlparse(url)
        if not parsed.scheme:
            return None
        if parsed.scheme not in {"http", "https"}:
            return None
        normalized = parsed._replace(fragment="").geturl()
        if path_from_url(normalized) == "/":
            return f"{parsed.scheme}://{parsed.netloc}/"
        return normalized.rstrip("/")

    def should_crawl(self, url: str) -> bool:
        """Keep the crawler scoped to Karma Items and useful page paths."""

        parsed = urlparse(url)
        if parsed.netloc != "karmaitems.com":
            return False
        path = parsed.path.rstrip("/") or "/"
        if path == "/":
            return True
        return path.startswith(("/collections/", "/products/", "/pages/", "/apps/", "/policies/"))


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI parser."""

    parser = argparse.ArgumentParser(description="Scrape Karma Items into a local SQLite-backed clone.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path.")
    parser.add_argument("--max-pages", type=int, default=200, help="Maximum number of pages to crawl.")
    parser.add_argument("--headed", action="store_true", help="Run Chromium with a visible browser window.")
    parser.add_argument("urls", nargs="*", help="Optional seed URLs. Defaults to the required Karma Items URLs.")
    return parser


def main() -> None:
    """CLI entrypoint."""

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_arg_parser().parse_args()
    crawler = KarmaItemsCrawler(db_path=args.db, max_pages=args.max_pages, headless=not args.headed)
    crawler.crawl(args.urls or None)


if __name__ == "__main__":
    main()
