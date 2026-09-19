"""Generic RSS/Atom source, backed by arbitrary configured feed URLs.

Unlike arxiv's per-category feed (one hand-rolled, uniform, fully
controlled dialect parsed with plain ElementTree -- see arxiv.py), a
"generic RSS" source has to accept whatever an arbitrary configured feed
happens to serve: RSS 2.0 or Atom, unfamiliar namespaces, inconsistent
date formats, questionable encodings, occasionally malformed XML.
`feedparser` is used here instead of ElementTree for that reason -- it
normalizes RSS/Atom to one entry shape and is deliberately lenient about
malformed input (it sets `bozo`/`bozo_exception` rather than raising), so
one badly-behaved feed degrades to zero items instead of taking down the
whole run.

No comparable concept of points/votes exists here (`has_score = False`).

`fetch()` deliberately does *not* filter entries against [window_start,
window_end] the way every other source here does. A fixed wall-clock
window makes sense for a high-volume, high-cadence source like HN, but is
the wrong model for a typical low-volume blog/site feed: an infrequent
poster's item can easily be older than the window on the run where it's
first seen (or first added to the config), and would otherwise just be
silently dropped forever rather than surfaced once. Instead every entry
the feed currently lists is returned as a candidate every run, and
downstream `storage.upsert_feed` -- keyed by item id, re-bumping
`surfaced_at` on re-appearance rather than duplicating -- already makes
that safe: an old item ranking well just gets a fair shot the first time
it's evaluated instead of needing a wide enough window to catch it. The
same reasoning would apply to any other no-score, low-volume source later.
"""

from __future__ import annotations

from calendar import timegm
from datetime import datetime, timezone

import feedparser
import httpx

from news.logging_setup import logger
from news.models import RawItem
from news.sources._util import clean_html, domain_of

REQUEST_TIMEOUT_SECONDS = 30.0


class RssSource:
    has_score = False

    def __init__(self, feed_url: str, label: str | None = None, client: httpx.Client | None = None) -> None:
        self.feed_url = feed_url
        self.name = f"rss:{label or domain_of(feed_url) or feed_url}"
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]:
        # window_start/window_end are intentionally unused -- see module docstring.
        del window_start, window_end
        logger.info("Fetching RSS feed {!r} ({})", self.name, self.feed_url)

        try:
            resp = self._client.get(self.feed_url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("RSS feed {!r} request failed: {}; skipping this run", self.name, exc)
            return []

        try:
            parsed = feedparser.parse(resp.content)
        except Exception:
            logger.exception("RSS feed {!r} failed to parse; skipping this run", self.name)
            return []

        if parsed.get("bozo"):
            logger.warning(
                "RSS feed {!r} did not parse cleanly ({}); continuing with whatever entries were recovered",
                self.name,
                parsed.get("bozo_exception"),
            )

        entries = parsed.get("entries", [])
        items = [item for entry in entries if (item := self._to_raw_item(entry)) is not None]

        logger.info(
            "Fetched {} RSS items for {!r} ({} total entries in feed)",
            len(items),
            self.name,
            len(entries),
        )
        return items

    def _to_raw_item(self, entry: feedparser.FeedParserDict) -> RawItem | None:
        title = entry.get("title")
        link = entry.get("link")
        guid = entry.get("id") or link
        published = entry.get("published_parsed") or entry.get("updated_parsed")

        if not title or not link or not guid or published is None:
            logger.debug("Skipping RSS entry from {!r} missing required fields: {}", self.name, guid)
            return None

        return RawItem(
            id=f"{self.name}:{guid}",
            source=self.name,
            title=" ".join(title.split()),
            url=link,
            domain=domain_of(link),
            text=clean_html(entry.get("summary")),
            author=entry.get("author"),
            points=0,
            num_comments=0,
            created_at=datetime.fromtimestamp(timegm(published), tz=timezone.utc),
            discussion_url=link,
            source_id=guid,
        )
