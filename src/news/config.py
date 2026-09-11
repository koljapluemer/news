"""Loading and validating the personal interest profile."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class InterestEntry(BaseModel):
    text: str
    weight: float = 1.0


class Blacklist(BaseModel):
    terms: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class InterestProfile(BaseModel):
    interests: list[InterestEntry]
    anti_interests: list[InterestEntry] = Field(default_factory=list)
    blacklist: Blacklist = Field(default_factory=Blacklist)


def load_interest_profile(path: Path) -> InterestProfile:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return InterestProfile.model_validate(raw)
