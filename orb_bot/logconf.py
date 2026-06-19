"""Structured JSON logging: structlog rendered through stdlib, daily rotation.

`configure_logging` installs a console handler + a TimedRotatingFileHandler
(`{log_dir}/orb_{YYYY-MM-DD}_{run_id}.log`, rotating at midnight) and returns a
structlog BoundLogger pre-bound with the per-run correlation id. One line per
event, JSON-rendered (spec §17).
"""
from __future__ import annotations

import datetime
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import structlog

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configure_logging(
    level: str,
    log_dir: str,
    run_id: str,
) -> structlog.stdlib.BoundLogger:
    # Fix C: fail fast on unknown log levels — don't silently fall back to INFO.
    if level.upper() not in _VALID_LEVELS:
        raise ValueError(f"unknown log level: {level!r}")
    log_level = getattr(logging, level.upper())

    # Fix A: create log dir owner-only so trade data isn't world-readable.
    Path(log_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    # Fix B: include run_id in filename to prevent same-day run collisions.
    log_path = Path(log_dir) / f"orb_{today}_{run_id}.log"

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=False)
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        timestamper,
    ]

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Fix D: cache_logger_on_first_use=False allows reconfiguration —
        # cached BoundLoggers ignore a later structlog.configure(), which
        # breaks tests and re-init scenarios.
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_path),
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    # Fix A: restrict the active log file to owner-only (trade data is sensitive).
    try:
        os.chmod(str(log_path), 0o600)
    except OSError:
        pass  # exotic filesystems (e.g. FAT, some containers) — best-effort
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(log_level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console)
    root.addHandler(file_handler)

    return structlog.get_logger().bind(run_id=run_id)
