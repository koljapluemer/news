"""Orchestrates the full fetch -> filter -> rank -> output pipeline.

Currently wired to a single source (HackerNews); adding a source means
adding another `NewsSource` implementation and including it in
`_fetch_all`, nothing else here needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from news.config import load_interest_profile
from news.filters.hard import apply_hard_filters
from news.logging_setup import logger
from news.models import RawItem, RunMetadata, RunOutput, ScoredItem
from news.rank.embed import EMBEDDING_MODEL_NAME, EmbeddingRanker
from news.rank.llm_rerank import DEFAULT_MODEL_NAME, LLMReranker
from news.sources.hackernews import HackerNewsSource
from news import storage


@dataclass
class PipelineConfig:
    data_dir: Path
    interests_path: Path
    window_hours: float = 30.0
    min_points: int = 1
    shortlist_size: int = 40
    top_n: int = 10
    embedding_model_name: str = EMBEDDING_MODEL_NAME
    llm_model_name: str = DEFAULT_MODEL_NAME
    force_fetch: bool = False


def _fetch_all(cfg: PipelineConfig, window_start: datetime, window_end: datetime) -> list[RawItem]:
    day = window_end.date().isoformat()
    source = HackerNewsSource()

    if not cfg.force_fetch:
        cached = storage.load_cached_raw(cfg.data_dir, source.name, day)
        if cached is not None:
            return cached

    items = source.fetch(window_start, window_end)
    storage.save_raw(cfg.data_dir, source.name, day, items, window_start, window_end)
    return items


def run_pipeline(cfg: PipelineConfig) -> Path:
    profile = load_interest_profile(cfg.interests_path)
    logger.info(
        "Loaded interest profile: {} interests, {} blacklist terms, {} blacklist domains",
        len(profile.interests),
        len(profile.blacklist.terms),
        len(profile.blacklist.domains),
    )

    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=cfg.window_hours)

    raw_items = _fetch_all(cfg, window_start, window_end)
    hard_filtered = apply_hard_filters(raw_items, profile, min_points=cfg.min_points)

    embedder = EmbeddingRanker(cfg.embedding_model_name)
    scored = embedder.score(hard_filtered, profile)

    reranker = LLMReranker(cfg.llm_model_name)
    scored = reranker.rerank(scored, profile, shortlist_size=cfg.shortlist_size)

    ranked = sorted(
        (s for s in scored if s.final_score is not None),
        key=lambda s: s.final_score,
        reverse=True,
    )
    top = ranked[: cfg.top_n]
    if len(top) < cfg.top_n:
        logger.warning(
            "Only {} ranked items available (requested top {})", len(top), cfg.top_n
        )
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
        hard_filtered_count=len(hard_filtered),
        shortlisted_count=min(cfg.shortlist_size, len(hard_filtered)),
        model_name=cfg.llm_model_name,
        embedding_model_name=cfg.embedding_model_name,
    )
    output = RunOutput(meta=meta, items=top)
    out_path = storage.save_run_output(run_dir, output)
    storage.save_latest_pointer(cfg.data_dir, output)

    logger.info("Run complete: {} top items written to {}", len(top), out_path)
    return out_path
