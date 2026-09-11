"""Reddit source, backed by the public per-subreddit Atom feed.

Reddit's Data API has required manual, approval-gated OAuth registration
since its Nov 2025 "Responsible Builder Policy" (self-service app creation
is closed), and even the old unauthenticated `.json` workaround was
reportedly shut off in mid-2026. The `.rss` suffix on any public Reddit
URL, however, was never part of that priced/gated surface -- but Reddit
tightened *its* rate limit hard too (mid-2026: ~100 requests/10min down to
~1 request/minute per IP), so fetching N subreddits as N sequential
requests reliably 429s past N=1.

To stay under that budget, every subreddit configured for this profile is
fetched in a *single* combined request via Reddit's multireddit URL syntax
(`r/sub1+sub2+.../new.rss`) -- `RedditBatch` does that fetch once and
splits the result by each entry's own `<category term="...">` (which
subreddit it's actually from), and `RedditSource` (one per subreddit, same
as before) just reads its own slice out of the shared batch. Nothing
downstream of `NewsSource.fetch()` changes: caching, logging, the
shortlist floor, and interest scoping all stay per-subreddit exactly as
if each were fetched independently -- see docs/architecture.md.

Trade-offs from combining the request (on top of the RSS-vs-API ones
below): `FEED_LIMIT` is a budget shared across every subreddit in the
batch, not per subreddit -- a very active subreddit can crowd quieter
ones out of a given fetch, which single-subreddit requests didn't risk.
Watch the per-subreddit counts this logs; a subreddit that unexpectedly
returns 0 while others return plenty is more likely crowded out than
genuinely quiet. Also unverified: whether a private/banned/typo'd
subreddit in the combined list silently drops out or fails the whole
batch -- worth checking logs after adding a new one.

Trade-offs from using RSS rather than the API:
  - No time-range query. The feed returns only the most recent posts (up
    to `FEED_LIMIT` total across the batch), so `fetch()` filters
    client-side by the window and can silently miss older posts on very
    active subreddits polled infrequently -- logged as a warning when a
    full page doesn't reach back to window_start.
  - No score/comment-count field in the feed at all, unlike the JSON API
    -- points/num_comments are always 0 here (`has_score = False`, so
    hard filtering doesn't apply a min-points floor to this source).
  - No external submission URL. The feed only exposes the comments
    permalink, not the link a link-post points to, so `url` and
    `discussion_url` end up identical and `domain` is always
    "reddit.com" -- fine for dedup, just don't expect domain-based
    blacklisting to see a link post's real target domain.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from news.logging_setup import logger
from news.models import RawItem
from news.sources._util import clean_html

ATOM_NS = "{http://www.w3.org/2005/Atom}"
FEED_LIMIT = 100
USER_AGENT = "news-pipeline/0.1 (personal single-user feed aggregator)"


def _parse_entry(entry: ET.Element) -> RawItem | None:
    entry_id = entry.findtext(f"{ATOM_NS}id")
    title = entry.findtext(f"{ATOM_NS}title")
    published = entry.findtext(f"{ATOM_NS}published") or entry.findtext(f"{ATOM_NS}updated")
    link_el = entry.find(f"{ATOM_NS}link")
    category_el = entry.find(f"{ATOM_NS}category")

    if not entry_id or not title or not published or link_el is None or category_el is None:
        logger.debug("Skipping reddit entry missing required fields: {}", entry_id)
        return None

    subreddit = category_el.get("term")
    if not subreddit:
        logger.debug("Skipping reddit entry with no subreddit in its <category>: {}", entry_id)
        return None

    permalink = link_el.get("href")
    author_name = entry.findtext(f"{ATOM_NS}author/{ATOM_NS}name")
    content_el = entry.find(f"{ATOM_NS}content")
    text = clean_html(content_el.text if content_el is not None else None)

    return RawItem(
        id=f"reddit:{entry_id}",
        source=f"reddit:{subreddit}",
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


class RedditBatch:
    """Fetches every subreddit in `subreddits` with one combined request,
    memoizing the (possibly empty, on failure) result so however many
    sibling `RedditSource`s share this batch only ever trigger it once."""

    def __init__(self, subreddits: list[str], client: httpx.Client | None = None) -> None:
        self.subreddits = subreddits
        self._client = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})
        self._result: dict[str, list[RawItem]] | None = None

    def items_for(self, subreddit: str, window_start: datetime, window_end: datetime) -> list[RawItem]:
        if self._result is None:
            self._result = self._fetch(window_start, window_end)
        return self._result.get(subreddit, [])

    def _fetch(self, window_start: datetime, window_end: datetime) -> dict[str, list[RawItem]]:
        combined = "+".join(self.subreddits)
        empty: dict[str, list[RawItem]] = {s: [] for s in self.subreddits}

        logger.info("Fetching combined reddit RSS feed for: {}", combined)
        try:
            resp = self._client.get(
                f"https://www.reddit.com/r/{combined}/new.rss", params={"limit": FEED_LIMIT}
            )
        except httpx.HTTPError as exc:
            logger.warning("Reddit request failed for {}: {}; skipping reddit for this run", combined, exc)
            return empty

        if resp.status_code == 429:
            logger.warning("Reddit rate-limited combined feed for {} (429); skipping reddit for this run", combined)
            return empty
        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Reddit request failed for {}: {}; skipping reddit for this run", combined, exc)
            return empty

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse combined reddit RSS for {}: {}", combined, exc)
            return empty

        entries = root.findall(f"{ATOM_NS}entry")
        items = [item for entry in entries if (item := _parse_entry(entry)) is not None]
        in_window = [i for i in items if window_start <= i.created_at <= window_end]

        if len(items) >= FEED_LIMIT and items and min(i.created_at for i in items) > window_start:
            logger.warning(
                "Combined reddit feed (limit={}) doesn't reach back to window_start={}; "
                "older posts in the window may be missing -- poll more often, shrink the window, "
                "or split into fewer subreddits per batch",
                FEED_LIMIT,
                window_start,
            )

        by_subreddit = dict(empty)
        for item in in_window:
            by_subreddit[item.source.removeprefix("reddit:")].append(item)

        for subreddit, subreddit_items in by_subreddit.items():
            logger.info("Fetched {} r/{} posts in window (combined batch)", len(subreddit_items), subreddit)

        return by_subreddit


class RedditSource:
    has_score = False

    def __init__(self, subreddit: str, batch: RedditBatch) -> None:
        self.subreddit = subreddit
        self.name = f"reddit:{subreddit}"
        self._batch = batch

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]:
        return self._batch.items_for(self.subreddit, window_start, window_end)
