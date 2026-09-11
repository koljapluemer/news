"""Stage 0: cheap, non-ML filtering.

Dedup, drop below a minimum score, drop exact blacklist term/domain hits.
Everything here is a hard exclude -- for "less interested" use interest
weights in stage 1 instead.
"""

from __future__ import annotations

from news.config import InterestProfile
from news.logging_setup import logger
from news.models import RawItem


def apply_hard_filters(
    items: list[RawItem], profile: InterestProfile, min_points: dict[str, int]
) -> list[RawItem]:
    """`min_points` is per-source (keyed by `RawItem.source`, e.g.
    "hackernews" or "reddit:MachineLearning") -- sources with no native
    vote/score concept should be resolved to 0 by the caller (see
    `pipeline._resolve_min_points`), since every item there has
    points=0 and a nonzero floor would silently drop everything."""
    seen_ids: set[str] = set()
    blacklist_terms = [t.lower() for t in profile.blacklist.terms]
    blacklist_domains = {d.lower() for d in profile.blacklist.domains}

    kept: list[RawItem] = []
    for item in items:
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)

        if item.points < min_points.get(item.source, 0):
            continue

        if item.domain and item.domain.lower() in blacklist_domains:
            continue

        title_lower = item.title.lower()
        if any(term in title_lower for term in blacklist_terms):
            continue

        kept.append(item)

    logger.info(
        "Hard filters: {} -> {} items (min_points={}, {} blacklist terms, {} blacklist domains)",
        len(items),
        len(kept),
        min_points,
        len(blacklist_terms),
        len(blacklist_domains),
    )
    return kept
