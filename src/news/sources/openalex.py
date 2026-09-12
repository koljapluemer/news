"""OpenAlex source, backed by the free, keyless OpenAlex REST API.

Like Crossref, this indexes works across publishers rather than one
repository, so each configured instance is a `title_and_abstract.search`
term (analogous to an arxiv category or subreddit) rather than a fixed feed.

Unlike Crossref, there's no keyless equivalent of "date this record was
added" here: `from_created_date`/`to_created_date` exist but return a 403
("Plan upgrade required") on the free, unauthenticated tier, so `fetch()`
has to poll by `publication_date` instead. That field can lag when a work
actually lands in OpenAlex's index (backfilled metadata, slow publisher
feeds), meaning an item can fail to appear under a publication date that's
already scrolled out of the window by the time it's indexed -- and, unlike
arxiv/reddit, there's no client-side fallback field to catch it since we
don't reprocess past windows. Expect occasional silent misses near the
window edge; this is a real gap, not just a documentation caveat.

The date filter is day-granularity server-side, so `fetch()` requests the
[start_date, end_date] day range and refines to the exact window
client-side using `publication_date` (also day-precision -- OpenAlex
doesn't expose a finer publication timestamp).

`abstract_inverted_index` is OpenAlex's compressed abstract representation
(word -> list of positions); `_reconstruct_abstract` turns it back into
plain text.

No comparable concept of points/votes exists here (`has_score = False`);
`cited_by_count` exists but is a citation count, not a recency signal, and
a new item will always read ~0.

Like Crossref, OpenAlex's anonymous pool is heavily throttled; adding a
`mailto` param moves requests into its dedicated polite pool -- see
`news.sources._util.CONTACT_EMAIL` (set via `NEWS_CONTACT_EMAIL`).
`get_with_retry` also retries a handful of times on 429 as a fallback.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx

from news.logging_setup import logger
from news.models import RawItem
from news.sources._util import CONTACT_EMAIL, get_with_retry

OPENALEX_URL = "https://api.openalex.org/works"
PER_PAGE = 50
USER_AGENT = "news-pipeline/0.1 (personal single-user feed aggregator" + (
    f"; mailto:{CONTACT_EMAIL})" if CONTACT_EMAIL else ")"
)


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    positions: dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = word
    if not positions:
        return None
    return " ".join(positions[i] for i in sorted(positions))


def _authors(entry: dict) -> str | None:
    names = [
        a.get("author", {}).get("display_name")
        for a in entry.get("authorships", [])
    ]
    names = [n for n in names if n]
    return ", ".join(names) or None


class OpenAlexSource:
    has_score = False

    def __init__(self, query: str, client: httpx.Client | None = None) -> None:
        self.query = query
        self.name = f"openalex:{query}"
        self._client = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]:
        logger.info("Fetching OpenAlex works for query {!r}", self.query)
        params = {
            "filter": (
                f"from_publication_date:{window_start.date().isoformat()},"
                f"to_publication_date:{window_end.date().isoformat()},"
                f"title_and_abstract.search:{self.query}"
            ),
            "per-page": PER_PAGE,
            "sort": "publication_date:desc",
            "select": "id,doi,title,abstract_inverted_index,authorships,publication_date",
        }
        if CONTACT_EMAIL:
            params["mailto"] = CONTACT_EMAIL
        try:
            resp = get_with_retry(self._client, OPENALEX_URL, params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("OpenAlex request failed for {!r}: {}; skipping this run", self.query, exc)
            return []

        entries = resp.json().get("results", [])
        items = [item for entry in entries if (item := self._to_raw_item(entry)) is not None]
        in_window = [i for i in items if window_start.date() <= i.created_at.date() <= window_end.date()]

        if len(entries) >= PER_PAGE and items and min(i.created_at for i in items) > window_start:
            logger.warning(
                "OpenAlex query {!r} (per-page={}) doesn't reach back to window_start={}; "
                "older items in the window may be missing -- poll more often or narrow the query",
                self.query,
                PER_PAGE,
                window_start,
            )

        logger.info(
            "Fetched {} OpenAlex items in window for {!r} ({} total in response)",
            len(in_window),
            self.query,
            len(entries),
        )
        return in_window

    def _to_raw_item(self, entry: dict) -> RawItem | None:
        openalex_id = entry.get("id")
        title = entry.get("title")
        pub_date = entry.get("publication_date")

        if not openalex_id or not title or not pub_date:
            logger.debug("Skipping OpenAlex entry missing required fields: {}", openalex_id)
            return None

        source_id = openalex_id.rsplit("/", 1)[-1]
        doi = entry.get("doi")
        url = doi or openalex_id

        return RawItem(
            id=f"openalex:{source_id}",
            source=self.name,
            title=" ".join(title.split()),
            url=url,
            domain="openalex.org" if not doi else "doi.org",
            text=_reconstruct_abstract(entry.get("abstract_inverted_index")),
            author=_authors(entry),
            points=0,
            num_comments=0,
            created_at=datetime.combine(date.fromisoformat(pub_date), datetime.min.time(), tzinfo=timezone.utc),
            discussion_url=url,
            source_id=source_id,
        )
