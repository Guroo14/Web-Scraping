"""Flask web application for the crawled Karma Items catalog."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from config import DATABASE_PATH, PRODUCTS_PER_PAGE, SECRET_KEY, TARGET_SITE, ensure_directories
from crawler import crawl_site
from database import (
    export_catalog,
    get_category_ancestors,
    get_category_by_slug,
    get_category_children,
    get_menu_tree,
    get_price_bounds,
    get_product_by_slug,
    get_products,
    get_related_products,
    get_stats,
    init_db,
)
from utils import parse_price


def create_app() -> Flask:
    ensure_directories()
    init_db()

    app = Flask(__name__)
    app.config.update(SECRET_KEY=SECRET_KEY, DATABASE=str(DATABASE_PATH), TARGET_SITE=TARGET_SITE)

    @app.context_processor
    def inject_catalog_context() -> dict[str, Any]:
        return {
            "menu_tree": get_menu_tree(),
            "catalog_stats": get_stats(),
            "price_bounds": get_price_bounds(),
            "target_site": TARGET_SITE,
        }

    @app.template_filter("money")
    def money(value: Any, currency: str = "USD") -> str:
        if value is None or value == "":
            return "Price unavailable"
        symbol = {"USD": "$", "EUR": "EUR ", "GBP": "GBP "}.get(currency or "USD", f"{currency} ")
        return f"{symbol}{float(value):,.2f}"

    @app.route("/")
    def index() -> str:
        products = get_products(sort="newest", page=1, per_page=PRODUCTS_PER_PAGE)
        root_categories = get_category_children()
        return render_template("index.html", products=products, root_categories=root_categories)

    @app.route("/category/<path:slug>")
    def category(slug: str) -> str:
        category_record = get_category_by_slug(slug)
        if not category_record:
            abort(404)
        filters = _filters_from_request()
        products = get_products(category_slug=slug, **filters)
        children = get_category_children(category_record["id"])
        breadcrumbs = _category_breadcrumbs(category_record)
        return render_template(
            "category.html",
            category=category_record,
            products=products,
            children=children,
            breadcrumbs=breadcrumbs,
            active_filters=filters,
        )

    @app.route("/collections/<path:slug>")
    def collection_alias(slug: str) -> Any:
        return redirect(url_for("category", slug=slug), code=301)

    @app.route("/product/<slug>")
    def product(slug: str) -> str:
        product_record = get_product_by_slug(slug)
        if not product_record:
            abort(404)
        related = get_related_products(product_record["id"])
        breadcrumbs = _product_breadcrumbs(product_record)
        return render_template(
            "product.html",
            product=product_record,
            related_products=related,
            breadcrumbs=breadcrumbs,
        )

    @app.route("/products/<slug>")
    def product_alias(slug: str) -> Any:
        return redirect(url_for("product", slug=slug), code=301)

    @app.route("/search")
    def search() -> str:
        filters = _filters_from_request()
        products = get_products(**filters)
        return render_template("search.html", products=products, active_filters=filters)

    @app.route("/api/search")
    def api_search() -> Any:
        filters = _filters_from_request()
        products = get_products(**filters)
        return jsonify(
            {
                "total": products["total"],
                "page": products["page"],
                "pages": products["pages"],
                "items": [
                    {
                        "title": product["title"],
                        "slug": product["slug"],
                        "url": url_for("product", slug=product["slug"]),
                        "price": product["price"],
                        "currency": product["currency"],
                        "image": product["primary_image"],
                    }
                    for product in products["items"]
                ],
            }
        )

    @app.route("/exports/<name>")
    def download_export(name: str) -> Any:
        exports = export_catalog()
        key = {
            "catalog.json": "json",
            "products.csv": "products_csv",
            "categories.csv": "categories_csv",
        }.get(name)
        if not key:
            abort(404)
        return send_file(exports[key], as_attachment=True)

    @app.route("/admin/crawl", methods=["POST"])
    def admin_crawl() -> Any:
        max_pages = int(request.form.get("max_pages") or 100)
        stats = asyncio.run(crawl_site(max_pages=max_pages))
        flash(
            "Crawl complete: "
            f"{stats.get('pages_crawled', 0)} pages, "
            f"{stats.get('products_from_json', 0)} JSON products indexed.",
            "success",
        )
        return redirect(url_for("index"))

    @app.cli.command("init-db")
    def init_db_command() -> None:
        init_db()
        print(f"Initialized database at {DATABASE_PATH}")

    @app.cli.command("crawl")
    def crawl_command() -> None:
        stats = asyncio.run(crawl_site())
        print(stats)

    @app.cli.command("export")
    def export_command() -> None:
        print(export_catalog())

    return app


def _filters_from_request() -> dict[str, Any]:
    return {
        "query": request.args.get("q", "").strip(),
        "min_price": parse_price(request.args.get("min_price")),
        "max_price": parse_price(request.args.get("max_price")),
        "sort": request.args.get("sort", "featured"),
        "page": max(int(request.args.get("page", "1") or 1), 1),
        "per_page": PRODUCTS_PER_PAGE,
    }


def _category_breadcrumbs(category: dict[str, Any]) -> list[dict[str, str]]:
    ancestors = get_category_ancestors(category["id"])
    return [{"title": item["name"], "url": url_for("category", slug=item["slug"])} for item in ancestors]


def _product_breadcrumbs(product: dict[str, Any]) -> list[dict[str, str]]:
    categories = product.get("categories") or []
    if categories:
        breadcrumbs = _category_breadcrumbs(categories[0])
    else:
        breadcrumbs = []
    breadcrumbs.append({"title": product["title"], "url": url_for("product", slug=product["slug"])})
    return breadcrumbs


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
