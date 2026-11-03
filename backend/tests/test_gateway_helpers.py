"""Pure helpers of the OpenAI/Anthropic gateways. Run: docker compose run --rm --no-deps backend pytest -q tests"""
from types import SimpleNamespace

from app.services import llm


def test_system_messages_are_gathered_first():
    msgs = [{"role": "system", "content": "A"}, {"role": "user", "content": "hola"}, {"role": "system", "content": "B"},
            {"role": "assistant", "content": "ok"}, {"role": "developer", "content": [{"type": "text", "text": "C"}]},
            {"role": "user", "content": "hola"}]
    out = llm.system_first(msgs)
    assert out[0] == {"role": "system", "content": "A\n\nB\n\nC"}
    assert [m["role"] for m in out[1:]] == ["user", "assistant", "user"]  # repeated messages are kept


def test_system_first_leaves_normal_conversations_alone():
    msgs = [{"role": "system", "content": "A"}, {"role": "user", "content": "x"}]
    assert llm.system_first(msgs) is msgs
    only_user = [{"role": "user", "content": "x"}]
    assert llm.system_first(only_user) is only_user


def test_responses_payload_keeps_only_function_tools():
    m = SimpleNamespace(backend="vllm", alias="spark-model", engine_ref=None)
    p = llm.responses_payload(m, {"model": "x", "input": "hi", "tool_choice": "auto", "tools": [{"type": "function", "name": "f"}, {"type": "web_search"}]})
    assert p["model"] == "spark-model" and p["tools"] == [{"type": "function", "name": "f"}]
    p = llm.responses_payload(m, {"model": "x", "tool_choice": "auto", "tools": [{"type": "web_search"}]})
    assert "tools" not in p and "tool_choice" not in p


def test_claude_picker_ids_resolve_to_the_spark_model(monkeypatch):
    from app.routers import anthropic_gateway as ag

    monkeypatch.setattr(ag, "get_setting", lambda db, k: "main-model")
    assert ag._map_model(None, "claude-spark-qwen3.8-flash-next-fp4") == "qwen3.8-flash-next-fp4"
    assert ag._map_model(None, "qwen3-8b-fp8") == "qwen3-8b-fp8"
    assert ag._map_model(None, "claude-sonnet-5-5") == "main-model"  # built-in names use the administrator's default
