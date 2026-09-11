"""Stage 1: cheap embedding-based scoring of every surviving candidate.

Each interest is embedded as its own sentence (rather than one blended
profile vector) so distinct topics don't dilute each other. A candidate's
score is the weighted-max cosine similarity across interests, which lets a
strong match on *any one* topic surface the item, rather than requiring
broad relevance to the whole profile.

Anti-interests work the same way but subtract: the weighted-max similarity
across anti-interests is deducted from the score. This is a soft
counterweight, not a filter -- a story can still surface if it matches a
real interest strongly enough to outweigh the penalty. Use `blacklist` in
`interests.yaml` for topics that should never appear at all.

Interest/anti-interest entries may be scoped to specific sources (see
`InterestEntry.sources`). Every entry is embedded once regardless -- the
per-item weighted-max only considers the subset of entries applicable to
that item's source, so scoping costs no extra encoding work.

Uses BAAI/bge-base-en-v1.5, which distinguishes "query" (the interest,
what we're searching for) from "passage" (the story title) -- only the
query side gets the retrieval instruction prefix.
"""

from __future__ import annotations

from news.config import InterestProfile, source_matches
from news.logging_setup import logger
from news.models import RawItem, ScoredItem

EMBEDDING_MODEL_NAME = "BAAI/bge-base-en-v1.5"
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class EmbeddingRanker:
    def __init__(self, model_name: str = EMBEDDING_MODEL_NAME) -> None:
        # Imported lazily: sentence-transformers/torch are slow to import
        # and not needed for e.g. `--help` or fetch-only runs.
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model {}", model_name)
        self.model = SentenceTransformer(model_name)
        self.model_name = model_name
        logger.debug("Embedding model loaded on device {}", self.model.device)

    def score(self, items: list[RawItem], profile: InterestProfile) -> list[ScoredItem]:
        from sentence_transformers.util import cos_sim

        if not items:
            return []

        interest_texts = [QUERY_INSTRUCTION + i.text for i in profile.interests]
        interest_weights = [i.weight for i in profile.interests]
        anti_texts = [QUERY_INSTRUCTION + i.text for i in profile.anti_interests]
        anti_weights = [i.weight for i in profile.anti_interests]

        logger.info(
            "Embedding {} interests, {} anti-interests, and {} candidate titles",
            len(interest_texts),
            len(anti_texts),
            len(items),
        )
        interest_vecs = self.model.encode(interest_texts, normalize_embeddings=True) if interest_texts else None
        anti_vecs = self.model.encode(anti_texts, normalize_embeddings=True) if anti_texts else None
        title_vecs = self.model.encode(
            [item.title for item in items],
            normalize_embeddings=True,
            show_progress_bar=True,
        )

        sims = cos_sim(title_vecs, interest_vecs) if interest_vecs is not None else None  # [n_items, n_interests]
        anti_sims = cos_sim(title_vecs, anti_vecs) if anti_vecs is not None else None  # [n_items, n_anti]

        scored: list[ScoredItem] = []
        for idx, item in enumerate(items):
            pos_score, matches = _weighted_max(
                sims[idx] if sims is not None else None, profile.interests, interest_weights, item.source
            )
            anti_score, anti_matches = _weighted_max(
                anti_sims[idx] if anti_sims is not None else None, profile.anti_interests, anti_weights, item.source
            )

            scored.append(
                ScoredItem(
                    **item.model_dump(),
                    embedding_score=round(pos_score - anti_score, 4),
                    embedding_matches=matches,
                    embedding_anti_score=round(anti_score, 4),
                    embedding_anti_matches=anti_matches,
                )
            )

        scored.sort(key=lambda s: s.embedding_score, reverse=True)
        logger.info("Stage 1 (embedding) scoring complete")
        return scored


def _weighted_max(
    item_sims,  # 1D tensor of cosine similarities, one per entry, or None
    entries,
    weights: list[float],
    source: str,
) -> tuple[float, dict[str, float]]:
    """Weighted-max similarity over the entries applicable to `source`,
    plus the raw per-entry similarities (for debugging/display)."""
    if item_sims is None:
        return 0.0, {}

    applicable = [i for i, entry in enumerate(entries) if source_matches(source, entry.sources)]
    if not applicable:
        return 0.0, {}

    best_score = max(float(item_sims[i]) * weights[i] for i in applicable)
    matches = {entries[i].text: round(float(item_sims[i]), 4) for i in applicable}
    return best_score, matches
