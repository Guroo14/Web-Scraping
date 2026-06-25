"""Design and structure extraction helpers."""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

import cssutils
from bs4 import BeautifulSoup

from scraper.parser import absolute_url, css_urls


LOGGER = logging.getLogger(__name__)
CSS_PROPERTIES = {
    "color": "color",
    "background": "color",
    "background-color": "color",
    "font-family": "typography",
    "font-size": "typography",
    "font-weight": "typography",
    "line-height": "typography",
    "letter-spacing": "typography",
    "margin": "spacing",
    "margin-top": "spacing",
    "margin-right": "spacing",
    "margin-bottom": "spacing",
    "margin-left": "spacing",
    "padding": "spacing",
    "padding-top": "spacing",
    "padding-right": "spacing",
    "padding-bottom": "spacing",
    "padding-left": "spacing",
    "border": "border",
    "border-color": "border",
    "border-radius": "border",
    "box-shadow": "shadow",
    "text-shadow": "shadow",
    "display": "layout",
    "grid-template-columns": "layout",
    "grid-template-rows": "layout",
    "gap": "layout",
    "column-gap": "layout",
    "row-gap": "layout",
    "justify-content": "layout",
    "align-items": "layout",
    "flex-direction": "layout",
}


def token_type(property_name: str) -> str:
    """Return a normalized token type for a CSS property."""

    return CSS_PROPERTIES.get(property_name.lower(), "style")


def compact_selector(selector: str) -> str:
    """Normalize selectors before storage."""

    selector = re.sub(r"\s+", " ", selector.strip())
    return selector[:500]


class DesignExtractor:
    """Extract design tokens from HTML, CSS, and browser-computed styles."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def extract_from_html(self, html: str) -> list[dict[str, Any]]:
        """Extract inline style and embedded stylesheet tokens."""

        soup = BeautifulSoup(html, "html.parser")
        tokens: list[dict[str, Any]] = []
        tokens.extend(self._extract_inline_styles(soup))
        for style in soup.find_all("style"):
            tokens.extend(self.extract_from_css(style.get_text(), source_url=self.base_url))
        tokens.extend(self._summarize_structure(soup))
        return self._dedupe_tokens(tokens)

    def extract_from_css(self, css_text: str, source_url: str | None = None) -> list[dict[str, Any]]:
        """Extract selected design properties from CSS text using cssutils."""

        cssutils.log.setLevel(logging.CRITICAL)
        tokens: list[dict[str, Any]] = []
        if not css_text.strip():
            return tokens
        try:
            sheet = cssutils.parseString(css_text)
        except Exception as exc:  # cssutils can raise on severely malformed CSS.
            LOGGER.debug("Skipping malformed CSS from %s: %s", source_url, exc)
            return tokens
        for rule in sheet:
            if rule.type == rule.STYLE_RULE:
                selector = compact_selector(rule.selectorText)
                for property_name in CSS_PROPERTIES:
                    value = rule.style.getPropertyValue(property_name)
                    if value:
                        tokens.append(
                            {
                                "selector": selector,
                                "property": property_name,
                                "value": value.strip(),
                                "token_type": token_type(property_name),
                                "source_url": source_url,
                            }
                        )
            elif rule.type == rule.MEDIA_RULE:
                for nested in rule.cssRules:
                    if nested.type != nested.STYLE_RULE:
                        continue
                    selector = compact_selector(f"@media {rule.media.mediaText} {{ {nested.selectorText} }}")
                    for property_name in CSS_PROPERTIES:
                        value = nested.style.getPropertyValue(property_name)
                        if value:
                            tokens.append(
                                {
                                    "selector": selector,
                                    "property": property_name,
                                    "value": value.strip(),
                                    "token_type": token_type(property_name),
                                    "source_url": source_url,
                                }
                            )
        return self._dedupe_tokens(tokens)

    def extract_asset_urls_from_css(self, css_text: str, source_url: str) -> list[str]:
        """Return background/font URLs referenced by CSS."""

        return css_urls(css_text, source_url or self.base_url)

    def extract_from_computed_snapshot(self, snapshot: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Playwright-computed style snapshots into tokens."""

        tokens: list[dict[str, Any]] = []
        for item in snapshot:
            selector = item.get("selector") or item.get("tag") or "unknown"
            styles = item.get("styles") or {}
            for property_name, value in styles.items():
                if value and value != "none":
                    tokens.append(
                        {
                            "selector": selector,
                            "property": property_name,
                            "value": str(value),
                            "token_type": token_type(property_name),
                            "source_url": self.base_url,
                        }
                    )
            rect = item.get("rect") or {}
            for key in ("width", "height"):
                value = rect.get(key)
                if value:
                    tokens.append(
                        {
                            "selector": selector,
                            "property": key,
                            "value": f"{round(float(value), 2)}px",
                            "token_type": "layout",
                            "source_url": self.base_url,
                        }
                    )
        return self._dedupe_tokens(tokens)

    def _extract_inline_styles(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Extract inline style attributes."""

        tokens: list[dict[str, Any]] = []
        for tag in soup.find_all(style=True):
            selector = self._selector_for_tag(tag)
            declarations = str(tag.get("style")).split(";")
            for declaration in declarations:
                if ":" not in declaration:
                    continue
                property_name, value = declaration.split(":", 1)
                property_name = property_name.strip().lower()
                value = value.strip()
                if property_name and value:
                    tokens.append(
                        {
                            "selector": selector,
                            "property": property_name,
                            "value": value,
                            "token_type": token_type(property_name),
                            "source_url": self.base_url,
                        }
                    )
        return tokens

    def _selector_for_tag(self, tag: Any) -> str:
        """Create a readable selector for an element."""

        if tag.get("id"):
            return f"#{tag['id']}"
        classes = tag.get("class") or []
        if classes:
            return f"{tag.name}." + ".".join(classes[:3])
        return tag.name

    def _summarize_structure(self, soup: BeautifulSoup) -> list[dict[str, Any]]:
        """Store useful layout hints from class/id naming and tag distribution."""

        tokens: list[dict[str, Any]] = []
        class_counter: Counter[str] = Counter()
        for tag in soup.find_all(True):
            for class_name in tag.get("class") or []:
                class_counter[str(class_name)] += 1
        for class_name, count in class_counter.most_common(80):
            lowered = class_name.lower()
            if any(term in lowered for term in ("grid", "flex", "container", "row", "column", "card", "banner", "slider")):
                tokens.append(
                    {
                        "selector": f".{class_name}",
                        "property": "usage-count",
                        "value": str(count),
                        "token_type": "layout",
                        "source_url": self.base_url,
                    }
                )
        return tokens

    def _dedupe_tokens(self, tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove duplicate token rows while preserving order."""

        seen: set[tuple[str, str, str, str | None]] = set()
        deduped: list[dict[str, Any]] = []
        for token in tokens:
            key = (
                str(token.get("selector")),
                str(token.get("property")),
                str(token.get("value")),
                token.get("source_url"),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(token)
        return deduped


def css_link_urls(html: str, base_url: str) -> list[str]:
    """Extract stylesheet URLs from a page."""

    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        href = str(link.get("href"))
        if "stylesheet" in rel or href.lower().endswith(".css"):
            resolved = absolute_url(base_url, href)
            if resolved:
                urls.append(resolved)
    return urls
