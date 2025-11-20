"""Structured logging configuration."""

from __future__ import annotations

import logging
from typing import Any, Dict

import structlog


def _configure_structlog() -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    structlog.configure(
        processors=[
            timestamper,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO)
    _configure_structlog()


def get_logger(name: str, **kwargs: Dict[str, Any]) -> structlog.stdlib.BoundLogger:
    logger = structlog.get_logger(name)
    if kwargs:
        logger = logger.bind(**kwargs)
    return logger

