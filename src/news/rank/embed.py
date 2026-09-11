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

Uses BAAI/bge-base-en-v1.5, which distinguishes "query" (the interest,
what we're searching for) from "passage" (the story title) -- only the
query side gets the retrieval instruction prefix.
"""

from __future__ import annotations

from news.config import InterestProfile
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
        interest_vecs = self.model.encode(interest_texts, normalize_embeddings=True)
        anti_vecs = self.model.encode(anti_texts, normalize_embeddings=True) if anti_texts else None
        title_vecs = self.model.encode(
            [item.title for item in items],
            normalize_embeddings=True,
            show_progress_bar=True,
        )

        sims = cos_sim(title_vecs, interest_vecs)  # [n_items, n_interests]
        anti_sims = cos_sim(title_vecs, anti_vecs) if anti_vecs is not None else None  # [n_items, n_anti]

        scored: list[ScoredItem] = []
        for idx, (item, item_sims) in enumerate(zip(items, sims)):
            weighted = [
                float(sim) * weight for sim, weight in zip(item_sims, interest_weights)
            ]
            best_idx = max(range(len(weighted)), key=lambda i: weighted[i]) if weighted else None
            pos_score = weighted[best_idx] if best_idx is not None else 0.0
            matches = {
                profile.interests[i].text: round(float(item_sims[i]), 4)
                for i in range(len(profile.interests))
            }

            anti_score = 0.0
            anti_matches: dict[str, float] = {}
            if anti_sims is not None:
                item_anti_sims = anti_sims[idx]
                anti_weighted = [
                    float(sim) * weight for sim, weight in zip(item_anti_sims, anti_weights)
                ]
                anti_score = max(anti_weighted) if anti_weighted else 0.0
                anti_matches = {
                    profile.anti_interests[i].text: round(float(item_anti_sims[i]), 4)
                    for i in range(len(profile.anti_interests))
                }

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
