# Data layout

All pipeline data lives under `data/` (gitignored -- throwaway/regeneratable,
but structured so it's useful for debugging and for evaluating ranking
changes over time).

```
data/
  raw/
    hackernews/
      2026-09-11/
        items.jsonl       One JSON RawItem per line, as fetched.
        manifest.json     Fetch window, fetched_at timestamp, item count.
  runs/
    2026-09-11T14-30-05/
      config.json          Snapshot of the interest profile used this run.
      scored.jsonl          Every candidate that survived stage 0, with
                             stage-1 embedding scores and (for shortlisted
                             items) stage-2 LLM scores/reasons attached.
      top10.json            Final output: metadata + top-N ranked items.
  latest.json               Copy of the most recent run's top10.json, at a
                             fixed path for downstream consumers (e.g. a
                             future UI) that don't want to track run ids.
  feed.jsonl                Ever-growing, deduped-by-id history of every
                             item that has ever made a run's top-N, one
                             flattened record per line. The Flutter app
                             (flutter/) reads this directly. See below.
```

## `feed.jsonl`

Unlike everything else under `data/`, this file is never replaced wholesale
-- each run upserts into it. One JSON object per line (newest `surfaced_at`
first):

```json
{
  "id": "hackernews:12345678",
  "source": "hackernews",
  "title": "...",
  "url": "https://...",
  "domain": "example.com",
  "discussion_url": "https://news.ycombinator.com/item?id=12345678",
  "points": 234,
  "num_comments": 89,
  "created_at": "2026-09-11T09:00:00Z",
  "final_score": 9.71,
  "surfaced_at": "2026-09-11T14:30:05Z"
}
```

If an item is still in a later run's top-N, its record is overwritten in
place (fresh score, `surfaced_at` bumped to that run) rather than
duplicated. Nothing is ever deleted from it -- an item that stops
reappearing just sinks toward the bottom once the file is re-sorted by
`surfaced_at`. See `storage.upsert_feed`.

## Why raw and runs are separate

Raw HN data for a given calendar day is immutable once fetched and is
cached there (`raw/<source>/<date>/`) -- re-running the pipeline the same
day reuses it instead of re-hitting the API, unless `--force-fetch` is
passed.

Ranking runs are timestamped and independent of raw data. This is what
lets you replay the same day's raw data through a changed interest profile
or a different reranking model and compare `scored.jsonl` across runs --
the basis for evaluating ranking-algorithm changes later, once there's a
feedback signal to compare against (e.g. which items you actually read).

## Formats

- **JSONL** for item lists (`items.jsonl`, `scored.jsonl`): one record per
  line, streamable, greppable, trivial to load into pandas/polars.
- **Plain JSON** for singletons (`manifest.json`, `config.json`,
  `top10.json`, `latest.json`).

## `top10.json` shape

```json
{
  "meta": {
    "generated_at": "2026-09-11T14:30:05Z",
    "window_hours": 30.0,
    "window_start": "...",
    "window_end": "...",
    "source_candidate_count": 812,
    "hard_filtered_count": 640,
    "shortlisted_count": 40,
    "model_name": "qwen3.5:9b",
    "embedding_model_name": "BAAI/bge-base-en-v1.5"
  },
  "items": [
    {
      "id": "hackernews:12345678",
      "source": "hackernews",
      "title": "...",
      "url": "https://...",
      "domain": "example.com",
      "discussion_url": "https://news.ycombinator.com/item?id=12345678",
      "points": 234,
      "num_comments": 89,
      "created_at": "...",
      "embedding_score": 0.71,
      "embedding_matches": {"interest text": 0.71, "...": 0.3},
      "embedding_anti_score": 0.0,
      "embedding_anti_matches": {"anti-interest text": 0.12},
      "llm_score": 9.0,
      "llm_reason": "Directly about local LLM quantization on consumer GPUs.",
      "final_score": 9.71
    }
  ]
}
```
