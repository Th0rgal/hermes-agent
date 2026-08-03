"""Session-stream paths must not deliver the raw incomplete sentinel."""

from gateway.platforms.api_server import _present_final_response

SENTINEL = "Codex response remained incomplete after 3 continuation attempts"


def test_sentinel_final_response_is_converted():
    result = {"final_response": SENTINEL, "error": SENTINEL, "partial": True}
    text = _present_final_response(result)
    assert SENTINEL not in text
    assert "hidden reasoning" in text


def test_real_answer_with_sentinel_error_is_delivered():
    result = {"final_response": "Voici le tableau.", "error": SENTINEL}
    assert _present_final_response(result) == "Voici le tableau."


def test_plain_result_passthrough():
    assert _present_final_response({"final_response": "ok"}) == "ok"
    assert _present_final_response("not a dict") == ""
