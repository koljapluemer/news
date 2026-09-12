"""Orchestrates the full fetch -> filter -> rank -> output pipeline.

Adding a source means adding another `NewsSource` implementation and
registering a factory for it in `SOURCE_FACTORIES` -- nothing else here
needs to change. A factory takes that source's `SourceSettings` (or None,
if the profile doesn't mention it) and returns zero or more `NewsSource`
instances: most sources produce one, but a source with a list of targets
(arxiv categories, subreddits) produces one instance per target, so each
gets its own cache file, log lines, and shortlist-floor slot rather than
being merged into a single opaque bucket.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from news.config import InterestProfile, SourceSettings, load_interest_profile
from news.filters.hard import apply_hard_filters
from news.logging_setup import logger
from news.models import RawItem, RunMetadata, RunOutput, ScoredItem
from news.rank.embed import EMBEDDING_MODEL_NAME, EmbeddingRanker
from news.rank.llm_rerank import DEFAULT_MIN_SHORTLIST_PER_SOURCE, DEFAULT_MODEL_NAME, LLMReranker
from news.sources.arxiv import ArxivSource
from news.sources.base import NewsSource
from news.sources.crossref import CrossrefSource
from news.sources.hackernews import HackerNewsSource
from news.sources.openalex import OpenAlexSource
from news.sources.reddit import RedditBatch, RedditSource
from news import storage


def _extra(settings: SourceSettings | None) -> dict:
    return (settings.model_extra or {}) if settings else {}


def _hackernews_factory(settings: SourceSettings | None) -> list[NewsSource]:
    return [HackerNewsSource()]


def _arxiv_factory(settings: SourceSettings | None) -> list[NewsSource]:
    categories = _extra(settings).get("categories", [])
    if not categories:
        logger.warning("arxiv enabled but `sources.arxiv.categories` is empty in the profile; skipping")
        return []
    return [ArxivSource(category=c) for c in categories]


def _reddit_factory(settings: SourceSettings | None) -> list[NewsSource]:
    subreddits = _extra(settings).get("subreddits", [])
    if not subreddits:
        logger.warning("reddit enabled but `sources.reddit.subreddits` is empty in the profile; skipping")
        return []
    # All subreddits share one RedditBatch, so they cost one combined HTTP
    # request instead of one per subreddit -- see sources/reddit.py.
    batch = RedditBatch(subreddits)
    return [RedditSource(subreddit=s, batch=batch) for s in subreddits]


def _crossref_factory(settings: SourceSettings | None) -> list[NewsSource]:
    queries = _extra(settings).get("queries", [])
    if not queries:
        logger.warning("crossref enabled but `sources.crossref.queries` is empty in the profile; skipping")
        return []
    return [CrossrefSource(query=q) for q in queries]


def _openalex_factory(settings: SourceSettings | None) -> list[NewsSource]:
    queries = _extra(settings).get("queries", [])
    if not queries:
        logger.warning("openalex enabled but `sources.openalex.queries` is empty in the profile; skipping")
        return []
    return [OpenAlexSource(query=q) for q in queries]


SOURCE_FACTORIES: dict[str, Callable[[SourceSettings | None], list[NewsSource]]] = {
    "hackernews": _hackernews_factory,
    "arxiv": _arxiv_factory,
    "reddit": _reddit_factory,
    "crossref": _crossref_factory,
    "openalex": _openalex_factory,
}


@dataclass
class PipelineConfig:
    data_dir: Path
    interests_path: Path
    window_hours: float = 30.0
    min_points: int = 1
    shortlist_size: int = 40
    min_shortlist_per_source: int = DEFAULT_MIN_SHORTLIST_PER_SOURCE
    top_n: int = 10
    max_per_source: int = 4
    embedding_model_name: str = EMBEDDING_MODEL_NAME
    llm_model_name: str = DEFAULT_MODEL_NAME
    force_fetch: bool = False


def _log_by_source(label: str, items: list) -> None:
    counts = Counter(item.source for item in items)
    logger.info("{}: {} total, by source: {}", label, len(items), dict(counts))


def _build_sources(profile: InterestProfile) -> list[NewsSource]:
    sources: list[NewsSource] = []
    for name, factory in SOURCE_FACTORIES.items():
        if not profile.source_enabled(name):
            logger.info("Source '{}' disabled in profile, skipping", name)
            continue
        sources.extend(factory(profile.sources.get(name)))
    return sources


def _resolve_min_points(
    sources: list[NewsSource], profile: InterestProfile, default_min_points: int
) -> dict[str, int]:
    """Per-source min-points floor: an explicit `sources.<type>.min_points`
    in the profile wins; otherwise sources without a real score concept
    (`has_score = False`) resolve to 0, and the rest fall back to
    `default_min_points`. Keyed by source *type*, since that's the level
    SourceSettings.min_points is configured at -- resolved to per-instance
    keys here so `apply_hard_filters` can do a plain dict lookup."""
    resolved: dict[str, int] = {}
    for source in sources:
        source_type = source.name.split(":", 1)[0]
        settings = profile.sources.get(source_type)
        if settings and settings.min_points is not None:
            resolved[source.name] = settings.min_points
        elif source.has_score:
            resolved[source.name] = default_min_points
        else:
            resolved[source.name] = 0
    return resolved


def _fetch_all(
    cfg: PipelineConfig, sources: list[NewsSource], window_start: datetime, window_end: datetime
) -> list[RawItem]:
    day = window_end.date().isoformat()
    items: list[RawItem] = []

    for source in sources:
        if not cfg.force_fetch:
            cached = storage.load_cached_raw(cfg.data_dir, source.name, day)
            if cached is not None:
                items.extend(cached)
                continue

        try:
            fetched = source.fetch(window_start, window_end)
        except Exception:
            logger.exception("Fetching source '{}' failed; skipping it for this run", source.name)
            continue

        storage.save_raw(cfg.data_dir, source.name, day, fetched, window_start, window_end)
        items.extend(fetched)

    return items


def run_pipeline(cfg: PipelineConfig) -> Path:
    profile = load_interest_profile(cfg.interests_path)
    logger.info(
        "Loaded interest profile: {} interests, {} anti-interests, {} blacklist terms, {} blacklist domains",
        len(profile.interests),
        len(profile.anti_interests),
        len(profile.blacklist.terms),
        len(profile.blacklist.domains),
    )

    sources = _build_sources(profile)
    logger.info("Sources enabled this run: {}", [s.name for s in sources])

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=cfg.window_hours)

    raw_items = _fetch_all(cfg, sources, window_start, window_end)
    _log_by_source("Raw candidates", raw_items)

    min_points = _resolve_min_points(sources, profile, cfg.min_points)
    hard_filtered = apply_hard_filters(raw_items, profile, min_points=min_points)
    _log_by_source("After hard filters", hard_filtered)

    embedder = EmbeddingRanker(cfg.embedding_model_name)
    scored = embedder.score(hard_filtered, profile)

    reranker = LLMReranker(cfg.llm_model_name)
    scored = reranker.rerank(
        scored, profile, shortlist_size=cfg.shortlist_size, min_shortlist_per_source=cfg.min_shortlist_per_source
    )
    _log_by_source("Shortlisted for LLM rerank", [s for s in scored if s.llm_score is not None])

    ranked = sorted(
        (s for s in scored if s.final_score is not None),
        key=lambda s: s.final_score,
        reverse=True,
    )

    # Cap how many final slots one source can take, so a source that's
    # genuinely (or spuriously) scoring higher can't fill the whole digest.
    top: list[ScoredItem] = []
    per_source_count: Counter[str] = Counter()
    for item in ranked:
        if per_source_count[item.source] >= cfg.max_per_source:
            continue
        top.append(item)
        per_source_count[item.source] += 1
        if len(top) >= cfg.top_n:
            break

    if len(top) < cfg.top_n:
        logger.warning(
            "Only {} ranked items available (requested top {}, max {}/source)",
            len(top), cfg.top_n, cfg.max_per_source,
        )
    _log_by_source("Final output", top)
    for rank, item in enumerate(top, start=1):
        logger.debug("#{}: [{}] {}", rank, item.final_score, item.title)

    run_id = window_end.strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = storage.new_run_dir(cfg.data_dir, run_id)
    storage.save_run_config(run_dir, profile.model_dump())
    storage.save_scored(run_dir, scored)

    meta = RunMetadata(
        generated_at=window_end,
        window_hours=cfg.window_hours,
        window_start=window_start,
        window_end=window_end,
        source_candidate_count=len(raw_items),
        source_candidate_counts=dict(Counter(item.source for item in raw_items)),
        hard_filtered_count=len(hard_filtered),
        shortlisted_count=sum(1 for s in scored if s.llm_score is not None),
        model_name=cfg.llm_model_name,
        embedding_model_name=cfg.embedding_model_name,
    )
    output = RunOutput(meta=meta, items=top)
    out_path = storage.save_run_output(run_dir, output)
    storage.save_latest_pointer(cfg.data_dir, output)
    storage.upsert_feed(cfg.data_dir, top, surfaced_at=window_end)

    logger.info("Run complete: {} top items written to {}", len(top), out_path)
    return out_path
