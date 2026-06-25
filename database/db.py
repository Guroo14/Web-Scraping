"""SQLite persistence helpers for the Karma Items clone project."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "site.db"
SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"


def dumps(value: Any) -> str:
    """Serialize structured values consistently for SQLite text columns."""

    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def loads(value: str | None, default: Any) -> Any:
    """Deserialize JSON columns while tolerating empty values."""

    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a configured SQLite connection."""

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Create the database schema if it does not already exist."""

    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row into a normal dictionary."""

    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def local_path_for_url(url: str) -> str:
    """Map an original Karma Items URL/path to a local Flask path."""

    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path or "/"
    if path == "/":
        return "/"
    return path.rstrip("/")


def page_type_for_path(path: str) -> str:
    """Classify a page path for template selection."""

    if path == "/":
        return "home"
    if path.startswith("/collections/"):
        return "category"
    if path.startswith("/products/"):
        return "product"
    return "page"


def upsert_page(conn: sqlite3.Connection, page: dict[str, Any]) -> int:
    """Insert or update one scraped page and return its id."""

    conn.execute(
        """
        INSERT INTO pages (
            url, slug, path, page_type, title, html_path, local_html, text_content,
            meta_json, headers_json, menus_json, navigation_json, breadcrumbs_json,
            categories_json, content_json, footer_html, scraped_at
        )
        VALUES (
            :url, :slug, :path, :page_type, :title, :html_path, :local_html, :text_content,
            :meta_json, :headers_json, :menus_json, :navigation_json, :breadcrumbs_json,
            :categories_json, :content_json, :footer_html, CURRENT_TIMESTAMP
        )
        ON CONFLICT(url) DO UPDATE SET
            slug = excluded.slug,
            path = excluded.path,
            page_type = excluded.page_type,
            title = excluded.title,
            html_path = excluded.html_path,
            local_html = excluded.local_html,
            text_content = excluded.text_content,
            meta_json = excluded.meta_json,
            headers_json = excluded.headers_json,
            menus_json = excluded.menus_json,
            navigation_json = excluded.navigation_json,
            breadcrumbs_json = excluded.breadcrumbs_json,
            categories_json = excluded.categories_json,
            content_json = excluded.content_json,
            footer_html = excluded.footer_html,
            scraped_at = CURRENT_TIMESTAMP
        """,
        {
            "url": page["url"],
            "slug": page["slug"],
            "path": page["path"],
            "page_type": page.get("page_type", page_type_for_path(page["path"])),
            "title": page.get("title"),
            "html_path": page.get("html_path"),
            "local_html": page.get("local_html"),
            "text_content": page.get("text_content"),
            "meta_json": dumps(page.get("meta")),
            "headers_json": dumps(page.get("headers", [])),
            "menus_json": dumps(page.get("menus", [])),
            "navigation_json": dumps(page.get("navigation", [])),
            "breadcrumbs_json": dumps(page.get("breadcrumbs", [])),
            "categories_json": dumps(page.get("categories", [])),
            "content_json": dumps(page.get("content", {})),
            "footer_html": page.get("footer_html"),
        },
    )
    row = conn.execute("SELECT id FROM pages WHERE url = ?", (page["url"],)).fetchone()
    return int(row["id"])


def upsert_asset(conn: sqlite3.Connection, asset: dict[str, Any]) -> int:
    """Insert or update a downloaded asset and return its id."""

    conn.execute(
        """
        INSERT INTO assets (
            original_url, local_path, asset_type, mime_type, width, height, bytes,
            sha256, source_page_url
        )
        VALUES (
            :original_url, :local_path, :asset_type, :mime_type, :width, :height,
            :bytes, :sha256, :source_page_url
        )
        ON CONFLICT(original_url) DO UPDATE SET
            local_path = excluded.local_path,
            asset_type = excluded.asset_type,
            mime_type = excluded.mime_type,
            width = excluded.width,
            height = excluded.height,
            bytes = excluded.bytes,
            sha256 = excluded.sha256,
            source_page_url = excluded.source_page_url
        """,
        asset,
    )
    row = conn.execute("SELECT id FROM assets WHERE original_url = ?", (asset["original_url"],)).fetchone()
    return int(row["id"])


