"""Reddit source, backed by the public per-subreddit Atom feed.

Reddit's Data API has required manual, approval-gated OAuth registration
since its Nov 2025 "Responsible Builder Policy" (self-service app creation
is closed), and even the old unauthenticated `.json` workaround was
reportedly shut off in mid-2026. The `.rss` suffix on any public Reddit
URL, however, was never part of that priced/gated surface and still works
unauthenticated -- so this source polls
`https://www.reddit.com/r/<subreddit>/new.rss` instead of the API.

Trade-offs from using RSS rather than the API:
  - No time-range query. The feed returns only the most recent posts (up
    to `FEED_LIMIT`, Reddit's cap is 100), so `fetch()` filters
    client-side by the window and can silently miss older posts on a
    very active subreddit polled infrequently -- logged as a warning when
    a full page doesn't reach back to window_start.
  - No score/comment-count field in the feed at all, unlike the JSON API
    -- points/num_comments are always 0 here (`has_score = False`, so
    hard filtering doesn't apply a min-points floor to this source).
  - No external submission URL. The feed only exposes the comments
    permalink, not the link a link-post points to, so `url` and
    `discussion_url` end up identical and `domain` is always
    "reddit.com" -- fine for dedup, just don't expect domain-based
    blacklisting to see a link post's real target domain.
  - Aggressive, tight rate limiting even for a single unauthenticated
    request (observed emptying the per-minute budget after one call) --
    REQUEST_DELAY_SECONDS is deliberately generous, and a 429 is treated
    as "skip this subreddit for this run", not a hard failure.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from news.logging_setup import logger
from news.models import RawItem
from news.sources._util import clean_html

ATOM_NS = "{http://www.w3.org/2005/Atom}"
FEED_LIMIT = 100
REQUEST_DELAY_SECONDS = 2.0
USER_AGENT = "news-pipeline/0.1 (personal single-user feed aggregator)"


class RedditSource:
    has_score = False

    def __init__(self, subreddit: str, client: httpx.Client | None = None) -> None:
        self.subreddit = subreddit
        self.name = f"reddit:{subreddit}"
        self._client = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]:
        logger.info("Fetching r/{} RSS feed", self.subreddit)
        resp = self._client.get(
            f"https://www.reddit.com/r/{self.subreddit}/new.rss", params={"limit": FEED_LIMIT}
        )
        time.sleep(REQUEST_DELAY_SECONDS)

        if resp.status_code == 429:
            logger.warning("Reddit rate-limited r/{} (429); skipping this source for this run", self.subreddit)
            return []
        resp.raise_for_status()

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse RSS for r/{}: {}", self.subreddit, exc)
            return []

        entries = root.findall(f"{ATOM_NS}entry")
        items = [item for entry in entries if (item := self._to_raw_item(entry)) is not None]
        in_window = [i for i in items if window_start <= i.created_at <= window_end]

        if len(items) >= FEED_LIMIT and items and min(i.created_at for i in items) > window_start:
            logger.warning(
                "r/{} feed page (limit={}) doesn't reach back to window_start={}; "
                "older posts in the window may be missing -- poll more often or shrink the window",
                self.subreddit,
                FEED_LIMIT,
                window_start,
            )

        logger.info(
            "Fetched {} r/{} posts in window ({} total in feed page)", len(in_window), self.subreddit, len(items)
        )
        return in_window

    def _to_raw_item(self, entry: ET.Element) -> RawItem | None:
        entry_id = entry.findtext(f"{ATOM_NS}id")
        title = entry.findtext(f"{ATOM_NS}title")
        published = entry.findtext(f"{ATOM_NS}published") or entry.findtext(f"{ATOM_NS}updated")
        link_el = entry.find(f"{ATOM_NS}link")

        if not entry_id or not title or not published or link_el is None:
            logger.debug("Skipping r/{} entry missing required fields: {}", self.subreddit, entry_id)
            return None

        permalink = link_el.get("href")
        author_name = entry.findtext(f"{ATOM_NS}author/{ATOM_NS}name")
        content_el = entry.find(f"{ATOM_NS}content")
        text = clean_html(content_el.text if content_el is not None else None)

        return RawItem(
            id=f"reddit:{entry_id}",
            source=self.name,
            title=" ".join(title.split()),
            url=permalink,
            domain="reddit.com",
            text=text,
            author=author_name.removeprefix("/u/") if author_name else None,
            points=0,
            num_comments=0,
            created_at=datetime.fromisoformat(published),
            discussion_url=permalink,
            source_id=entry_id,
        )
