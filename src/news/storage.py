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
from news.models import RawItem, RunOutput, ScoredItem


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
