# Karma Items Website Scraper and Local Clone

This project is a complete Python application for scraping the provided Karma Items storefront pages and recreating them locally with Flask, Jinja2, SQLite, and downloaded static assets.

## Folder structure

```text
project/
├── app.py
├── requirements.txt
├── README.md
├── scraper/
│   ├── __init__.py
│   ├── crawler.py
│   ├── parser.py
│   ├── downloader.py
│   └── extractor.py
├── database/
│   ├── __init__.py
│   ├── db.py
│   └── schema.sql
├── templates/
│   ├── base.html
│   ├── home.html
│   ├── category.html
│   ├── product.html
│   ├── page.html
│   └── partials/
│       └── product_grid.html
├── static/
│   ├── css/
│   │   └── site.css
│   ├── js/
│   │   └── navigation.js
│   └── images/
└── data/
    ├── pages/
    └── site.db
```

## Target URLs

The crawler is preconfigured for:

- `https://karmaitems.com/`
- `https://karmaitems.com/collections/best-sellers`
- `https://karmaitems.com/collections/mala-necklace-collection`
- `https://karmaitems.com/collections/bracelet-collection`
- `https://karmaitems.com/collections/pendant-necklaces`
- `https://karmaitems.com/collections/spiritual-artefacts`
- `https://karmaitems.com/apps/trackingmore`

It also follows discovered internal product, category, app, page, and policy links so navigation and product cards route to local cloned pages.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Scrape the website

```bash
python app.py scrape --max-pages 200
```

You can pass explicit seed URLs if needed:

```bash
python app.py scrape https://karmaitems.com/ https://karmaitems.com/collections/best-sellers
```

The scrape command:

1. Opens each page with Playwright.
2. Waits for rendered/lazy-loaded content.
3. Extracts visible text, metadata, navigation, breadcrumbs, categories, banners, products, product images, prices, discounts, ratings, review counts, and availability.
4. Downloads images, logos, icons, SVGs, CSS, JavaScript, fonts, and CSS background assets.
5. Analyzes colors, typography, spacing, borders, shadows, flex/grid layout data, and computed browser styles.
6. Stores all structured records in `data/site.db`.
7. Archives rewritten HTML in `data/pages/`.

## Serve the cloned website

```bash
python app.py --host 127.0.0.1 --port 5000
```

Then open:

```text
http://127.0.0.1:5000/
```

## Flask routes

The application automatically maps original internal URLs to local routes:

- `/`
- `/collections/<slug>`
- `/products/<slug>`
- `/apps/<path>`
- `/pages/<path>`
- `/policies/<path>`
- `/search?q=<term>`
- `/<path>` fallback for any other scraped page path

Header links, footer links, category cards, product cards, breadcrumbs, dropdown menus, and mobile navigation all use these local route mappings.

## Database schema

The SQLite schema is generated from `database/schema.sql` and includes:

- `pages`
- `products`
- `categories`
- `assets`
- `design_tokens`
- `navigation`
- `banners`

The app creates `data/site.db` automatically if it does not exist.

## Development checks

```bash
python -m compileall app.py scraper database
python -m py_compile app.py scraper/*.py database/*.py
```

## Notes

- The project uses Python only.
- `sqlite3` is provided by the Python standard library.
- Downloaded assets are saved under `static/`.
- The local clone prioritizes the scraped structure, content order, product ordering, navigation flow, images, typography, spacing, colors, and layout metadata captured from the source pages.
