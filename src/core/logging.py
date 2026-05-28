"""结构化日志配置."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        level=log_level,
        stream=sys.stdout,
        format="%(message)s",
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
