"""Shared helpers for turning source-native HTML/URLs into clean text."""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

_TAG_RE = re.compile(r"<[^>]+>")


def clean_html(text: str | None) -> str | None:
    if not text:
        return None
    stripped = _TAG_RE.sub(" ", text)
    return html.unescape(stripped).strip() or None


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    netloc = urlparse(url).netloc
    return netloc.removeprefix("www.") or None
