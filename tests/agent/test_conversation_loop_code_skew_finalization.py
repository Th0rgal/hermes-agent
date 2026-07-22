"""Regression coverage for the early code-skew exit in the turn loop."""

from types import SimpleNamespace

from agent import conversation_loop


def test_code_skew_exit_can_finalize_before_first_api_call(monkeypatch):
    """The guard runs before the former function-local finalizer import."""

    context = SimpleNamespace(
        user_message="morning report",
        original_user_message="morning report",
        messages=[{"role": "user", "content": "morning report"}],
        conversation_history=[],
        active_system_prompt="system",
        effective_task_id="task-1",
        turn_id="turn-1",
        current_turn_user_idx=0,
        should_review_memory=False,
        plugin_user_context=None,
        ext_prefetch_cache=None,
    )
    monkeypatch.setattr(
        conversation_loop,
        "build_turn_context",
        lambda *args, **kwargs: context,
    )

    finalized = {}

    def fake_finalize_turn(agent, **kwargs):
        finalized.update(kwargs)
        return {"final_response": kwargs["final_response"], "failed": kwargs["failed"]}

    monkeypatch.setattr(conversation_loop, "finalize_turn", fake_finalize_turn)

    agent = SimpleNamespace(
        api_mode="openai",
        max_iterations=5,
        iteration_budget=SimpleNamespace(remaining=5),
        _budget_grace_call=False,
        _check_code_skew_before_turn=lambda: "boot abc123 != checkout def456",
    )

    result = conversation_loop.run_conversation(agent, "morning report")

    assert result["failed"] is True
    assert "restart the application" in result["final_response"]
    assert finalized["api_call_count"] == 0
    assert finalized["_turn_exit_reason"] == "code_skew_detected"
    assert finalized["messages"][-1]["role"] == "assistant"
