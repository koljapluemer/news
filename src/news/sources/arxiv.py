"""arXiv source, backed by the per-category daily RSS digest feed.

arXiv publishes one RSS feed per category
(`https://export.arxiv.org/rss/<category>`, e.g. "cs.CL") listing that
day's new submissions. There's no time-range query and, more importantly,
no per-paper timestamp: every item in a given day's feed shares the same
`pubDate` (midnight US/Eastern of the announce day). `fetch()` just
compares that shared timestamp against [window_start, window_end] like
any other source -- it naturally falls out of the window once a run's
window no longer reaches back to the announce day, no special-casing
needed, but don't expect finer-than-a-day resolution from it.

No comparable concept of points/votes exists here (`has_score = False`),
so hard filtering doesn't apply a min-points floor to this source.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from news.logging_setup import logger
from news.models import RawItem

ARXIV_RSS_URL = "https://export.arxiv.org/rss/{category}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"

# Strips the boilerplate arXiv prepends to every RSS description, e.g.
# "arXiv:2609.10702v1 Announce Type: new \nAbstract: <the actual text>"
_DESC_PREFIX_RE = re.compile(r"^arXiv:\S+\s+Announce Type:\s*\S+\s*\n?Abstract:\s*", re.IGNORECASE)


class ArxivSource:
    has_score = False

    def __init__(self, category: str, client: httpx.Client | None = None) -> None:
        self.category = category
        self.name = f"arxiv:{category}"
        self._client = client or httpx.Client(timeout=30.0)

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]:
        logger.info("Fetching arXiv {} RSS feed", self.category)
        resp = self._client.get(ARXIV_RSS_URL.format(category=self.category))
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        raw_entries = channel.findall("item") if channel is not None else []

        items = [item for entry in raw_entries if (item := self._to_raw_item(entry)) is not None]
        in_window = [i for i in items if window_start <= i.created_at <= window_end]

        logger.info(
            "Fetched {} arXiv {} papers in window ({} total in today's feed)",
            len(in_window),
            self.category,
            len(items),
        )
        return in_window

    def _to_raw_item(self, entry: ET.Element) -> RawItem | None:
        title = entry.findtext("title")
        link = entry.findtext("link")
        guid = entry.findtext("guid")
        pub_date = entry.findtext("pubDate")

        if not title or not link or not guid or not pub_date:
            logger.debug("Skipping arXiv {} entry missing required fields: {}", self.category, guid)
            return None

        paper_id = link.rstrip("/").rsplit("/", 1)[-1]
        description = entry.findtext("description") or ""
        abstract = _DESC_PREFIX_RE.sub("", description).strip() or None

        return RawItem(
            id=f"arxiv:{paper_id}",
            source=self.name,
            title=" ".join(title.split()),
            url=link,
            domain="arxiv.org",
            text=abstract,
            author=entry.findtext(f"{DC_NS}creator"),
            points=0,
            num_comments=0,
            created_at=parsedate_to_datetime(pub_date),
            discussion_url=link,
            source_id=paper_id,
        )
