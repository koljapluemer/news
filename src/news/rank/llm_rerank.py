"""Stage 2: local LLM reranking of the embedding shortlist.

Only the top-N candidates by embedding score are sent here -- this is the
expensive stage, so it's deliberately restricted to a small shortlist.
The LLM's job is fine-grained judgment (sarcasm, "interesting but not what
I meant", etc.) plus a one-line reason that doubles as a display blurb.

Final ranking is driven by the LLM score; embedding score is only a
tiebreaker. Items outside the shortlist keep llm_score=None and are not
eligible for the final top-N, but remain in scored.jsonl for later eval.

The shortlist itself guarantees each source at least `min_per_source`
slots (subject to the overall `shortlist_size` budget) before filling the
rest by embedding score. Without this, a source whose titles happen to
score higher on average -- for style/phrasing reasons having nothing to do
with true relevance -- can crowd every other source out of this stage
entirely, since stage 1 sorts and cuts the pooled candidate list globally.
"""

from __future__ import annotations

import json

import ollama
from pydantic import BaseModel, ValidationError
from tqdm import tqdm

from news.config import InterestProfile
from news.logging_setup import logger
from news.models import ScoredItem

DEFAULT_MODEL_NAME = "qwen3.5:9b"
MAX_RETRIES = 2
DEFAULT_MIN_SHORTLIST_PER_SOURCE = 5


class _Judgement(BaseModel):
    score: int
    reason: str


def _build_prompt(profile: InterestProfile, item: ScoredItem) -> str:
    interests = profile.interests_for_source(item.source)
    anti_interests = profile.anti_interests_for_source(item.source)

    interests_block = "\n".join(f"- {i.text}" for i in interests)
    anti_section = ""
    if anti_interests:
        anti_block = "\n".join(f"- {i.text}" for i in anti_interests)
        anti_section = (
            "\n\nTopics the reader is less interested in (not a hard "
            "exclude -- weigh these down, but a story can still score well "
            "if it's also strongly about a real interest above):\n"
            f"{anti_block}"
        )
    text_snippet = f'\nSelf-text: "{item.text[:500]}"' if item.text else ""
    return (
        f"You are rating how relevant a {item.source} post is to a reader's "
        "personal interests.\n\n"
        f"Reader's interests:\n{interests_block}"
        f"{anti_section}\n\n"
        f'Story title: "{item.title}"\n'
        f"Story domain: {item.domain or 'n/a'}"
        f"{text_snippet}\n\n"
        "Rate relevance to the reader's interests on a 1-10 integer scale "
        "(10 = squarely on-topic and high value, 1 = irrelevant). "
        "Judge relevance and substance, not general popularity.\n"
        'Respond with ONLY a JSON object: {"score": <1-10 int>, "reason": '
        '"<one short sentence explaining the score>"}'
    )


def _select_shortlist(
    items: list[ScoredItem], shortlist_size: int, min_per_source: int
) -> list[ScoredItem]:
    """Take the top `shortlist_size` items by embedding score, but first
    guarantee each source up to `min_per_source` slots so a source with a
    systematically higher (or just more numerous) score distribution can't
    crowd the others out of stage 2 entirely. `items` is assumed sorted by
    embedding_score descending (embed.py already does this)."""
    if len(items) <= shortlist_size:
        return list(items)

    by_source: dict[str, list[ScoredItem]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)

    selected: list[ScoredItem] = []
    selected_ids: set[str] = set()
    for source in sorted(by_source):
        for item in by_source[source][:min_per_source]:
            if len(selected) >= shortlist_size:
                break
            selected.append(item)
            selected_ids.add(item.id)

    for item in items:
        if len(selected) >= shortlist_size:
            break
        if item.id not in selected_ids:
            selected.append(item)
            selected_ids.add(item.id)

    selected.sort(key=lambda s: s.embedding_score, reverse=True)
    return selected


def _ensure_model_available(model_name: str) -> None:
    try:
        local_models = {m.model for m in ollama.list().models}
    except Exception as exc:  # noqa: BLE001 - surfacing a clear setup error
        raise RuntimeError(
            f"Could not reach the Ollama server. Is it running? ({exc})"
        ) from exc

    if model_name not in local_models:
        raise RuntimeError(
            f"Model '{model_name}' is not pulled in Ollama yet. Run:\n"
            f"    ollama pull {model_name}"
        )


class LLMReranker:
    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        _ensure_model_available(model_name)
        self.model_name = model_name

    def _judge_one(self, profile: InterestProfile, item: ScoredItem) -> _Judgement | None:
        prompt = _build_prompt(profile, item)
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                # think=False: qwen3.5 is a hybrid reasoning model that
                # otherwise burns tens of seconds (and can exhaust num_ctx
                # before emitting an answer) deliberating over a trivial
                # scoring task. Reconsider if swapping in a different model.
                response = ollama.chat(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    format="json",
                    think=False,
                    options={"temperature": 0.0},
                )
                data = json.loads(response.message.content)
                judgement = _Judgement.model_validate(data)
                judgement.score = max(1, min(10, judgement.score))
                return judgement
            except (json.JSONDecodeError, ValidationError) as exc:
                logger.warning(
                    "LLM judgement parse failed for '{}' (attempt {}/{}): {}",
                    item.title,
                    attempt,
                    MAX_RETRIES + 1,
                    exc,
                )
        logger.error("Giving up on LLM judgement for '{}' after {} attempts", item.title, MAX_RETRIES + 1)
        return None

    def rerank(
        self,
        items: list[ScoredItem],
        profile: InterestProfile,
        shortlist_size: int,
        min_shortlist_per_source: int = DEFAULT_MIN_SHORTLIST_PER_SOURCE,
    ) -> list[ScoredItem]:
        shortlist = _select_shortlist(items, shortlist_size, min_shortlist_per_source)
        logger.info(
            "Stage 2 (LLM rerank): judging top {} of {} candidates with {} (min {}/source)",
            len(shortlist),
            len(items),
            self.model_name,
            min_shortlist_per_source,
        )

        for item in tqdm(shortlist, desc="LLM reranking", unit="story"):
            judgement = self._judge_one(profile, item)
            if judgement is not None:
                item.llm_score = float(judgement.score)
                item.llm_reason = judgement.reason
                item.final_score = item.llm_score + item.embedding_score  # tiebreak
            else:
                item.final_score = None

        logger.info("Stage 2 complete")
        return items
