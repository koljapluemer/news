# Architecture

## Module layout

```
src/news/
  models.py           Normalized data models (RawItem, ScoredItem, ...)
  config.py            Interest profile loading/validation
  logging_setup.py      loguru file+console setup
  storage.py            Disk layout: raw cache, run outputs
  pipeline.py            Orchestrates fetch -> filter -> rank -> output
  cli.py                 `uv run news` entry point

  sources/
    base.py             NewsSource protocol
    hackernews.py        Algolia HN Search API implementation
    arxiv.py              Per-category arXiv RSS digest
    reddit.py              Per-subreddit (and front page) Atom feeds
    _util.py               Shared HTML/domain cleanup helpers

  filters/
    hard.py             Stage 0: dedup, min score, blacklist

  rank/
    embed.py            Stage 1: embedding similarity scoring
    llm_rerank.py         Stage 2: local LLM reranking (Ollama)
```

## Design principles

**Sources are dumb.** A `NewsSource` only translates its native API into
`RawItem`s for a given time window -- no filesystem access, no filtering.
This keeps sources trivial to add and unit-test in isolation, and means
the rest of the pipeline never needs to know which source an item came
from.

**Everything downstream is source-agnostic.** Filtering and ranking
operate purely on `RawItem`/`ScoredItem`, never on source-specific fields.
Adding a source means writing one new file and registering a factory for
it in `pipeline.SOURCE_FACTORIES`.

**One `NewsSource` instance per target, not per source type.** `arxiv`
and `reddit` each watch a list of targets (categories, subreddits) --
rather than one instance internally looping over that list, each target
gets its own instance (`name = "arxiv:cs.CL"`, `"reddit:MachineLearning"`)
via that source's factory. This gives each target its own raw-cache file,
log lines, and shortlist-floor/max-per-source accounting for free, so a
single struggling subreddit or category is visible and containable rather
than merged into one opaque "reddit" bucket. Per-entry `sources: [...]`
scoping and `SourceSettings.min_points` match either the full instance
name or the bare type prefix (`config.source_matches`), so
`sources: ["reddit"]` covers every subreddit and
`sources: ["reddit:MachineLearning"]` targets just one.

**A profile can enable/disable sources and scope interests to a source.**
`InterestProfile.sources` (a `dict[str, SourceSettings]`) turns whole
sources on/off per profile -- e.g. a non-technical reader's profile can
disable `hackernews` outright, rather than trying to anti-interest their
way out of an entire source. Separately, any `InterestEntry` (interest or
anti-interest) can carry a `sources: [...]` list to restrict it to
specific sources -- e.g. a narrow research-direction interest that's only
meaningful on `arxiv`, or an anti-interest like "drama, flame-bait" that
only applies to `hackernews`. An entry with no `sources` applies
everywhere. See `config/interests.yaml` for examples of both.

**Cross-source ranking guards against one source dominating.** Two
separate mechanisms, for two separate failure modes:
- Stage 2's shortlist selection (`llm_rerank._select_shortlist`)
  guarantees each source a minimum number of slots before filling the
  rest by embedding score -- otherwise a source whose titles happen to
  score higher on stage 1 (for phrasing/style reasons, not true
  relevance) could crowd every other source out of the LLM stage
  entirely, since stage 1 sorts the pooled candidate list globally.
- The final top-N selection (`pipeline.run_pipeline`) caps how many
  slots any one source can take (`max_per_source`), so even a source the
  LLM genuinely rates higher can't fill the whole digest.
Per-source candidate/shortlist/output counts are logged at each stage
(and raw counts persisted in `RunMetadata.source_candidate_counts`) to
make this visible rather than assumed.

**Sources without a real score/vote concept don't get penalized for it.**
`NewsSource.has_score` (False for `arxiv`/`reddit`, True for `hackernews`)
tells `pipeline._resolve_min_points` to skip the min-points floor for that
source entirely, rather than dropping every item because arXiv papers and
Reddit's RSS feed both report `points=0` unconditionally (no vote data is
exposed in either feed). A profile can still force a specific floor via
`sources.<type>.min_points`, which always wins over the has_score default.

**Cheap-to-expensive funnel.** Stage 0 (hard filters) and stage 1
(embeddings) are cheap enough to run over every candidate. Stage 2 (local
LLM judgment) is the expensive step, so it's deliberately restricted to a
shortlist (default: top 40 by embedding score). This is what makes running
a 9B local model for reranking practical on a laptop for a daily batch job.

**Anti-interests are a soft counterweight, not a filter.** `anti_interests`
in `interests.yaml` mirror `interests` (text + weight) but subtract: stage 1
deducts their weighted-max embedding similarity from the score, and stage 2
shows them to the LLM as "less interested in" context. Unlike `blacklist`,
a story can still surface if it matches a real interest strongly enough.

**Raw fetch is cached per calendar day, independent of ranking runs.**
Raw HN data for a given day doesn't change once fetched; your interest
profile and ranking logic will. Separating them means you can re-run
ranking against the same day's data with a different profile/model without
re-fetching, which is also what makes before/after algorithm comparisons
possible later (see `docs/data_layout.md`).

## Known simplifications (v1)

