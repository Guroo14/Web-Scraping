"""Flask application that serves the locally recreated Karma Items website."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flask import Flask, abort, g, redirect, render_template, request, url_for

from database.db import (
    DEFAULT_DB_PATH,
    get_connection,
    get_page_by_path,
    get_product_by_slug,
    init_db,
    list_banners,
    list_categories,
    list_navigation,
    list_products,
)
from scraper.crawler import TARGET_URLS, KarmaItemsCrawler


PROJECT_ROOT = Path(__file__).resolve().parent


def create_app(db_path: Path = DEFAULT_DB_PATH) -> Flask:
    """Create and configure the Flask app."""

    app = Flask(__name__)
    app.config["DB_PATH"] = Path(db_path)
    app.config["SITE_NAME"] = "Karma Items"

    if not app.config["DB_PATH"].exists():
        init_db(app.config["DB_PATH"])

    @app.before_request
    def open_database() -> None:
        g.db = get_connection(app.config["DB_PATH"])

    @app.teardown_request
    def close_database(_: Exception | None = None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.context_processor
    def inject_global_context() -> dict[str, Any]:
        header_nav = build_nav_tree(list_navigation(g.db, "header"))
        footer_nav = build_nav_tree(list_navigation(g.db, "footer"))
        categories = enrich_categories(g.db, list_categories(g.db))
        css_assets = g.db.execute(
            "SELECT local_path FROM assets WHERE asset_type = 'css' ORDER BY id"
        ).fetchall()
        return {
            "site_name": app.config["SITE_NAME"],
            "header_nav": header_nav,
            "footer_nav": footer_nav,
            "global_categories": categories,
            "downloaded_css": [row["local_path"] for row in css_assets],
            "local_url": local_url,
            "asset_url": lambda value: asset_url(g.db, value),
        }

    @app.route("/")
    def home() -> str:
        page = get_page_by_path(g.db, "/")
        products = enrich_products(g.db, list_products(g.db, limit=16))
        categories = enrich_categories(g.db, list_categories(g.db))
        banners = list_banners(g.db, page["id"]) if page else []
        return render_template("home.html", page=page, products=products, categories=categories, banners=banners)

    @app.route("/collections/<slug>")
    def category(slug: str) -> str:
        path = f"/collections/{slug}"
        page = get_page_by_path(g.db, path)
        products = enrich_products(g.db, list_products(g.db, category_slug=slug))
        if not page and not products:
            abort(404)
        category_record = category_by_slug(g.db, slug)
        banners = list_banners(g.db, page["id"]) if page else []
        return render_template(
            "category.html",
            page=page,
            products=products,
            category=category_record,
            banners=banners,
            slug=slug,
        )

    @app.route("/products/<slug>")
    def product(slug: str) -> str:
        product_record = get_product_by_slug(g.db, slug)
        page = get_page_by_path(g.db, f"/products/{slug}")
        if not product_record and not page:
            abort(404)
        if product_record:
            product_record = enrich_product(g.db, product_record)
        related = enrich_products(g.db, list_products(g.db, product_record.get("category_slug") if product_record else None, limit=8))
        return render_template("product.html", page=page, product=product_record, related_products=related)

    @app.route("/apps/<path:slug>")
    def app_page(slug: str) -> str:
        return render_generic_page(f"/apps/{slug}")

    @app.route("/pages/<path:slug>")
    def content_page(slug: str) -> str:
        return render_generic_page(f"/pages/{slug}")

    @app.route("/policies/<path:slug>")
    def policy_page(slug: str) -> str:
        return render_generic_page(f"/policies/{slug}")

    @app.route("/search")
    def search() -> str:
        query = request.args.get("q", "").strip().lower()
        products = list_products(g.db)
        if query:
            products = [
                product
                for product in products
                if query in (product.get("name") or "").lower()
                or query in (product.get("description") or "").lower()
            ]
        return render_template(
            "category.html",
            page={"title": "Search", "breadcrumbs": [{"label": "Home", "local_path": "/"}, {"label": "Search"}]},
            products=enrich_products(g.db, products),
            category={"name": f"Search results for '{query}'" if query else "Search"},
            banners=[],
            slug="search",
        )

    @app.route("/<path:req_path>")
    def fallback(req_path: str) -> str:
        normalized = "/" + req_path.strip("/")
        page = get_page_by_path(g.db, normalized)
        if not page:
            abort(404)
        if normalized.startswith("/collections/"):
            return redirect(url_for("category", slug=normalized.split("/")[-1]))
        if normalized.startswith("/products/"):
            return redirect(url_for("product", slug=normalized.split("/")[-1]))
        return render_template("page.html", page=page, banners=list_banners(g.db, page["id"]))

    def render_generic_page(path: str) -> str:
        page = get_page_by_path(g.db, path)
        if not page:
            abort(404)
        return render_template("page.html", page=page, banners=list_banners(g.db, page["id"]))

    return app


def local_url(value: str | None) -> str:
    """Convert original/internal URLs to cloned Flask paths."""

    if not value:
        return "#"
    if value.startswith("#"):
        return value
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc and parsed.netloc != "karmaitems.com":
        return value
    path = parsed.path if parsed.scheme else value
    if not path:
        return "/"
    if path == "/":
        return "/"
    return path.rstrip("/")


def asset_url(conn: Any, original_or_local: str | None) -> str:
    """Return a local static asset path for an original URL when available."""

    if not original_or_local:
        return ""
    if original_or_local.startswith("/static/"):
        return original_or_local
    row = conn.execute("SELECT local_path FROM assets WHERE original_url = ?", (original_or_local,)).fetchone()
    if row:
        return row["local_path"]
    return original_or_local


def enrich_products(conn: Any, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach local image paths to product records."""

    return [enrich_product(conn, product) for product in products]


