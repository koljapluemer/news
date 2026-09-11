"""Central logging setup: detailed rotating file log + tqdm-safe console log."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from tqdm import tqdm


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    # Console: concise, routed through tqdm.write so it doesn't tear
    # progress bars apart.
    logger.add(
        lambda msg: tqdm.write(msg, end=""),
        level="INFO",
        colorize=True,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    )

    # File: full detail, rotated daily, kept for a couple weeks.
    logger.add(
        log_dir / "news_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="14 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
    )

    logger.debug("Logging configured. Writing detailed logs to {}", log_dir)


__all__ = ["configure_logging", "logger"]
