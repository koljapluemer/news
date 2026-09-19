# news

Local-NLP pipeline that fetches recent news, ranks it against a personal
interest profile, and outputs a short daily digest. Runs entirely on-device
(HTTP fetch aside) -- embeddings and the reranking LLM both run locally.

## How it works

Three-stage funnel, cheap to expensive, so the costly stages only ever see
a handful of candidates:

1. **Fetch** -- pull recent items from each source enabled in your profile:
   HackerNews (via the [Algolia HN Search API](https://hn.algolia.com/api)),
   arXiv (per-category RSS digest), and Reddit (per-subreddit Atom feeds, plus
   optionally your personal front page).
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
to `data/profiles/<profile>/runs/<run_id>/top10.json` and mirrored to
`data/profiles/<profile>/latest.json`.

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

Optional secrets go in `.env` (copy `.env.example`; gitignored). Notably,
**Reddit needs your private RSS credentials** for reliable fetching -- see
[Reddit setup](#reddit-setup).

### Reddit setup

Reddit heavily rate-limits anonymous RSS (~1 request/minute), and its
official API needs a manually approved app. Sending your account's private
RSS `user` + `feed` values lifts the RSS limit:

1. Log in and open <https://www.reddit.com/prefs/feeds/>.
2. Right-click any feed link there -> "Copy link address". You get
   `https://www.reddit.com/.rss?feed=<token>&user=<username>`.
3. Put both values in `.env`: `REDDIT_RSS_USER=<username>` and
   `REDDIT_RSS_FEED=<token>`. The token is a credential -- don't commit or
   paste it (regenerate it on the same page if it leaks).
4. Optionally set `sources.reddit.front_page: true` in your profile to also
   fetch your personal front page.

Without credentials it still works, in a degraded mode: one combined request
for all subreddits (busy ones can crowd out quiet ones), and no front page.
Details and trade-offs: `docs/architecture.md`.

## Usage

Edit `config/interests.yaml` with your real interests, then:

```bash
uv run news
```

To keep several interest profiles (e.g. work vs. hobbies), add more YAML
files to `config/` and select one with `--profile` -- a name
(`config/<name>.yaml`) or a path. Each profile gets its own output under
`data/profiles/<name>/`, and the Flutter app has a selector for switching
between them:

```bash
uv run news --profile work               # config/work.yaml
uv run news --profile ~/somewhere/x.yaml # any file; profile name is "x"
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