def upsert_category(conn: sqlite3.Connection, category: dict[str, Any]) -> int:
    """Insert or update one category/collection."""

    conn.execute(
        """
        INSERT INTO categories (name, slug, url, parent_slug, position, image_asset_id, metadata_json)
        VALUES (:name, :slug, :url, :parent_slug, :position, :image_asset_id, :metadata_json)
        ON CONFLICT(slug) DO UPDATE SET
            name = excluded.name,
            url = excluded.url,
            parent_slug = excluded.parent_slug,
            position = excluded.position,
            image_asset_id = excluded.image_asset_id,
            metadata_json = excluded.metadata_json
        """,
        {
            "name": category["name"],
            "slug": category["slug"],
            "url": category["url"],
            "parent_slug": category.get("parent_slug"),
            "position": category.get("position", 0),
            "image_asset_id": category.get("image_asset_id"),
            "metadata_json": dumps(category.get("metadata", {})),
        },
    )
    row = conn.execute("SELECT id FROM categories WHERE slug = ?", (category["slug"],)).fetchone()
    return int(row["id"])


def upsert_product(conn: sqlite3.Connection, product: dict[str, Any]) -> int:
    """Insert or update one product."""

    conn.execute(
        """
        INSERT INTO products (
            page_id, product_url, slug, name, description, price, compare_at_price,
            discount, rating, reviews_count, availability, image_asset_id, images_json,
            category_slug, position, raw_json
        )
        VALUES (
            :page_id, :product_url, :slug, :name, :description, :price,
            :compare_at_price, :discount, :rating, :reviews_count, :availability,
            :image_asset_id, :images_json, :category_slug, :position, :raw_json
        )
        ON CONFLICT(product_url) DO UPDATE SET
            page_id = COALESCE(excluded.page_id, products.page_id),
            slug = excluded.slug,
            name = excluded.name,
            description = COALESCE(excluded.description, products.description),
            price = COALESCE(excluded.price, products.price),
            compare_at_price = COALESCE(excluded.compare_at_price, products.compare_at_price),
            discount = COALESCE(excluded.discount, products.discount),
            rating = COALESCE(excluded.rating, products.rating),
            reviews_count = COALESCE(excluded.reviews_count, products.reviews_count),
            availability = COALESCE(excluded.availability, products.availability),
            image_asset_id = COALESCE(excluded.image_asset_id, products.image_asset_id),
            images_json = excluded.images_json,
            category_slug = COALESCE(excluded.category_slug, products.category_slug),
            position = excluded.position,
            raw_json = excluded.raw_json
        """,
        {
            "page_id": product.get("page_id"),
            "product_url": product["product_url"],
            "slug": product["slug"],
            "name": product["name"],
            "description": product.get("description"),
            "price": product.get("price"),
            "compare_at_price": product.get("compare_at_price"),
            "discount": product.get("discount"),
            "rating": product.get("rating"),
            "reviews_count": product.get("reviews_count"),
            "availability": product.get("availability"),
            "image_asset_id": product.get("image_asset_id"),
            "images_json": dumps(product.get("images", [])),
            "category_slug": product.get("category_slug"),
            "position": product.get("position", 0),
            "raw_json": dumps(product.get("raw", {})),
        },
    )
    row = conn.execute("SELECT id FROM products WHERE product_url = ?", (product["product_url"],)).fetchone()
    return int(row["id"])


def replace_navigation(conn: sqlite3.Connection, page_id: int, items: Iterable[dict[str, Any]]) -> None:
    """Replace navigation rows for a page."""

    conn.execute("DELETE FROM navigation WHERE page_id = ?", (page_id,))
    conn.executemany(
        """
        INSERT INTO navigation (page_id, label, url, local_path, parent_label, position, nav_type)
        VALUES (:page_id, :label, :url, :local_path, :parent_label, :position, :nav_type)
        """,
        [
            {
                "page_id": page_id,
                "label": item.get("label") or item.get("text") or "Untitled",
                "url": item.get("url") or "#",
                "local_path": item.get("local_path") or local_path_for_url(item.get("url") or "#"),
                "parent_label": item.get("parent_label"),
                "position": item.get("position", index),
                "nav_type": item.get("nav_type", "header"),
            }
            for index, item in enumerate(items)
            if item.get("label") or item.get("text")
        ],
    )


