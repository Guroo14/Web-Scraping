"""Application configuration for the Karma Items catalog clone."""

from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("KARMA_DATA_DIR", BASE_DIR / "data"))
EXPORT_DIR = Path(os.environ.get("KARMA_EXPORT_DIR", DATA_DIR / "exports"))
DATABASE_PATH = Path(os.environ.get("KARMA_DATABASE", DATA_DIR / "catalog.db"))

TARGET_SITE = os.environ.get("KARMA_TARGET_SITE", "https://karmaitems.com/")
USER_AGENT = os.environ.get(
    "KARMA_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 KarmaCatalogBot/1.0",
)

CRAWLER_MAX_PAGES = int(os.environ.get("KARMA_CRAWLER_MAX_PAGES", "250"))
CRAWLER_MAX_DEPTH = int(os.environ.get("KARMA_CRAWLER_MAX_DEPTH", "5"))
CRAWLER_CONCURRENCY = int(os.environ.get("KARMA_CRAWLER_CONCURRENCY", "3"))
CRAWLER_DELAY_SECONDS = float(os.environ.get("KARMA_CRAWLER_DELAY_SECONDS", "0.35"))
REQUEST_TIMEOUT_MS = int(os.environ.get("KARMA_REQUEST_TIMEOUT_MS", "30000"))

PRODUCTS_PER_PAGE = int(os.environ.get("KARMA_PRODUCTS_PER_PAGE", "24"))
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")


def ensure_directories() -> None:
    """Create runtime data directories when the app or crawler starts."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
