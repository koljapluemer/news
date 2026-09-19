"""Reddit source, backed by the public per-subreddit Atom feed.

Reddit's Data API has required manual, approval-gated OAuth registration
since its Nov 2025 "Responsible Builder Policy" (self-service app creation
is closed), and even the old unauthenticated `.json` workaround was
reportedly shut off in mid-2026. The `.rss` suffix on any public Reddit
URL, however, was never part of that priced/gated surface -- but Reddit
tightened *its* rate limit hard too (mid-2026: ~100 requests/10min down to
~1 request/minute per IP for anonymous requests).

Two modes, depending on whether Reddit RSS credentials are configured:

  - **Authenticated (preferred).** With `REDDIT_RSS_USER` and
    `REDDIT_RSS_FEED` set in `.env` (copied from the private feed URLs at
    https://www.reddit.com/prefs/feeds/), those are sent as the `user=` /
    `feed=` query params, which reportedly lifts the anonymous 1 req/min
    limit (https://lapcatsoftware.com/articles/2026/6/3.html -- one
    source, so re-verify if 429s come back). Each subreddit then gets its
    own request and its own `FEED_LIMIT` budget, so a busy subreddit can't
    crowd a quiet one out of the feed. Only in this mode can the account's
    personal front page (`https://www.reddit.com/.rss`) be fetched too
    (`sources.reddit.front_page: true`); anonymously that URL would just
    return Reddit's generic default front page.
  - **Anonymous fallback.** Without credentials every subreddit is fetched
    in a *single* combined request via Reddit's multireddit URL syntax
    (`r/sub1+sub2+.../new.rss`) to stay under the 1 req/min budget.
    `FEED_LIMIT` is then shared across every subreddit in the batch, so a
    very active subreddit can crowd quieter ones out -- watch the
    per-subreddit counts this logs; a subreddit that unexpectedly returns
    0 while others return plenty is more likely crowded out than
    genuinely quiet. A nonexistent/typo'd subreddit in the combined list
    doesn't fail the request, it just contributes nothing.

Either way, `RedditBatch` does the fetching once and splits the result by
each entry's own `<category term="...">` (which subreddit it's actually
from), and `RedditSource` (one per subreddit, plus one for the front page
if enabled) just reads its own slice out of the shared batch. Nothing
downstream of `NewsSource.fetch()` changes: caching, logging, the
shortlist floor, and interest scoping all stay per-source -- see
docs/architecture.md.

The front page source is named `reddit:front-page` (hyphens aren't legal
in subreddit names, so it can't collide with a real one), but its *items*
keep their real subreddit as `RawItem.source`, so a front page post from
r/languagelearning is scoped/capped like any other r/languagelearning post.
A post that appears both there and in a configured subreddit is deduped by
id in the hard filters. The front page feed is Reddit's "hot" ranking of
the account's subscriptions, not chronological.

The `feed` token is a credential (it grants read access to the account's
private feeds), so it must never reach logs: httpx error strings embed the
full request URL, hence every logged exception goes through `_redact`.

Trade-offs from using RSS rather than the API:
  - No time-range query, and this source deliberately ignores the
    pipeline's time window: every entry the feed returns (the newest
    `FEED_LIMIT` per request) is kept, however old. Posts on Reddit stay
    relevant far longer than a 30h window suggests, and the window was
    silently discarding most of what the feed returned. Only the feed's own
    cap limits how far back we see, so a very active subreddit polled
    infrequently can still miss posts between runs.
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

import os
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from news.logging_setup import logger
from news.models import RawItem
from news.sources._util import clean_html

ATOM_NS = "{http://www.w3.org/2005/Atom}"
FEED_LIMIT = 100
USER_AGENT = "news-pipeline/0.1 (personal single-user feed aggregator)"
FRONT_PAGE = "front-page"
"""Pseudo-subreddit name for the personal front page source. Hyphens are
not legal in real subreddit names, so this can't collide with one."""


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


def credentials_from_env() -> tuple[str, str] | None:
    """(user, feed) from `REDDIT_RSS_USER` / `REDDIT_RSS_FEED`, or None if
    not both set. Read lazily (not at import) so `.env` is already loaded --
    `_util` does that when this module imports it."""
    user = os.environ.get("REDDIT_RSS_USER")
    feed = os.environ.get("REDDIT_RSS_FEED")
    if user and feed:
        return user, feed
    if user or feed:
        logger.warning(
            "Only one of REDDIT_RSS_USER / REDDIT_RSS_FEED is set; ignoring it and "
            "fetching reddit anonymously (both are needed, see .env.example)"
        )
    return None


