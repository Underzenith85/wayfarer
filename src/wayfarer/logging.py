"""Structured, secret-safe logging configuration."""

import logging
from collections.abc import MutableMapping

import structlog

_REDACTED_KEYS = {"authorization", "openai_api_key", "api_key", "token", "secret"}


def redact_secrets(
    _logger: object, _method: str, event: MutableMapping[str, object]
) -> MutableMapping[str, object]:
    for key in list(event):
        if key.lower() in _REDACTED_KEYS:
            event[key] = "[REDACTED]"
    return event


def configure(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            redact_secrets,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )
