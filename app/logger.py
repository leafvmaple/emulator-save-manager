"""Loguru logger configuration for the application."""

import sys
from pathlib import Path

from loguru import logger


def setup_logger(log_dir: Path | None = None) -> None:
    """Configure loguru with console and file sinks.

    Parameters
    ----------
    log_dir : Path, optional
        Directory for log files.  Defaults to ``Documents/EmulatorSaveManager/logs``.
    """
    # Remove default handler
    logger.remove()

    # Console handler — coloured, DEBUG level.
    # In a PyInstaller --windowed build there is no console, so sys.stderr is
    # None; adding it would raise "Cannot log to objects of type 'NoneType'"
    # and crash the app at startup. Only attach a console sink when one exists.
    if sys.stderr is not None:
        logger.add(
            sys.stderr,
            level="DEBUG",
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            ),
            colorize=True,
        )

    # File handler — rotating, DEBUG level
    if log_dir is None:
        from app.config import Config
        cfg = Config()
        log_dir = cfg.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    logger.add(
        str(log_file),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {module}:{function}:{line} | {message}",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        enqueue=True,  # thread-safe
    )

    logger.info("Logger initialized — file output: {}", log_file)