def enrich_product(conn: Any, product: dict[str, Any]) -> dict[str, Any]:
    """Attach local image paths and route path to one product."""

    product = dict(product)
    images = product.get("images") or []
    product["local_images"] = [asset_url(conn, image) for image in images]
    product["primary_image"] = product["local_images"][0] if product["local_images"] else ""
    product["local_path"] = f"/products/{product['slug']}"
    return product


def enrich_categories(conn: Any, categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach local routes and images to categories."""

    enriched: list[dict[str, Any]] = []
    for category in categories:
        item = dict(category)
        metadata = item.get("metadata") or {}
        item["local_path"] = f"/collections/{item['slug']}"
        item["image"] = asset_url(conn, metadata.get("image") or metadata.get("image_url"))
        enriched.append(item)
    return enriched


def category_by_slug(conn: Any, slug: str) -> dict[str, Any] | None:
    """Fetch and enrich a category by slug."""

    row = conn.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()
    if not row:
        return None
    category = {key: row[key] for key in row.keys()}
    import json

    category["metadata"] = json.loads(category.pop("metadata_json") or "{}")
    return enrich_categories(conn, [category])[0]


def build_nav_tree(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert flat navigation rows into a small parent/child tree."""

    parents: dict[str, dict[str, Any]] = {}
    roots: list[dict[str, Any]] = []
    for item in items:
        entry = {
            "label": item["label"],
            "url": item["url"],
            "local_path": local_url(item["local_path"] or item["url"]),
            "children": [],
        }
        parent_label = item.get("parent_label")
        if parent_label and parent_label in parents:
            parents[parent_label]["children"].append(entry)
        else:
            roots.append(entry)
            parents.setdefault(entry["label"], entry)
    return roots


def build_arg_parser() -> argparse.ArgumentParser:
    """Create command-line parser for serving and scraping."""

    parser = argparse.ArgumentParser(description="Serve or scrape the local Karma Items clone.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path.")
    parser.add_argument("--host", default="127.0.0.1", help="Flask host.")
    parser.add_argument("--port", type=int, default=5000, help="Flask port.")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")
    subparsers = parser.add_subparsers(dest="command")
    scrape_parser = subparsers.add_parser("scrape", help="Scrape Karma Items before serving.")
    scrape_parser.add_argument("--max-pages", type=int, default=200, help="Maximum number of pages to crawl.")
    scrape_parser.add_argument("--headed", action="store_true", help="Run Chromium with a visible browser.")
    scrape_parser.add_argument("urls", nargs="*", default=list(TARGET_URLS.values()), help="Optional seed URLs.")
    return parser


def main() -> None:
    """CLI entrypoint."""

    args = build_arg_parser().parse_args()
    if args.command == "scrape":
        crawler = KarmaItemsCrawler(db_path=args.db, max_pages=args.max_pages, headless=not args.headed)
        crawler.crawl(args.urls or None)
        return
    app = create_app(args.db)
    app.run(host=args.host, port=args.port, debug=args.debug)


app = create_app()


if __name__ == "__main__":
    main()
