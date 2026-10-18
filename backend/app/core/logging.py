"""Structured JSON logging.

Every log line is a JSON object carrying the request ID bound by
``RequestContextMiddleware``. Coordinates, place names and free text from
users are never logged.
"""

import logging
import sys

import structlog


def configure_logging(level: str) -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    # Route uvicorn's own loggers through the same level; its access log is
    # replaced by the request middleware's structured line.
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    logging.getLogger("uvicorn.access").disabled = True
    # httpx logs every request at INFO in plain text; our own logs cover it.
    logging.getLogger("httpx").setLevel(logging.WARNING)
