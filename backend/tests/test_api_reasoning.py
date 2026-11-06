"""API clients and reasoning: reasoning_effort is honoured and the hub can set a default."""
from types import SimpleNamespace

from app.services import llm


def model(parser="qwen3", backend="vllm"):
    return SimpleNamespace(backend=backend, runtime_config=SimpleNamespace(params={"reasoning_parser": parser} if parser else {}))


def kwargs(body):
    return (body.get("chat_template_kwargs") or {}).get("enable_thinking", "unset")


def test_reasoning_effort_none_or_minimal_turns_thinking_off_and_others_on():
    for e in ("none", "minimal", "NONE", "off"):
        assert kwargs(llm.apply_reasoning_defaults(model(), {"reasoning_effort": e})) is False, e
    for e in ("low", "medium", "high", "xhigh"):
        assert kwargs(llm.apply_reasoning_defaults(model(), {"reasoning_effort": e})) is True, e
    assert kwargs(llm.apply_reasoning_defaults(model(), {"reasoning": {"effort": "minimal"}})) is False


def test_without_a_request_the_hub_default_decides_and_auto_changes_nothing():
    b = {"messages": []}
    assert llm.apply_reasoning_defaults(model(), b, "auto") is b
    assert kwargs(llm.apply_reasoning_defaults(model(), b, "off")) is False
    assert kwargs(llm.apply_reasoning_defaults(model(), b, "on")) is True


def test_an_explicit_client_choice_always_wins_and_the_effort_beats_the_default():
    b = {"chat_template_kwargs": {"enable_thinking": True}, "reasoning_effort": "none"}
    assert kwargs(llm.apply_reasoning_defaults(model(), b, "off")) is True
    assert kwargs(llm.apply_reasoning_defaults(model(), {"reasoning_effort": "high"}, "off")) is True


def test_models_without_a_reasoning_parser_or_not_on_vllm_are_left_alone():
    b = {"reasoning_effort": "none"}
    assert llm.apply_reasoning_defaults(model(parser=None), b, "off") is b
    assert llm.apply_reasoning_defaults(model(backend="ollama"), b, "off") is b
    assert llm.apply_reasoning_defaults(model(), {"reasoning_effort": "weird"}, "auto") == {"reasoning_effort": "weird"}


def test_the_original_request_is_not_modified_and_other_template_kwargs_survive():
    b = {"reasoning_effort": "none", "chat_template_kwargs": {"foo": 1}}
    out = llm.apply_reasoning_defaults(model(), b)
    assert out["chat_template_kwargs"] == {"foo": 1, "enable_thinking": False} and b["chat_template_kwargs"] == {"foo": 1}
