"""Stage 1: cheap embedding-based scoring of every surviving candidate.

Each interest is embedded as its own sentence (rather than one blended
profile vector) so distinct topics don't dilute each other. A candidate's
score is the weighted-max cosine similarity across interests, which lets a
strong match on *any one* topic surface the item, rather than requiring
broad relevance to the whole profile.

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

        logger.info("Embedding {} interests and {} candidate titles", len(interest_texts), len(items))
        interest_vecs = self.model.encode(interest_texts, normalize_embeddings=True)
        title_vecs = self.model.encode(
            [item.title for item in items],
            normalize_embeddings=True,
            show_progress_bar=True,
        )

        sims = cos_sim(title_vecs, interest_vecs)  # [n_items, n_interests]

        scored: list[ScoredItem] = []
        for item, item_sims in zip(items, sims):
            weighted = [
                float(sim) * weight for sim, weight in zip(item_sims, interest_weights)
            ]
            best_idx = max(range(len(weighted)), key=lambda i: weighted[i]) if weighted else None
            embedding_score = weighted[best_idx] if best_idx is not None else 0.0
            matches = {
                profile.interests[i].text: round(float(item_sims[i]), 4)
                for i in range(len(profile.interests))
            }
            scored.append(
                ScoredItem(
                    **item.model_dump(),
                    embedding_score=round(embedding_score, 4),
                    embedding_matches=matches,
                )
            )

        scored.sort(key=lambda s: s.embedding_score, reverse=True)
        logger.info("Stage 1 (embedding) scoring complete")
        return scored
