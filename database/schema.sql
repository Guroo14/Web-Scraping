PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL,
    path TEXT NOT NULL,
    page_type TEXT NOT NULL DEFAULT 'page',
    title TEXT,
    html_path TEXT,
    local_html TEXT,
    text_content TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}',
    headers_json TEXT NOT NULL DEFAULT '[]',
    menus_json TEXT NOT NULL DEFAULT '[]',
    navigation_json TEXT NOT NULL DEFAULT '[]',
    breadcrumbs_json TEXT NOT NULL DEFAULT '[]',
    categories_json TEXT NOT NULL DEFAULT '[]',
    content_json TEXT NOT NULL DEFAULT '{}',
    footer_html TEXT,
    scraped_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pages_path ON pages(path);
CREATE INDEX IF NOT EXISTS idx_pages_type ON pages(page_type);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    parent_slug TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    image_asset_id INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(image_asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_url TEXT NOT NULL UNIQUE,
    local_path TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    mime_type TEXT,
    width INTEGER,
    height INTEGER,
    bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    source_page_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(asset_type);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER,
    product_url TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    price TEXT,
    compare_at_price TEXT,
    discount TEXT,
    rating TEXT,
    reviews_count TEXT,
    availability TEXT,
    image_asset_id INTEGER,
    images_json TEXT NOT NULL DEFAULT '[]',
    category_slug TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE SET NULL,
    FOREIGN KEY(image_asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_products_slug ON products(slug);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_slug);

CREATE TABLE IF NOT EXISTS design_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER,
    selector TEXT NOT NULL,
    property TEXT NOT NULL,
    value TEXT NOT NULL,
    token_type TEXT NOT NULL,
    source_url TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_design_tokens_page ON design_tokens(page_id);
CREATE INDEX IF NOT EXISTS idx_design_tokens_type ON design_tokens(token_type);

CREATE TABLE IF NOT EXISTS navigation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER,
    label TEXT NOT NULL,
    url TEXT NOT NULL,
    local_path TEXT NOT NULL,
    parent_label TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    nav_type TEXT NOT NULL DEFAULT 'header',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_navigation_type ON navigation(nav_type);

CREATE TABLE IF NOT EXISTS banners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER,
    title TEXT,
    subtitle TEXT,
    image_asset_id INTEGER,
    image_url TEXT,
    link_url TEXT,
    html TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE,
    FOREIGN KEY(image_asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_banners_page ON banners(page_id);
