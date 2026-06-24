"""SQLite persistence for crawled categories, products, and pages."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from config import DATABASE_PATH, EXPORT_DIR, ensure_directories
from utils import json_dumps, json_loads, slugify, utc_now_iso, write_csv, write_json


def get_connection(db_path: Path | str = DATABASE_PATH) -> sqlite3.Connection:
    ensure_directories()
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(db_path: Path | str = DATABASE_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                url TEXT UNIQUE,
                parent_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                description TEXT,
                image_url TEXT,
                path TEXT,
                depth INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                url TEXT UNIQUE,
                description TEXT,
                price REAL,
                compare_at_price REAL,
                currency TEXT DEFAULT 'USD',
                vendor TEXT,
                sku TEXT,
                availability TEXT,
                primary_image TEXT,
                source_path TEXT,
                metadata_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS product_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                image_url TEXT NOT NULL,
                alt_text TEXT,
                position INTEGER DEFAULT 0,
                UNIQUE(product_id, image_url)
            );

            CREATE TABLE IF NOT EXISTS product_categories (
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
                position INTEGER DEFAULT 0,
                PRIMARY KEY (product_id, category_id)
            );

            CREATE TABLE IF NOT EXISTS crawl_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                page_type TEXT,
                title TEXT,
                status_code INTEGER,
                depth INTEGER DEFAULT 0,
                discovered_from TEXT,
                metadata_json TEXT DEFAULT '{}',
                crawled_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);
            CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);
            CREATE INDEX IF NOT EXISTS idx_products_title ON products(title);
            CREATE INDEX IF NOT EXISTS idx_product_categories_category ON product_categories(category_id);
            """
        )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    if "metadata_json" in data:
        data["metadata"] = json_loads(data.pop("metadata_json"))
    return data


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def _category_parent_id(conn: sqlite3.Connection, parent_slug: str | None) -> int | None:
    if not parent_slug:
        return None
    row = conn.execute("SELECT id FROM categories WHERE slug = ?", (parent_slug,)).fetchone()
    return int(row["id"]) if row else None


