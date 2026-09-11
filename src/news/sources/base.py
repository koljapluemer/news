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

    def fetch(self, window_start: datetime, window_end: datetime) -> list[RawItem]: ...
