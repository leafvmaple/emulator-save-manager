"""Logger setup — must not crash in a windowed (no-console) build."""

from __future__ import annotations

import sys

from loguru import logger

from app.logger import setup_logger


def test_setup_logger_survives_no_stderr(tmp_path, monkeypatch):
    """PyInstaller --windowed sets sys.stderr to None; setup must not crash.

    Regression for: TypeError: Cannot log to objects of type 'NoneType'
    (every released --windowed binary failed to launch on a clean machine).
    """
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdout", None)
    try:
        setup_logger(log_dir=tmp_path / "logs")          # must not raise
        logger.info("hello from a windowed build")        # must not raise
        logger.complete()                                 # flush enqueued sink
        assert (tmp_path / "logs" / "app.log").exists()
    finally:
        logger.remove()


def test_setup_logger_with_stderr(tmp_path):
    """The normal path (console present) still attaches both sinks."""
    try:
        setup_logger(log_dir=tmp_path / "logs")
        logger.info("hello with console")
        logger.complete()
        assert (tmp_path / "logs" / "app.log").exists()
    finally:
        logger.remove()