def upsert_category(conn: sqlite3.Connection, category: dict[str, Any]) -> int:
    now = utc_now_iso()
    name = (category.get("name") or "Collection").strip()
    slug = category.get("slug") or slugify(name)
    parent_id = category.get("parent_id") or _category_parent_id(conn, category.get("parent_slug"))
    depth = int(category.get("depth") or 0)
    if parent_id:
        parent = conn.execute("SELECT path, depth FROM categories WHERE id = ?", (parent_id,)).fetchone()
        if parent:
            depth = int(parent["depth"]) + 1
    path = category.get("path") or slug

    conn.execute(
        """
        INSERT INTO categories (
            name, slug, url, parent_id, description, image_url, path, depth, sort_order,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            name = excluded.name,
            url = COALESCE(excluded.url, categories.url),
            parent_id = COALESCE(excluded.parent_id, categories.parent_id),
            description = COALESCE(NULLIF(excluded.description, ''), categories.description),
            image_url = COALESCE(NULLIF(excluded.image_url, ''), categories.image_url),
            path = COALESCE(NULLIF(excluded.path, ''), categories.path),
            depth = excluded.depth,
            sort_order = excluded.sort_order,
            updated_at = excluded.updated_at
        """,
        (
            name,
            slug,
            category.get("url"),
            parent_id,
            category.get("description", ""),
            category.get("image_url", ""),
            path,
            depth,
            int(category.get("sort_order") or 0),
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM categories WHERE slug = ?", (slug,)).fetchone()
    return int(row["id"])


def upsert_product(conn: sqlite3.Connection, product: dict[str, Any]) -> int:
    now = utc_now_iso()
    title = (product.get("title") or "Untitled product").strip()
    slug = product.get("slug") or slugify(title, fallback=product.get("url", "product"))
    images = [image for image in product.get("images", []) if image.get("url")]
    primary_image = product.get("primary_image") or (images[0]["url"] if images else "")

    conn.execute(
        """
        INSERT INTO products (
            title, slug, url, description, price, compare_at_price, currency, vendor, sku,
            availability, primary_image, source_path, metadata_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            title = excluded.title,
            url = COALESCE(excluded.url, products.url),
            description = COALESCE(NULLIF(excluded.description, ''), products.description),
            price = COALESCE(excluded.price, products.price),
            compare_at_price = COALESCE(excluded.compare_at_price, products.compare_at_price),
            currency = COALESCE(NULLIF(excluded.currency, ''), products.currency),
            vendor = COALESCE(NULLIF(excluded.vendor, ''), products.vendor),
            sku = COALESCE(NULLIF(excluded.sku, ''), products.sku),
            availability = COALESCE(NULLIF(excluded.availability, ''), products.availability),
            primary_image = COALESCE(NULLIF(excluded.primary_image, ''), products.primary_image),
            source_path = COALESCE(NULLIF(excluded.source_path, ''), products.source_path),
            metadata_json = excluded.metadata_json,
            updated_at = excluded.updated_at
        """,
        (
            title,
            slug,
            product.get("url"),
            product.get("description", ""),
            product.get("price"),
            product.get("compare_at_price"),
            product.get("currency", "USD"),
            product.get("vendor", ""),
            product.get("sku", ""),
            product.get("availability", ""),
            primary_image,
            product.get("source_path", ""),
            json_dumps(product.get("metadata", {})),
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM products WHERE slug = ?", (slug,)).fetchone()
    product_id = int(row["id"])

    for position, image in enumerate(images):
        conn.execute(
            """
            INSERT INTO product_images (product_id, image_url, alt_text, position)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(product_id, image_url) DO UPDATE SET
                alt_text = excluded.alt_text,
                position = excluded.position
            """,
            (product_id, image["url"], image.get("alt", title), position),
        )

    for position, category_slug in enumerate(product.get("category_slugs", [])):
        category_row = conn.execute("SELECT id FROM categories WHERE slug = ?", (category_slug,)).fetchone()
        if not category_row:
            continue
        conn.execute(
            """
            INSERT INTO product_categories (product_id, category_id, position)
            VALUES (?, ?, ?)
            ON CONFLICT(product_id, category_id) DO UPDATE SET position = excluded.position
            """,
            (product_id, int(category_row["id"]), position),
        )

    return product_id


def upsert_page(conn: sqlite3.Connection, page: dict[str, Any]) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO crawl_pages (
            url, page_type, title, status_code, depth, discovered_from, metadata_json, crawled_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            page_type = excluded.page_type,
            title = excluded.title,
            status_code = excluded.status_code,
            depth = excluded.depth,
            discovered_from = excluded.discovered_from,
            metadata_json = excluded.metadata_json,
            crawled_at = excluded.crawled_at
        """,
        (
            page.get("url"),
            page.get("page_type", "page"),
            page.get("title", ""),
            page.get("status_code", 200),
            int(page.get("depth") or 0),
            page.get("discovered_from", ""),
            json_dumps(page.get("metadata", {})),
            now,
        ),
    )


def ingest_parse_result(conn: sqlite3.Connection, result: dict[str, Any]) -> None:
    upsert_page(conn, result.get("page", {}))

    for category in result.get("categories", []):
        upsert_category(conn, category)

    for product in result.get("products", []):
        upsert_product(conn, product)


def get_category_by_slug(slug: str, db_path: Path | str = DATABASE_PATH) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()
        return row_to_dict(row)


def get_product_by_slug(slug: str, db_path: Path | str = DATABASE_PATH) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM products WHERE slug = ?", (slug,)).fetchone()
        product = row_to_dict(row)
        if not product:
            return None
        product["images"] = rows_to_dicts(
            conn.execute(
                "SELECT image_url AS url, alt_text AS alt, position FROM product_images "
                "WHERE product_id = ? ORDER BY position, id",
                (product["id"],),
            ).fetchall()
        )
        product["categories"] = rows_to_dicts(
            conn.execute(
                """
                SELECT c.* FROM categories c
                JOIN product_categories pc ON pc.category_id = c.id
                WHERE pc.product_id = ?
                ORDER BY pc.position, c.depth, c.name
                """,
                (product["id"],),
            ).fetchall()
        )
        return product


def _descendant_category_ids(conn: sqlite3.Connection, category_id: int) -> list[int]:
    rows = conn.execute(
        """
        WITH RECURSIVE tree(id) AS (
            SELECT id FROM categories WHERE id = ?
            UNION ALL
            SELECT c.id FROM categories c JOIN tree t ON c.parent_id = t.id
        )
        SELECT id FROM tree
        """,
        (category_id,),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def get_products(
    *,
    query: str = "",
    category_slug: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    sort: str = "featured",
    page: int = 1,
    per_page: int = 24,
    db_path: Path | str = DATABASE_PATH,
) -> dict[str, Any]:
    page = max(page, 1)
    per_page = max(min(per_page, 96), 1)
    where = ["1 = 1"]
    params: list[Any] = []

    with get_connection(db_path) as conn:
        category = None
        if category_slug:
            category = row_to_dict(
                conn.execute("SELECT * FROM categories WHERE slug = ?", (category_slug,)).fetchone()
            )
            if not category:
                return {"items": [], "total": 0, "page": page, "pages": 0, "category": None}
            category_ids = _descendant_category_ids(conn, int(category["id"]))
            placeholders = ",".join("?" for _ in category_ids)
            where.append(
                "p.id IN (SELECT product_id FROM product_categories WHERE category_id IN "
                f"({placeholders}))"
            )
            params.extend(category_ids)

        if query:
            like = f"%{query.strip()}%"
            where.append("(p.title LIKE ? OR p.description LIKE ? OR p.vendor LIKE ?)")
            params.extend([like, like, like])
        if min_price is not None:
            where.append("p.price >= ?")
            params.append(min_price)
        if max_price is not None:
            where.append("p.price <= ?")
            params.append(max_price)

        order_by = {
            "price-asc": "p.price IS NULL, p.price ASC, p.title ASC",
            "price-desc": "p.price IS NULL, p.price DESC, p.title ASC",
            "title-asc": "p.title ASC",
            "newest": "p.updated_at DESC",
        }.get(sort, "p.primary_image = '', p.updated_at DESC, p.title ASC")

        where_sql = " AND ".join(where)
        total = int(
            conn.execute(f"SELECT COUNT(DISTINCT p.id) AS count FROM products p WHERE {where_sql}", params).fetchone()[
                "count"
            ]
        )
        rows = conn.execute(
            f"""
            SELECT DISTINCT p.* FROM products p
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, (page - 1) * per_page],
        ).fetchall()

        return {
            "items": rows_to_dicts(rows),
            "total": total,
            "page": page,
            "pages": (total + per_page - 1) // per_page,
            "category": category,
        }


def get_category_children(category_id: int | None = None, db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        if category_id is None:
            rows = conn.execute(
                "SELECT * FROM categories WHERE parent_id IS NULL ORDER BY sort_order, name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM categories WHERE parent_id = ? ORDER BY sort_order, name",
                (category_id,),
            ).fetchall()
        return rows_to_dicts(rows)


def get_category_ancestors(category_id: int, db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            WITH RECURSIVE ancestors(id, name, slug, url, parent_id, description, image_url, path, depth, sort_order,
                                     created_at, updated_at) AS (
                SELECT id, name, slug, url, parent_id, description, image_url, path, depth, sort_order,
                       created_at, updated_at
                FROM categories WHERE id = ?
                UNION ALL
                SELECT c.id, c.name, c.slug, c.url, c.parent_id, c.description, c.image_url, c.path, c.depth,
                       c.sort_order, c.created_at, c.updated_at
                FROM categories c JOIN ancestors a ON a.parent_id = c.id
            )
            SELECT * FROM ancestors ORDER BY depth ASC
            """,
            (category_id,),
        ).fetchall()
        return rows_to_dicts(rows)


def get_menu_tree(db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = rows_to_dicts(conn.execute("SELECT * FROM categories ORDER BY depth, sort_order, name").fetchall())

    by_parent: dict[int | None, list[dict[str, Any]]] = {}
    for row in rows:
        row["children"] = []
        by_parent.setdefault(row.get("parent_id"), []).append(row)

    for row in rows:
        row["children"] = by_parent.get(row["id"], [])
    return by_parent.get(None, [])


def get_price_bounds(db_path: Path | str = DATABASE_PATH) -> dict[str, float | None]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT MIN(price) AS min_price, MAX(price) AS max_price FROM products").fetchone()
        return {"min": row["min_price"], "max": row["max_price"]}


def get_related_products(product_id: int, limit: int = 8, db_path: Path | str = DATABASE_PATH) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT p.* FROM products p
            JOIN product_categories pc ON pc.product_id = p.id
            WHERE pc.category_id IN (
                SELECT category_id FROM product_categories WHERE product_id = ?
            )
            AND p.id != ?
            ORDER BY p.primary_image = '', p.updated_at DESC
            LIMIT ?
            """,
            (product_id, product_id, limit),
        ).fetchall()
        if rows:
            return rows_to_dicts(rows)
        return rows_to_dicts(
            conn.execute(
                "SELECT * FROM products WHERE id != ? ORDER BY updated_at DESC LIMIT ?",
                (product_id, limit),
            ).fetchall()
        )


def get_stats(db_path: Path | str = DATABASE_PATH) -> dict[str, int]:
    with get_connection(db_path) as conn:
        return {
            "products": int(conn.execute("SELECT COUNT(*) AS count FROM products").fetchone()["count"]),
            "categories": int(conn.execute("SELECT COUNT(*) AS count FROM categories").fetchone()["count"]),
            "pages": int(conn.execute("SELECT COUNT(*) AS count FROM crawl_pages").fetchone()["count"]),
        }


def export_catalog(db_path: Path | str = DATABASE_PATH) -> dict[str, str]:
    with get_connection(db_path) as conn:
        categories = rows_to_dicts(conn.execute("SELECT * FROM categories ORDER BY depth, name").fetchall())
        products = rows_to_dicts(conn.execute("SELECT * FROM products ORDER BY title").fetchall())
        for product in products:
            product["images"] = rows_to_dicts(
                conn.execute(
                    "SELECT image_url AS url, alt_text AS alt, position FROM product_images "
                    "WHERE product_id = ? ORDER BY position, id",
                    (product["id"],),
                ).fetchall()
            )
            product["categories"] = rows_to_dicts(
                conn.execute(
                    """
                    SELECT c.slug, c.name FROM categories c
                    JOIN product_categories pc ON pc.category_id = c.id
                    WHERE pc.product_id = ?
                    ORDER BY pc.position, c.name
                    """,
                    (product["id"],),
                ).fetchall()
            )

    json_path = EXPORT_DIR / "catalog.json"
    products_csv_path = EXPORT_DIR / "products.csv"
    categories_csv_path = EXPORT_DIR / "categories.csv"

    write_json(json_path, {"categories": categories, "products": products})
    write_csv(
        products_csv_path,
        products,
        [
            "id",
            "title",
            "slug",
            "url",
            "price",
            "compare_at_price",
            "currency",
            "vendor",
            "sku",
            "availability",
            "primary_image",
            "description",
        ],
    )
    write_csv(categories_csv_path, categories, ["id", "name", "slug", "url", "parent_id", "path", "depth"])
    return {
        "json": str(json_path),
        "products_csv": str(products_csv_path),
        "categories_csv": str(categories_csv_path),
    }
