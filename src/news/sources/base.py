"""Common interface every news source implements.

A source's only job is: given a time window, return normalized `RawItem`s.
No disk I/O, no filtering, no scoring -- that all happens downstream so
sources stay trivial to add and test in isolation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from news.models import RawItem


class NewsSource(Protocol):
    name: str

    has_score: bool
    """Whether `RawItem.points` carries a real vote/score signal for this
    source. False for sources with no such concept (e.g. RSS-only feeds
    like arxiv/reddit here) -- hard filtering skips the min-points floor
    for those rather than dropping everything, since every item would
    otherwise have points=0."""

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]: ...
