"""CLI entry point: `uv run news [OPTIONS]`."""

from __future__ import annotations

from pathlib import Path

import typer

from news.logging_setup import configure_logging, logger
from news.pipeline import PipelineConfig, run_pipeline
from news.rank.llm_rerank import DEFAULT_MIN_SHORTLIST_PER_SOURCE, DEFAULT_MODEL_NAME

app = typer.Typer(add_completion=False)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_PROFILE = "interests"


def resolve_profile(profile: str) -> tuple[str, Path]:
    """Turns the `--profile` value into `(profile name, YAML path)`.

    A bare name ("work") means `config/<name>.yaml`; anything else is
    treated as a path to a YAML file. The name -- the file's stem -- also
    names the per-profile output directory under `data/profiles/`."""
    is_bare_name = Path(profile).name == profile and Path(profile).suffix not in {".yaml", ".yml"}
    if is_bare_name:
        path = CONFIG_DIR / f"{profile}.yaml"
        if not path.exists() and (yml := path.with_suffix(".yml")).exists():
            path = yml
    else:
        path = Path(profile).expanduser()
    if not path.is_file():
        available = sorted(p.stem for p in CONFIG_DIR.glob("*.y*ml"))
        raise typer.BadParameter(
            f"profile file not found: {path} (profiles in {CONFIG_DIR}: {', '.join(available) or 'none'})"
        )
    return path.stem, path


@app.command()
def main(
    hours: float = typer.Option(30.0, help="How far back to fetch stories from."),
    top: int = typer.Option(10, help="Number of ranked items to output."),
    shortlist: int = typer.Option(40, help="Candidates passed to the LLM reranker."),
    min_shortlist_per_source: int = typer.Option(
        DEFAULT_MIN_SHORTLIST_PER_SOURCE,
        help="Guaranteed LLM-shortlist slots per source, before filling the rest by embedding score.",
    ),
    max_per_source: int = typer.Option(4, help="Max final output items from any one source."),
    min_points: int = typer.Option(1, help="Minimum points to keep a story."),
    profile: str = typer.Option(
        DEFAULT_PROFILE,
        "--profile",
        "-p",
        help="Interest profile: a name (config/<name>.yaml) or a path to a profile YAML. "
        "Output goes to <data-dir>/profiles/<name>/.",
    ),
    data_dir: Path = typer.Option(REPO_ROOT / "data", help="Where raw/run data is stored."),
    log_dir: Path = typer.Option(REPO_ROOT / "logs", help="Where log files are written."),
    llm_model: str = typer.Option(DEFAULT_MODEL_NAME, help="Ollama model for stage-2 reranking."),
    embedding_model: str | None = typer.Option(
        None, help="sentence-transformers model for stage-1 scoring. Default: derived from the profile's languages."
    ),
    force_fetch: bool = typer.Option(
        False, help="Refetch even if today's raw data is already cached for a source."
    ),
) -> None:
    """Fetch recent items from every source enabled in your interest profile,
    rank them against your interests, and write the top N to
    data/profiles/<name>/runs/<run_id>/top10.json (and .../latest.json)."""
    profile_name, interests_path = resolve_profile(profile)
    configure_logging(log_dir)

    cfg = PipelineConfig(
        data_dir=data_dir,
        profile_name=profile_name,
        interests_path=interests_path,
        window_hours=hours,
        min_points=min_points,
        shortlist_size=shortlist,
        min_shortlist_per_source=min_shortlist_per_source,
        top_n=top,
        max_per_source=max_per_source,
        embedding_model_name=embedding_model,
        llm_model_name=llm_model,
        force_fetch=force_fetch,
    )

    try:
        out_path = run_pipeline(cfg)
    except Exception:
        logger.exception("Pipeline run failed")
        raise typer.Exit(code=1)

    typer.echo(f"\nDone. Top items written to {out_path}")


if __name__ == "__main__":
    app()
