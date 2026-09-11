"""Normalized data models shared across sources and pipeline stages.

Sources translate their native API responses into `RawItem`. Every later
stage only ever deals with these normalized models, so adding a new source
(RSS, Reddit, ...) or a new pipeline stage doesn't require touching the
others.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RawItem(BaseModel):
    """A single candidate item, normalized across sources."""

    id: str
    """Globally unique id, e.g. "hackernews:12345678"."""

    source: str
    """Short source name, e.g. "hackernews"."""

    title: str
    url: str | None = None
    """External article URL, if any (None for text-only posts)."""

    domain: str | None = None
    text: str | None = None
    """Self-post / body text, if any, HTML stripped."""

    author: str | None = None
    points: int = 0
    num_comments: int = 0
    created_at: datetime

    discussion_url: str
    """Link to the source's own discussion thread (always present)."""

    source_id: str
    """Raw id from the source API, for debugging/dedup."""


class ScoredItem(RawItem):
    """A RawItem augmented with stage-1 (embedding) scoring."""

    embedding_score: float
    embedding_matches: dict[str, float] = Field(default_factory=dict)
    """Per-interest cosine similarity, keyed by the interest text."""

    embedding_anti_score: float = 0.0
    """Weighted-max cosine similarity to anti-interests, subtracted into embedding_score."""
    embedding_anti_matches: dict[str, float] = Field(default_factory=dict)
    """Per-anti-interest cosine similarity, keyed by the anti-interest text."""

    llm_score: float | None = None
    llm_reason: str | None = None
    final_score: float | None = None


class RunMetadata(BaseModel):
    generated_at: datetime
    window_hours: float
    window_start: datetime
    window_end: datetime
    source_candidate_count: int
    source_candidate_counts: dict[str, int] = Field(default_factory=dict)
    """Raw candidate count per source, for spotting one source dominating volume."""
    hard_filtered_count: int
    shortlisted_count: int
    model_name: str
    embedding_model_name: str


class RunOutput(BaseModel):
    meta: RunMetadata
    items: list[ScoredItem]
