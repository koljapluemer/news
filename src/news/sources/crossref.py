"""Crossref source, backed by the free, keyless Crossref REST API.

Crossref indexes DOI metadata across nearly every scholarly publisher, so
unlike arXiv/bioRxiv this isn't scoped to one repository -- each configured
instance is a bibliographic search term (`query.bibliographic`, matched
against title/author/etc.), analogous to an arxiv category or subreddit.

Polling uses `from-created-date`/`until-created-date`, not
`from-pub-date`/publication date: `published` is often only year- or
month-precision (many records have no day at all) and, more importantly,
old records get bulk-reindexed under later `indexed` timestamps, so neither
is a reliable "what's new" signal. `created` is when the DOI was first
registered and carries a full timestamp -- the right field for incremental
polling. Its date-range filter is day-granularity server-side, though, so
`fetch()` requests the [start_date, end_date] day range and then, like
arxiv/reddit, refines to the exact window client-side using the item's
real `created` timestamp.

`type:journal-article` is filtered server-side to skip components (figures,
datasets, etc.) and other non-article records Crossref also indexes.

No comparable concept of points/votes exists here (`has_score = False`).
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from news.logging_setup import logger
from news.models import RawItem
from news.sources._util import clean_html

CROSSREF_URL = "https://api.crossref.org/works"
ROWS = 50
USER_AGENT = "news-pipeline/0.1 (personal single-user feed aggregator)"


def _authors(entry: dict) -> str | None:
    names = [
        " ".join(part for part in (a.get("given"), a.get("family")) if part)
        for a in entry.get("author", [])
    ]
    names = [n for n in names if n]
    return ", ".join(names) or None


class CrossrefSource:
    has_score = False

    def __init__(self, query: str, client: httpx.Client | None = None) -> None:
        self.query = query
        self.name = f"crossref:{query}"
        self._client = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]:
        logger.info("Fetching Crossref works for query {!r}", self.query)
        params = {
            "query.bibliographic": self.query,
            "filter": (
                f"from-created-date:{window_start.date().isoformat()},"
                f"until-created-date:{window_end.date().isoformat()},"
                "type:journal-article"
            ),
            "rows": ROWS,
            "sort": "created",
            "order": "desc",
            "select": "DOI,title,abstract,author,created",
        }
        try:
            resp = self._client.get(CROSSREF_URL, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Crossref request failed for {!r}: {}; skipping this run", self.query, exc)
            return []

        entries = resp.json().get("message", {}).get("items", [])
        items = [item for entry in entries if (item := self._to_raw_item(entry)) is not None]
        in_window = [i for i in items if window_start <= i.created_at <= window_end]

        if len(entries) >= ROWS and items and min(i.created_at for i in items) > window_start:
            logger.warning(
                "Crossref query {!r} (rows={}) doesn't reach back to window_start={}; "
                "older items in the window may be missing -- poll more often or narrow the query",
                self.query,
                ROWS,
                window_start,
            )

        logger.info(
            "Fetched {} Crossref items in window for {!r} ({} total in response)",
            len(in_window),
            self.query,
            len(entries),
        )
        return in_window

    def _to_raw_item(self, entry: dict) -> RawItem | None:
        doi = entry.get("DOI")
        titles = entry.get("title") or []
        created = entry.get("created", {}).get("date-time")

        if not doi or not titles or not created:
            logger.debug("Skipping Crossref entry missing required fields: {}", doi)
            return None

        url = f"https://doi.org/{doi}"

        return RawItem(
            id=f"crossref:{doi}",
            source=self.name,
            title=" ".join(titles[0].split()),
            url=url,
            domain="doi.org",
            text=clean_html(entry.get("abstract")),
            author=_authors(entry),
            points=0,
            num_comments=0,
            created_at=datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc),
            discussion_url=url,
            source_id=doi,
        )
