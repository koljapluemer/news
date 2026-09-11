"""HackerNews source, backed by the Algolia HN Search API.

API docs: https://hn.algolia.com/api

We fetch every item tagged "story" (i.e. all submissions, not just the
front page) created within the requested window. Comments are
deliberately not fetched here -- ranking runs on title/text first, and a
later stage can pull comments only for the handful of stories that make
the shortlist.

Note: the public endpoint hard-caps results at ~`hitsPerPage` per query --
`nbHits` can report a much larger true count while `page=1` simply returns
nothing (verified empirically: `nbPages` is not a reliable pagination
signal past the first page). So instead of paginating, a window whose
`nbHits` exceeds what was actually returned is bisected in time and each
half is fetched (and, if still over the cap, bisected again), which sidesteps
the cap by making each individual query small enough to fit in one page.
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from tqdm import tqdm

from news.logging_setup import logger
from news.models import RawItem

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
HITS_PER_PAGE = 1000
REQUEST_DELAY_SECONDS = 0.1
MAX_SPLIT_DEPTH = 8
MIN_SPLIT_INTERVAL = timedelta(minutes=1)

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html(text: str | None) -> str | None:
    if not text:
        return None
    stripped = _TAG_RE.sub(" ", text)
    return html.unescape(stripped).strip() or None


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    netloc = urlparse(url).netloc
    return netloc.removeprefix("www.") or None


class HackerNewsSource:
    name = "hackernews"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=30.0)

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]:
        logger.info(
            "Fetching HackerNews stories from {} to {}", window_start, window_end
        )

        pbar = tqdm(desc="Fetching HN stories", unit="req")
        try:
            items = self._fetch_window(window_start, window_end, pbar, depth=0)
        finally:
            pbar.close()

        deduped: dict[str, RawItem] = {item.id: item for item in items}
        logger.info(
            "Fetched {} HN stories in window ({} requests)", len(deduped), pbar.n
        )
        return list(deduped.values())

    def _fetch_window(
        self, start: datetime, end: datetime, pbar: tqdm, depth: int
    ) -> list[RawItem]:
        payload = self._request(start, end)
        pbar.update(1)
        time.sleep(REQUEST_DELAY_SECONDS)

        hits = payload.get("hits", [])
        nb_hits = payload.get("nbHits", len(hits))

        can_split = depth < MAX_SPLIT_DEPTH and (end - start) > MIN_SPLIT_INTERVAL
        if nb_hits > len(hits) and can_split:
            mid = start + (end - start) / 2
            left = self._fetch_window(start, mid, pbar, depth + 1)
            right = self._fetch_window(mid, end, pbar, depth + 1)
            return left + right

        if nb_hits > len(hits):
            logger.warning(
                "HN window {}..{} reports {} hits but only {} were reachable; "
                "results are truncated (hit MAX_SPLIT_DEPTH/MIN_SPLIT_INTERVAL)",
                start,
                end,
                nb_hits,
                len(hits),
            )

        return [item for hit in hits if (item := self._to_raw_item(hit)) is not None]

    def _request(self, start: datetime, end: datetime) -> dict:
        start_ts = int(start.timestamp())
        end_ts = int(end.timestamp())
        resp = self._client.get(
            ALGOLIA_SEARCH_URL,
            params={
                "tags": "story",
                "numericFilters": f"created_at_i>{start_ts},created_at_i<={end_ts}",
                "hitsPerPage": HITS_PER_PAGE,
            },
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _to_raw_item(hit: dict) -> RawItem | None:
        object_id = hit.get("objectID")
        title = hit.get("title")
        created_at_i = hit.get("created_at_i")
        if not object_id or not title or created_at_i is None:
            logger.debug("Skipping HN hit missing required fields: {}", hit)
            return None

        url = hit.get("url")
        return RawItem(
            id=f"hackernews:{object_id}",
            source="hackernews",
            title=title,
            url=url,
            domain=_domain_of(url),
            text=_clean_html(hit.get("story_text")),
            author=hit.get("author"),
            points=hit.get("points") or 0,
            num_comments=hit.get("num_comments") or 0,
            created_at=datetime.fromtimestamp(created_at_i, tz=timezone.utc),
            discussion_url=f"https://news.ycombinator.com/item?id={object_id}",
            source_id=str(object_id),
        )
