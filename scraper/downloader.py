"""Asset downloading and local path rewriting."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
from PIL import Image, UnidentifiedImageError

from database.db import upsert_asset
from scraper.parser import absolute_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PROJECT_ROOT / "static"
MAX_FILENAME_STEM_LENGTH = 56
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
}
URL_RE = re.compile(r"url\((['\"]?)(.*?)\1\)", flags=re.IGNORECASE)


@dataclass(slots=True)
class DownloadedAsset:
    """A locally downloaded asset."""

    original_url: str
    local_path: str
    asset_type: str
    mime_type: str | None
    width: int | None
    height: int | None
    bytes: int
    sha256: str
    source_page_url: str | None

    def as_dict(self) -> dict[str, Any]:
        """Return a database-ready dictionary."""

        return {
            "original_url": self.original_url,
            "local_path": self.local_path,
            "asset_type": self.asset_type,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "source_page_url": self.source_page_url,
        }


class AssetDownloader:
    """Download and register remote assets used by scraped pages."""

    def __init__(self, conn: Any | None = None, static_root: Path = STATIC_ROOT) -> None:
        self.conn = conn
        self.static_root = static_root
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.url_map: dict[str, str] = {}

    def download_many(self, urls: list[str], source_page_url: str | None = None) -> dict[str, str]:
        """Download URLs and return an original-to-local URL map."""

        queue = list(dict.fromkeys(urls))
        index = 0
        while index < len(queue):
            url = queue[index]
            index += 1
            asset = self.download(url, source_page_url)
            if not asset:
                continue
            if asset.asset_type == "css":
                css_path = PROJECT_ROOT / asset.local_path.lstrip("/")
                discovered = self.rewrite_css_dependencies(css_path, asset.original_url, source_page_url)
                for discovered_url in discovered:
                    if discovered_url not in self.url_map and discovered_url not in queue:
                        queue.append(discovered_url)
        return self.url_map

    def download(self, url: str, source_page_url: str | None = None) -> DownloadedAsset | None:
        """Download one asset if possible."""

        normalized = self.normalize_url(url, source_page_url)
        if not normalized:
            return None
        if normalized in self.url_map:
            return None
        try:
            response = self.session.get(normalized, timeout=30)
            response.raise_for_status()
        except requests.RequestException:
            return None

        content = response.content
        if not content:
            return None
        mime_type = response.headers.get("Content-Type", "").split(";")[0] or mimetypes.guess_type(normalized)[0]
        asset_type = self.asset_type_for(normalized, mime_type)
        sha256 = hashlib.sha256(content).hexdigest()
        extension = self.extension_for(normalized, mime_type, asset_type)
        local_relative = self.local_relative_path(normalized, sha256, extension, asset_type)
        local_file = PROJECT_ROOT / local_relative
        local_file.parent.mkdir(parents=True, exist_ok=True)
        local_file.write_bytes(content)

        width, height = self.image_dimensions(content, asset_type)
        local_path = "/" + local_relative.as_posix()
        asset = DownloadedAsset(
            original_url=normalized,
            local_path=local_path,
            asset_type=asset_type,
            mime_type=mime_type,
            width=width,
            height=height,
            bytes=len(content),
            sha256=sha256,
            source_page_url=source_page_url,
        )
        self.url_map[normalized] = local_path
        if self.conn is not None:
            upsert_asset(self.conn, asset.as_dict())
        return asset

    def rewrite_html(self, html: str, url_map: dict[str, str] | None = None) -> str:
        """Rewrite original asset URLs in HTML to local static URLs."""

        rewritten = html
        for original, local in (url_map or self.url_map).items():
            rewritten = rewritten.replace(original, local)
            parsed = urlparse(original)
            if parsed.scheme:
                rewritten = rewritten.replace(f"//{parsed.netloc}{parsed.path}", local)
        return rewritten

    def rewrite_css_dependencies(self, css_path: Path, css_url: str, source_page_url: str | None) -> list[str]:
        """Download CSS dependencies and rewrite the local CSS file."""

        if not css_path.exists():
            return []
        css_text = css_path.read_text(encoding="utf-8", errors="ignore")
        discovered: list[str] = []

        def replace(match: re.Match[str]) -> str:
            raw = match.group(2)
            resolved = absolute_url(css_url, raw)
            if not resolved:
                return match.group(0)
            discovered.append(resolved)
            asset = self.download(resolved, source_page_url)
            if asset:
                return f"url('{asset.local_path}')"
            return match.group(0)

        rewritten = URL_RE.sub(replace, css_text)
        if rewritten != css_text:
            css_path.write_text(rewritten, encoding="utf-8")
        return discovered

    def normalize_url(self, url: str, base_url: str | None = None) -> str | None:
        """Normalize asset URL values."""

        resolved = absolute_url(base_url or "", url) if base_url else urljoin("", url)
        if not resolved:
            return None
        parsed = urlparse(resolved)
        if parsed.scheme not in {"http", "https"}:
            return None
        return parsed._replace(fragment="").geturl()

    def asset_type_for(self, url: str, mime_type: str | None) -> str:
        """Infer a broad asset type."""

        path = urlparse(url).path.lower()
        mime = (mime_type or "").lower()
        if "image/svg" in mime or path.endswith(".svg"):
            return "svg"
        if mime.startswith("image/") or path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico", ".avif")):
            return "image"
        if "css" in mime or path.endswith(".css"):
            return "css"
        if "javascript" in mime or path.endswith(".js"):
            return "js"
        if "font" in mime or path.endswith((".woff", ".woff2", ".ttf", ".otf", ".eot")):
            return "font"
        return "asset"

    def extension_for(self, url: str, mime_type: str | None, asset_type: str) -> str:
        """Choose a file extension."""

        path = unquote(urlparse(url).path)
        suffix = Path(path).suffix
        if suffix and len(suffix) <= 8:
            return suffix
        guessed = mimetypes.guess_extension(mime_type or "")
        if guessed:
            return guessed
        return {
            "css": ".css",
            "js": ".js",
            "svg": ".svg",
            "image": ".img",
            "font": ".woff2",
        }.get(asset_type, ".bin")

    def local_relative_path(self, url: str, sha256: str, extension: str, asset_type: str) -> Path:
        """Build a deterministic local path under static/."""

        basename = Path(unquote(urlparse(url).path)).stem
        safe_name = self.safe_filename_stem(basename)
        filename = f"{safe_name}-{sha256[:12]}{extension}"
        folder = {
            "css": "css/downloaded",
            "js": "js/downloaded",
            "font": "fonts",
            "svg": "images",
            "image": "images",
        }.get(asset_type, "assets")
        return Path("static") / folder / filename

    def safe_filename_stem(self, value: str) -> str:
        """Return a short, Windows-safe filename stem for downloaded assets."""

        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-. ") or "asset"
        if len(safe_name) > MAX_FILENAME_STEM_LENGTH:
            safe_name = safe_name[:MAX_FILENAME_STEM_LENGTH].rstrip("-. ")
        if safe_name.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
            safe_name = f"asset-{safe_name.lower()}"
        return safe_name or "asset"

    def image_dimensions(self, content: bytes, asset_type: str) -> tuple[int | None, int | None]:
        """Read image dimensions with Pillow when possible."""

        if asset_type not in {"image", "svg"}:
            return None, None
        if asset_type == "svg":
            text = content.decode("utf-8", errors="ignore")
            width = self.svg_dimension(text, "width")
            height = self.svg_dimension(text, "height")
            return width, height
        try:
            with Image.open(BytesIO(content)) as image:
                return image.width, image.height
        except (UnidentifiedImageError, OSError):
            return None, None

    def svg_dimension(self, text: str, attribute: str) -> int | None:
        """Extract a numeric SVG width or height."""

        match = re.search(fr'{attribute}=["\']?(\d+(?:\.\d+)?)', text)
        if not match:
            return None
        return int(float(match.group(1)))
