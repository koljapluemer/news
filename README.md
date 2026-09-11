# news

Local-NLP pipeline that fetches recent news, ranks it against a personal
interest profile, and outputs a short daily digest. Runs entirely on-device
(HTTP fetch aside) -- embeddings and the reranking LLM both run locally.

## How it works

Three-stage funnel, cheap to expensive, so the costly stages only ever see
a handful of candidates:

1. **Fetch** -- pull recent items from each source enabled in your profile:
   HackerNews (via the [Algolia HN Search API](https://hn.algolia.com/api)),
   arXiv (per-category RSS digest), and Reddit (per-subreddit Atom feed).
2. **Hard filter** (stage 0) -- drop duplicates, low-score items, and exact
   blacklist term/domain matches. No ML.
3. **Embedding rank** (stage 1) -- score every surviving candidate by
   cosine similarity between its title and each interest sentence in your
   profile (`sentence-transformers`, GPU-accelerated). Cheap enough to run
   on everything.
4. **LLM rerank** (stage 2) -- send only the top-N embedding matches to a
   local LLM (via [Ollama](https://ollama.com)) for a finer relevance
   judgment (1-10 score + one-line reason). This is the expensive stage,
   so it's restricted to a shortlist.

The final top 10 (by LLM score, embedding score as tiebreaker) is written
to `data/runs/<run_id>/top10.json` and mirrored to `data/latest.json`.

See [`docs/architecture.md`](docs/architecture.md) for the module layout
and design rationale, and [`docs/data_layout.md`](docs/data_layout.md) for
the on-disk data format.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and [Ollama](https://ollama.com)
running locally with the reranking model pulled:

```bash
uv sync
ollama pull qwen3.5:9b
```

## Usage

Edit `config/interests.yaml` with your real interests, then:

```bash
uv run news
```

Useful options (see `uv run news --help` for the full list):

```bash
uv run news --hours 24 --top 15          # different window / output size
uv run news --force-fetch                # bypass today's cached raw fetch
uv run news --llm-model qwen3.5:35b-a3b  # swap the reranking model
```

Logs: concise progress to the console, full detail to `logs/news_<date>.log`
(rotated daily, kept 14 days).

## Extending

- **New source**: implement `NewsSource.fetch(window_start, window_end) ->
  list[RawItem]` (see `src/news/sources/hackernews.py`, `arxiv.py`,
  `reddit.py`) and register a factory for it in `pipeline.SOURCE_FACTORIES`.
  Everything downstream (filtering, ranking, storage) is source-agnostic --
  see `docs/architecture.md` for the per-target-instance and
  `has_score`/`min_points` conventions new sources should follow.
- **New pipeline stage**: stages operate on lists of `ScoredItem` and are
  composed in `pipeline.run_pipeline` -- add a module under `src/news/rank/`
  or `src/news/filters/` and call it there.
