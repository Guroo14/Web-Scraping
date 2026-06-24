# Karma Items Flask Catalog

A complete Flask + SQLite e-commerce catalog generator for crawling and indexing
<https://karmaitems.com/>. The app crawls product and collection pages with
Playwright, parses Shopify-style markup with BeautifulSoup, stores normalized
data in SQLite, exports JSON/CSV, and renders a premium responsive storefront
with Bootstrap 5 and vanilla JavaScript.

## Features

- Async Playwright crawler with sitemap and Shopify `products.json` discovery.
- BeautifulSoup parser for products, images, breadcrumbs, navigation links, and
  collection/category hierarchy.
- SQLite persistence for categories, products, product images, crawl pages, and
  product/category relationships.
- JSON and CSV exports under `data/exports/`.
- Flask pages for home, category/collection browsing, search, product details,
  related products, breadcrumbs, filters, sticky navigation, image galleries, and
  dark mode.
- Responsive layouts for desktop, tablet, and mobile.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

## Crawl and index the catalog

```bash
python crawler.py --max-pages 250
```

You can tune crawl behavior with environment variables:

- `KARMA_TARGET_SITE` defaults to `https://karmaitems.com/`
- `KARMA_CRAWLER_MAX_PAGES` defaults to `250`
- `KARMA_CRAWLER_MAX_DEPTH` defaults to `5`
- `KARMA_DATABASE` defaults to `data/catalog.db`

The crawler writes:

- SQLite database: `data/catalog.db`
- JSON export: `data/exports/catalog.json`
- Product CSV: `data/exports/products.csv`
- Category CSV: `data/exports/categories.csv`

## Run the storefront

```bash
flask --app app run --host 0.0.0.0 --port 5000
```

Open <http://localhost:5000> after crawling to browse the populated catalog.

You can also trigger a local crawl from the empty-state button on the homepage,
or use Flask CLI commands:

```bash
flask --app app init-db
flask --app app crawl
flask --app app export
```
