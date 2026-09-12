"""Shared helpers for turning source-native HTML/URLs into clean text, and
for talking to rate-limited APIs."""

from __future__ import annotations

import html
import os
import re
import time
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from news.logging_setup import logger

_TAG_RE = re.compile(r"<[^>]+>")

# Repo root is src/news/sources/../../.. from this file.
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

CONTACT_EMAIL = os.environ.get("NEWS_CONTACT_EMAIL")
"""Optional contact address for APIs (Crossref, OpenAlex, ...) that offer a
faster/dedicated "polite pool" to keyless clients who identify themselves.
Read from `.env` (via `NEWS_CONTACT_EMAIL`, gitignored) rather than
committed config, since interests.yaml is tracked in git and this is
personal contact info, not a pipeline setting."""


def get_with_retry(
    client: httpx.Client, url: str, params: dict, max_retries: int = 3
) -> httpx.Response:
    """GET with retry-on-429. Honors a numeric `Retry-After` header when the
    API sends one (Crossref/OpenAlex both do); falls back to exponential
    backoff (1s, 2s, 4s, ...) otherwise. Raises via `raise_for_status()` on
    the final attempt's response, same as a plain `client.get()` caller
    would need to do themselves."""
    resp = client.get(url, params=params)
    for attempt in range(max_retries):
        if resp.status_code != 429:
            return resp
        retry_after = resp.headers.get("Retry-After")
        wait = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
        logger.warning("Rate-limited (429) by {}; retrying in {:.0f}s", urlparse(url).netloc, wait)
        time.sleep(wait)
        resp = client.get(url, params=params)
    return resp


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