class RedditBatch:
    """Fetches every subreddit in `subreddits` (one request each with
    `credentials`, one combined request without), plus the personal front
    page if `front_page` (which requires `credentials`), memoizing the
    (possibly empty, on failure) result so however many sibling
    `RedditSource`s share this batch only ever trigger it once."""

    def __init__(
        self,
        subreddits: list[str],
        credentials: tuple[str, str] | None = None,
        front_page: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        if front_page and credentials is None:
            raise ValueError("the reddit front page feed needs credentials (REDDIT_RSS_USER / REDDIT_RSS_FEED)")
        self.subreddits = subreddits
        self._credentials = credentials
        self._front_page = front_page
        self._client = client or httpx.Client(timeout=30.0, headers={"User-Agent": USER_AGENT})
        self._result: dict[str, list[RawItem]] | None = None

    def items_for(self, subreddit: str) -> list[RawItem]:
        """`subreddit` is a configured subreddit name, or `FRONT_PAGE`."""
        if self._result is None:
            self._result = self._fetch()
        return self._result.get(subreddit, [])

    def _redact(self, text: object) -> str:
        """Scrub the feed token out of anything headed for the logs (httpx
        error messages include the full request URL, query string and all)."""
        text = str(text)
        return text.replace(self._credentials[1], "<redacted>") if self._credentials else text

    def _fetch(self) -> dict[str, list[RawItem]]:
        by_target: dict[str, list[RawItem]] = {s: [] for s in self.subreddits}
        canonical_by_lower = {s.lower(): s for s in self.subreddits}

        # Authenticated: one request per subreddit. Anonymous: everything in
        # one combined request, to stay under the 1 req/min limit.
        if self._credentials:
            groups = [[s] for s in self.subreddits]
        else:
            groups = [self.subreddits] if self.subreddits else []
        for group in groups:
            combined = "+".join(group)
            for item in self._fetch_feed(f"/r/{combined}/new.rss", combined):
                fetched_subreddit = item.source.removeprefix("reddit:")
                canonical = canonical_by_lower.get(fetched_subreddit.lower())
                if canonical is None:
                    logger.warning(
                        "Reddit feed for {} returned unexpected subreddit {!r}; skipping it",
                        combined,
                        fetched_subreddit,
                    )
                    continue
                by_target[canonical].append(item)

        if self._front_page:
            # Items keep their own subreddit as `source` -- only the fetch
            # bucket is the front page. See module docstring.
            by_target[FRONT_PAGE] = self._fetch_feed("/.rss", FRONT_PAGE)

        for target, target_items in by_target.items():
            logger.info("Fetched {} reddit {} posts", len(target_items), target)

        return by_target

    def _fetch_feed(self, path: str, label: str) -> list[RawItem]:
        """One request for `path`. Returns every parsed item, or [] if the
        request failed -- a failure for one feed must not sink the others."""
        params: dict[str, str | int] = {"limit": FEED_LIMIT}
        if self._credentials:
            params["user"], params["feed"] = self._credentials

        logger.info("Fetching reddit RSS feed for: {}", label)
        try:
            resp = self._client.get(f"https://www.reddit.com{path}", params=params)
        except httpx.HTTPError as exc:
            logger.warning("Reddit request failed for {}: {}; skipping", label, self._redact(exc))
            return []

        if resp.status_code == 429:
            logger.warning("Reddit rate-limited feed for {} (429); skipping", label)
            return []
        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Reddit request failed for {}: {}; skipping", label, self._redact(exc))
            return []

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse reddit RSS for {}: {}", label, exc)
            return []

        entries = root.findall(f"{ATOM_NS}entry")
        return [item for entry in entries if (item := _parse_entry(entry)) is not None]


class RedditSource:
    has_score = False

    def __init__(self, subreddit: str, batch: RedditBatch) -> None:
        self.subreddit = subreddit
        self.name = f"reddit:{subreddit}"
        self._batch = batch

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]:
        """Ignores the window -- see the module docstring."""
        return self._batch.items_for(self.subreddit)
