"""Logging regression tests.

The double-render bug this guards against: if structlog's own chain ends in a
renderer *and* the stdlib ProcessorFormatter also renders, every line is
emitted twice in one record. It reads as noise rather than as an error, so it
survives review easily — hence a test.
"""

import json
import logging

import pytest

from backend.logging_config import configure_logging, get_logger, request_id_var


@pytest.fixture(autouse=True)
def _restore_logging():
    yield
    configure_logging("WARNING", json_output=False)


def test_each_event_is_rendered_once(capsys):
    configure_logging("INFO", json_output=False)
    get_logger("backend.test").info("something_happened", thing="value")

    line = capsys.readouterr().out.strip()
    assert line.count("something_happened") == 1
    assert line.count("thing=value") == 1


def test_json_output_is_a_single_valid_object(capsys):
    configure_logging("INFO", json_output=True)
    get_logger("backend.test").info("json_event", user_id="u-1")

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1

    payload = json.loads(out[0])
    assert payload["event"] == "json_event"
    assert payload["user_id"] == "u-1"
    assert payload["level"] == "info"


def test_request_id_is_attached_when_set(capsys):
    configure_logging("INFO", json_output=True)
    token = request_id_var.set("req-xyz")
    try:
        get_logger("backend.test").info("with_request_id")
    finally:
        request_id_var.reset(token)

    assert json.loads(capsys.readouterr().out.strip())["request_id"] == "req-xyz"


def test_request_id_is_absent_when_unset(capsys):
    configure_logging("INFO", json_output=True)
    get_logger("backend.test").info("no_request_id")

    assert "request_id" not in json.loads(capsys.readouterr().out.strip())


def test_stdlib_loggers_share_the_same_format(capsys):
    """uvicorn's records must come out as one stream with the app's, not two."""
    configure_logging("INFO", json_output=True)
    logging.getLogger("uvicorn.access").info("GET /api/health 200")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["event"] == "GET /api/health 200"
    assert payload["logger"] == "uvicorn.access"