These were deliberately left out of the first working version -- flagged
here so they're easy to revisit rather than silently forgotten:

- **No comment fetching.** Ranking currently runs on title (+ self-text
  for text posts) only. Fetching a story's comment tree (via Algolia's
  `items/{id}` endpoint) for shortlisted candidates would be a natural
  stage-1.5 addition if comment content turns out to matter for ranking.
- **Cache freshness isn't validated beyond "file exists for today".** If
  you run twice in one day expecting new stories, use `--force-fetch`;
  there's no automatic staleness check.
- **arxiv has day, not hour, granularity.** arXiv's per-category RSS feed
  gives every paper announced on a given day the same `pubDate` (midnight
  US/Eastern) -- there's no finer timestamp to fetch. Fine for a ~30h
  window, but don't expect within-day ordering or precision from it.
- **reddit has no reliable external link.** Reddit's Atom feed only
  exposes the comments permalink, not a link post's actual target URL, so
  `url`/`domain` are always the reddit.com permalink -- domain-based
  blacklisting won't see a link post's real domain. (Reddit's official API
  has required manual app approval since Nov 2025, so RSS is what we use.)
- **reddit fetching has two modes, keyed on `REDDIT_RSS_USER` /
  `REDDIT_RSS_FEED` in `.env`.** Reddit tightened *anonymous* RSS to ~1
  request/minute per IP in mid-2026 (down from ~100/10min). Sending the
  account's private feed `user=`/`feed=` params (from
  <https://www.reddit.com/prefs/feeds/>) reportedly lifts that
  ([source](https://lapcatsoftware.com/articles/2026/6/3.html), a single
  blog post -- we've seen it work, but it's undocumented Reddit behavior
  that can change).
  - *With credentials:* one request per subreddit, each with its own
    `FEED_LIMIT` (100) budget; and `sources.reddit.front_page: true` also
    fetches the personal front page (`/.rss`) as an extra source named
    `reddit:front-page`. Its items keep their real subreddit as
    `RawItem.source`, so interest scoping and `max_per_source` treat them
    like any other post from that subreddit; overlap with configured
    subreddits is deduped by id in the hard filters. The front page feed is
    Reddit's *hot* ranking of your subscriptions, not chronological.
  - *Without credentials:* all subreddits in one combined multireddit
    request (`r/sub1+sub2+.../new.rss`), split back up by each entry's
    `<category>`. The real trade-off: the 100-item budget is shared across
    the whole batch, so a very active subreddit can crowd quieter ones out
    -- watch for a configured subreddit unexpectedly returning 0 items
    while others return plenty. `front_page` is skipped with a warning (an
    anonymous `/.rss` is just Reddit's generic default front page).
  - *The pipeline's time window is ignored for reddit.* Every entry the
    feed returns (newest 100 per request) is kept regardless of age; the
    window used to discard most of what came back. Consequences: older
    posts can reappear in a run's candidates (dedup is per run, by id), and
    a very active subreddit polled infrequently can still miss posts
    between runs, since the feed only goes back 100 entries.
  - `RedditBatch` (`sources/reddit.py`) does the fetching once and
    `RedditSource` instances read their slice, so caching, logging, the
    shortlist floor and scoping are per-source in both modes.
  - The `feed` token is a credential: httpx error messages embed the full
    URL, so all logged exceptions go through `RedditBatch._redact`. Keep
    that in mind when adding any new logging around Reddit requests.
- **reddit follow-ups / TODO.**
  - Watch the first few real runs for `429`s or `403`s in the logs
    (`Reddit rate-limited feed ...`); if authenticated requests get
    limited too, the `user`/`feed` trick has stopped working.
  - Durable fix: apply for Reddit's official Data API (free for
    non-commercial use, ~100 req/min, but manual approval that can take
    weeks) and swap `RedditBatch` to OAuth -- that would also give real
    scores/comment counts and external link URLs, letting `has_score` flip
    to True.
  - Fallback if RSS dies entirely: see
    `docs/issues/add-headless-account-cookie-scraper-for-reddit.md`, or
    Arctic Shift / PullPush archives (freshness unverified).
  - `front_page` always uses hot ranking (`/.rss`); the chronological
    `/new/.rss` variant might suit windowed fetching better, untested.
- **`max_per_source` bites immediately once >1 source is enabled.** With
  only `hackernews` on, the cap never triggers (nothing to compete with).
  Turning on `arxiv`/`reddit` in `config/interests.yaml` makes it active
  for real -- worth a quick sanity check on the first run after enabling
  a new source that the top-N mix looks like what you'd expect, per
  `--max-per-source` and the per-source stage logs.

## Gotcha: reasoning models and `think=False`

`qwen3.5` (like Qwen3 before it) is a hybrid model that reasons at length
by default, even for trivial prompts -- in testing, a one-line scoring
task took 40+ seconds and, on the longer real prompts used here, could
burn through the entire context window on internal deliberation before
ever emitting the requested JSON, producing empty responses. `llm_rerank.py`
explicitly passes `think=False` to Ollama's chat call to avoid this. If you
swap in a different reasoning-capable model, check whether it needs the
same treatment (or a larger `num_ctx`) before assuming it's just slow.
