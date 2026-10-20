import json
import logging
import sys

from claims_agent.log_config import JsonFormatter


def record(**extra) -> logging.LogRecord:
    return logging.makeLogRecord(
        {"name": "claims", "levelno": logging.WARNING, "levelname": "WARNING", "msg": "hola %s",
         "args": ("María",), "created": 0.0} | extra
    )  # fmt: skip


def test_one_json_object_with_the_rendered_message_and_extras():
    entry = json.loads(JsonFormatter().format(record(conversation_id="abc", color_message="x")))

    assert entry == {
        "time": "1970-01-01T00:00:00+00:00",
        "level": "WARNING",
        "logger": "claims",
        "message": "hola María",
        "conversation_id": "abc",
    }


def test_exceptions_are_included():
    try:
        raise ValueError("boom")
    except ValueError:
        entry = json.loads(JsonFormatter().format(record(exc_info=sys.exc_info())))

    assert "ValueError: boom" in entry["exception"]
