"""Structured logging.

Console-friendly in development, JSON in production. Every log line carries a
request id so a single request can be followed across the whole handler, and
that same id goes back to the client in `X-Request-ID` — so a user reporting a
failure can hand you something you can actually grep for.
"""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar

import structlog

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def _add_request_id(_logger: object, _name: str, event_dict: dict) -> dict:
    request_id = request_id_var.get()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    shared = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        # Hand the event dict to the stdlib formatter rather than rendering it
        # here — rendering in both places prints every structlog line twice.
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level, logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (uvicorn, sqlalchemy) through the same handler so the
    # output is one coherent stream rather than two competing formats.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    handler.addFilter(_redact_tokens)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers; drop them so lines aren't duplicated.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        stdlib_logger = logging.getLogger(name)
        stdlib_logger.handlers = []
        stdlib_logger.propagate = True


_TOKEN_IN_QUERY = re.compile(r"(token=)[^&\s\"']+")
_REDACTED = r"\g<1>[redacted]"


def _redact_tokens(record: logging.LogRecord) -> bool:
    """Strip `?token=` from anything a log line carries.

    The chat socket authenticates with the session token in its query string,
    because a browser cannot set headers on a WebSocket — and uvicorn logs the
    full path of every request and socket. Unredacted, every opened chat wrote
    a working sign-in token into the host's logs, readable by anyone with log
    access for as long as the token lives.
    """
    if isinstance(record.msg, str) and "token=" in record.msg:
        record.msg = _TOKEN_IN_QUERY.sub(_REDACTED, record.msg)
    if record.args:
        args = record.args if isinstance(record.args, tuple) else (record.args,)
        cleaned = tuple(
            _TOKEN_IN_QUERY.sub(_REDACTED, a) if isinstance(a, str) and "token=" in a else a for a in args
        )
        if cleaned != args:
            record.args = cleaned
    return True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
