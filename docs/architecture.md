# Architecture

## Module layout

```
src/news/
  models.py           Normalized data models (RawItem, ScoredItem, ...)
  config.py            Interest profile loading/validation
  logging_setup.py      loguru file+console setup
  storage.py            Disk layout: raw cache, run outputs
  pipeline.py            Orchestrates fetch -> filter -> rank -> output
  cli.py                 `uv run news` entry point

  sources/
    base.py             NewsSource protocol
    hackernews.py        Algolia HN Search API implementation

  filters/
    hard.py             Stage 0: dedup, min score, blacklist

  rank/
    embed.py            Stage 1: embedding similarity scoring
    llm_rerank.py         Stage 2: local LLM reranking (Ollama)
```

## Design principles

**Sources are dumb.** A `NewsSource` only translates its native API into
`RawItem`s for a given time window -- no filesystem access, no filtering.
This keeps sources trivial to add and unit-test in isolation, and means
the rest of the pipeline never needs to know which source an item came
from.

**Everything downstream is source-agnostic.** Filtering and ranking
operate purely on `RawItem`/`ScoredItem`, never on source-specific fields.
Adding a second source (RSS, Reddit, ...) means writing one new file and
adding one line to `pipeline._fetch_all`.

**Cheap-to-expensive funnel.** Stage 0 (hard filters) and stage 1
(embeddings) are cheap enough to run over every candidate. Stage 2 (local
LLM judgment) is the expensive step, so it's deliberately restricted to a
shortlist (default: top 40 by embedding score). This is what makes running
a 9B local model for reranking practical on a laptop for a daily batch job.

**Anti-interests are a soft counterweight, not a filter.** `anti_interests`
in `interests.yaml` mirror `interests` (text + weight) but subtract: stage 1
deducts their weighted-max embedding similarity from the score, and stage 2
shows them to the LLM as "less interested in" context. Unlike `blacklist`,
a story can still surface if it matches a real interest strongly enough.

**Raw fetch is cached per calendar day, independent of ranking runs.**
Raw HN data for a given day doesn't change once fetched; your interest
profile and ranking logic will. Separating them means you can re-run
ranking against the same day's data with a different profile/model without
re-fetching, which is also what makes before/after algorithm comparisons
possible later (see `docs/data_layout.md`).

## Known simplifications (v1)

These were deliberately left out of the first working version -- flagged
here so they're easy to revisit rather than silently forgotten:

- **No comment fetching.** Ranking currently runs on title (+ self-text
  for text posts) only. Fetching a story's comment tree (via Algolia's
  `items/{id}` endpoint) for shortlisted candidates would be a natural
  stage-1.5 addition if comment content turns out to matter for ranking.
- **Cache freshness isn't validated beyond "file exists for today".** If
  you run twice in one day expecting new stories, use `--force-fetch`;
  there's no automatic staleness check.
- **Single source.** Only HackerNews is implemented. The `NewsSource`
  protocol exists specifically so more can be added without touching
  filtering/ranking/storage.

## Gotcha: reasoning models and `think=False`

`qwen3.5` (like Qwen3 before it) is a hybrid model that reasons at length
by default, even for trivial prompts -- in testing, a one-line scoring
task took 40+ seconds and, on the longer real prompts used here, could
burn through the entire context window on internal deliberation before
ever emitting the requested JSON, producing empty responses. `llm_rerank.py`
explicitly passes `think=False` to Ollama's chat call to avoid this. If you
swap in a different reasoning-capable model, check whether it needs the
same treatment (or a larger `num_ctx`) before assuming it's just slow.
