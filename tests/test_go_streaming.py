import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "scripts"))
from probe_go_streaming import consume


def line(value):
    return "data: " + json.dumps(value) + "\n"


def test_chat_usage_after_finish_is_kept():
    response, events = consume([
        line({"id":"x","model":"m","choices":[{"delta":{"content":"{"},"index":0}]}),
        line({"choices":[{"delta":{"content":"}"},"finish_reason":"stop","index":0}]}),
        line({"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2}}), "data: [DONE]\n"])
    assert response["choices"][0]["message"]["content"] == "{}"
    assert response["usage"]["prompt_tokens"] == 10


def test_missing_terminal_is_failure():
    with pytest.raises(ValueError,match="finish_reason"):
        consume([line({"choices":[]})])
    with pytest.raises(ValueError,match="terminal"):
        consume([],True)


def test_responses_terminal():
    native = {"status":"completed","output":[],"usage":{"input_tokens":2}}
    result, events = consume([line({"type":"response.completed","response":native})],True)
    assert result == native


def test_stream_wall_deadline():
    with pytest.raises(TimeoutError,match="wall-clock"):
        consume([": keepalive\n"],deadline=-1)