def replace_banners(conn: sqlite3.Connection, page_id: int, banners: Iterable[dict[str, Any]]) -> None:
    """Replace banner rows for a page."""

    conn.execute("DELETE FROM banners WHERE page_id = ?", (page_id,))
    conn.executemany(
        """
        INSERT INTO banners (
            page_id, title, subtitle, image_asset_id, image_url, link_url, html, position, metadata_json
        )
        VALUES (
            :page_id, :title, :subtitle, :image_asset_id, :image_url, :link_url,
            :html, :position, :metadata_json
        )
        """,
        [
            {
                "page_id": page_id,
                "title": banner.get("title"),
                "subtitle": banner.get("subtitle"),
                "image_asset_id": banner.get("image_asset_id"),
                "image_url": banner.get("image_url"),
                "link_url": banner.get("link_url"),
                "html": banner.get("html"),
                "position": banner.get("position", index),
                "metadata_json": dumps(banner.get("metadata", {})),
            }
            for index, banner in enumerate(banners)
        ],
    )


def replace_design_tokens(conn: sqlite3.Connection, page_id: int, tokens: Iterable[dict[str, Any]]) -> None:
    """Replace analyzed design tokens for a page."""

    conn.execute("DELETE FROM design_tokens WHERE page_id = ?", (page_id,))
    conn.executemany(
        """
        INSERT INTO design_tokens (page_id, selector, property, value, token_type, source_url)
        VALUES (:page_id, :selector, :property, :value, :token_type, :source_url)
        """,
        [
            {
                "page_id": page_id,
                "selector": token["selector"],
                "property": token["property"],
                "value": token["value"],
                "token_type": token.get("token_type", "style"),
                "source_url": token.get("source_url"),
            }
            for token in tokens
            if token.get("selector") and token.get("property") and token.get("value")
        ],
    )


def get_page_by_path(conn: sqlite3.Connection, path: str) -> dict[str, Any] | None:
    """Fetch a page and expand JSON fields."""

    normalized = "/" + path.strip("/") if path != "/" else "/"
    row = conn.execute("SELECT * FROM pages WHERE path = ?", (normalized,)).fetchone()
    page = row_to_dict(row)
    return expand_page(page) if page else None


def get_product_by_slug(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    """Fetch one product and expand its JSON fields."""

    row = conn.execute("SELECT * FROM products WHERE slug = ?", (slug,)).fetchone()
    product = row_to_dict(row)
    if not product:
        return None
    product["images"] = loads(product.pop("images_json", None), [])
    product["raw"] = loads(product.pop("raw_json", None), {})
    return product


def list_products(conn: sqlite3.Connection, category_slug: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    """List products, optionally filtered by category."""

    sql = "SELECT * FROM products"
    params: list[Any] = []
    if category_slug:
        sql += " WHERE category_slug = ?"
        params.append(category_slug)
    sql += " ORDER BY category_slug, position, name"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    products: list[dict[str, Any]] = []
    for row in rows:
        product = row_to_dict(row) or {}
        product["images"] = loads(product.pop("images_json", None), [])
        product["raw"] = loads(product.pop("raw_json", None), {})
        products.append(product)
    return products


def list_categories(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return categories in their scraped order."""

    rows = conn.execute("SELECT * FROM categories ORDER BY position, name").fetchall()
    categories: list[dict[str, Any]] = []
    for row in rows:
        category = row_to_dict(row) or {}
        category["metadata"] = loads(category.pop("metadata_json", None), {})
        categories.append(category)
    return categories


def list_navigation(conn: sqlite3.Connection, nav_type: str = "header") -> list[dict[str, Any]]:
    """Return de-duplicated navigation items of one type."""

    rows = conn.execute(
        """
        SELECT label, url, local_path, parent_label, MIN(position) AS position, nav_type
        FROM navigation
        WHERE nav_type = ?
        GROUP BY label, url, local_path, parent_label, nav_type
        ORDER BY position, label
        """,
        (nav_type,),
    ).fetchall()
    return [row_to_dict(row) or {} for row in rows]


def list_banners(conn: sqlite3.Connection, page_id: int) -> list[dict[str, Any]]:
    """Return banners for a page."""

    rows = conn.execute("SELECT * FROM banners WHERE page_id = ? ORDER BY position", (page_id,)).fetchall()
    banners: list[dict[str, Any]] = []
    for row in rows:
        banner = row_to_dict(row) or {}
        banner["metadata"] = loads(banner.pop("metadata_json", None), {})
        banners.append(banner)
    return banners


def expand_page(page: dict[str, Any]) -> dict[str, Any]:
    """Expand serialized page fields for templates."""

    for key, default in {
        "meta_json": {},
        "headers_json": [],
        "menus_json": [],
        "navigation_json": [],
        "breadcrumbs_json": [],
        "categories_json": [],
        "content_json": {},
    }.items():
        page[key.removesuffix("_json")] = loads(page.pop(key, None), default)
    return page
