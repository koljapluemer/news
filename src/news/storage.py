"""Disk layout for raw fetches and pipeline runs.

data/
  raw/<source>/<date>/items.jsonl   -- one fetch per source per calendar day,
                                        cached so re-running the same day
                                        doesn't hit the API again
  raw/<source>/<date>/manifest.json -- window + fetch metadata
  runs/<run_id>/config.json         -- interest profile snapshot for the run
  runs/<run_id>/scored.jsonl        -- every candidate that survived stage 0,
                                        with stage-1/stage-2 scores attached
  runs/<run_id>/top10.json          -- final ranked output
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from news.logging_setup import logger
from news.models import FeedEntry, RawItem, RunOutput, ScoredItem


def raw_dir(data_dir: Path, source: str, day: str) -> Path:
    return data_dir / "raw" / source / day


def load_cached_raw(data_dir: Path, source: str, day: str) -> list[RawItem] | None:
    items_path = raw_dir(data_dir, source, day) / "items.jsonl"
    if not items_path.exists():
        return None
    items = [
        RawItem.model_validate_json(line)
        for line in items_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    logger.info("Loaded {} cached {} items from {}", len(items), source, items_path)
    return items


def save_raw(
    data_dir: Path,
    source: str,
    day: str,
    items: list[RawItem],
    window_start: datetime,
    window_end: datetime,
) -> None:
    out_dir = raw_dir(data_dir, source, day)
    out_dir.mkdir(parents=True, exist_ok=True)

    items_path = out_dir / "items.jsonl"
    with items_path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(item.model_dump_json())
            f.write("\n")

    manifest = {
        "source": source,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "item_count": len(items),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    logger.info("Saved {} raw {} items to {}", len(items), source, items_path)


def new_run_dir(data_dir: Path, run_id: str) -> Path:
    run_dir = data_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def save_run_config(run_dir: Path, profile_dict: dict) -> None:
    (run_dir / "config.json").write_text(
        json.dumps(profile_dict, indent=2), encoding="utf-8"
    )


def save_scored(run_dir: Path, items: list[ScoredItem]) -> None:
    with (run_dir / "scored.jsonl").open("w", encoding="utf-8") as f:
        for item in items:
            f.write(item.model_dump_json())
            f.write("\n")


def save_run_output(run_dir: Path, output: RunOutput) -> Path:
    out_path = run_dir / "top10.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return out_path


def save_latest_pointer(data_dir: Path, output: RunOutput) -> Path:
    """Convenience copy of the most recent run's output at a fixed path,
    so a future UI/consumer doesn't need to know run ids."""
    latest_path = data_dir / "latest.json"
    latest_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return latest_path


def feed_path(data_dir: Path) -> Path:
    return data_dir / "feed.jsonl"


def load_feed(data_dir: Path) -> dict[str, FeedEntry]:
    """Loads the persistent feed, keyed by item id. Missing file (first
    run) or a line that fails to parse -- treated as "not there yet" rather
    than fatal, same policy as `load_cached_raw`."""
    path = feed_path(data_dir)
    if not path.exists():
        return {}
    entries: dict[str, FeedEntry] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = FeedEntry.model_validate_json(line)
        entries[entry.id] = entry
    return entries


def upsert_feed(data_dir: Path, items: list[ScoredItem], surfaced_at: datetime) -> Path:
    """Merges this run's top-N items into the persistent, ever-growing
    `feed.jsonl`, keyed by item id. An item already in the feed (e.g. still
    ranking in today's top 10) has its data refreshed and `surfaced_at`
    bumped to `surfaced_at` -- nothing is ever pruned, so the file only
    grows; an item that stops reappearing just sinks toward the bottom
    once the whole thing is re-sorted by `surfaced_at` descending.

    Written via temp file + rename so a reader (the Flutter app) polling
    the file never sees a half-written line.

    `checked_off`/`thumbs_down` are set by the Flutter app, never by a
    pipeline run -- an item re-surfacing here carries its existing flags
    forward rather than resetting them."""
    entries = load_feed(data_dir)
    for item in items:
        existing = entries.get(item.id)
        entries[item.id] = FeedEntry(
            id=item.id,
            source=item.source,
            title=item.title,
            url=item.url,
            domain=item.domain,
            discussion_url=item.discussion_url,
            points=item.points,
            num_comments=item.num_comments,
            created_at=item.created_at,
            final_score=item.final_score,
            surfaced_at=surfaced_at,
            checked_off=existing.checked_off if existing else False,
            thumbs_down=existing.thumbs_down if existing else False,
        )

    ordered = sorted(entries.values(), key=lambda e: e.surfaced_at, reverse=True)

    path = feed_path(data_dir)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for entry in ordered:
            f.write(entry.model_dump_json())
            f.write("\n")
    tmp.replace(path)

    logger.info("Upserted {} items into feed.jsonl ({} total)", len(items), len(ordered))
    return path
