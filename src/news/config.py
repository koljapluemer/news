"""Loading and validating the personal interest profile."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class InterestEntry(BaseModel):
    text: str
    weight: float = 1.0
    sources: list[str] | None = None
    """Restrict this entry to specific source names (e.g. ["arxiv"]).
    None (default) means it applies to every source."""


class Blacklist(BaseModel):
    terms: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class SourceSettings(BaseModel):
    """Extra fields are allowed so each source can define its own bespoke
    params (subreddit list, arxiv categories, ...) without changing this
    shared schema."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    min_points: int | None = None
    """Override the pipeline's default min-points floor for this source
    (e.g. force to 0 for a source with no native vote/score concept). None
    falls back to the source's own default -- see `NewsSource.has_score`."""


def source_matches(source: str, allowed: list[str] | None) -> bool:
    """True if `source` (e.g. "reddit:MachineLearning") is covered by
    `allowed`. `allowed=None` means "everywhere". A source's type prefix
    (before ":", e.g. "reddit") matches too, so `["reddit"]` covers every
    subreddit and `["reddit:MachineLearning"]` covers just that one."""
    if allowed is None:
        return True
    base = source.split(":", 1)[0]
    return source in allowed or base in allowed


class InterestProfile(BaseModel):
    interests: list[InterestEntry]
    anti_interests: list[InterestEntry] = Field(default_factory=list)
    blacklist: Blacklist = Field(default_factory=Blacklist)
    sources: dict[str, SourceSettings] = Field(default_factory=dict)

    def source_enabled(self, name: str) -> bool:
        """A source not mentioned in `sources:` is enabled by default.
        `name` here is the source *type* ("arxiv", "reddit"), not a
        per-instance name -- this is a whole-source on/off switch."""
        settings = self.sources.get(name)
        return settings.enabled if settings else True

    def interests_for_source(self, name: str) -> list[InterestEntry]:
        return [e for e in self.interests if source_matches(name, e.sources)]

    def anti_interests_for_source(self, name: str) -> list[InterestEntry]:
        return [e for e in self.anti_interests if source_matches(name, e.sources)]


def load_interest_profile(path: Path) -> InterestProfile:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return InterestProfile.model_validate(raw)
