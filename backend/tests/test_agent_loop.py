"""Run-until-done agent loop: plan handling, "continue" decisions, mid-turn compaction selection and the inbox of
messages sent while a turn runs (pure logic). Run: docker compose run --rm --no-deps backend pytest -q tests"""
import uuid

from app.services.agent import (PLAN_MAX_ITEMS, Turn, announces_work, compaction_split, continue_reason,
                                is_context_error, mechanical_summary, normalize_plan, nudge_message, plan_open,
                                step_groups, turn_transcript)
from app.services.turns import followup_needed, inbox_order


# ------------------------------------------------------------------ plan

def test_normalize_plan_accepts_synonyms_strings_and_json():
    plan = normalize_plan([{"content": "  Escribir   informe.tex ", "status": "done"},
                           {"content": "Compilar", "status": "In Progress"},
                           {"step": "Revisar el PDF"},  # missing status -> pending
                           "Enviar resumen",
                           {"content": "", "status": "pending"},  # empty -> dropped
                           42])
    assert plan == [{"content": "Escribir informe.tex", "status": "completed"},
                    {"content": "Compilar", "status": "in_progress"},
                    {"content": "Revisar el PDF", "status": "pending"},
                    {"content": "Enviar resumen", "status": "pending"}]
    # some tool parsers hand arrays over as a JSON string
    assert normalize_plan('[{"content": "a", "status": "completed"}]') == [{"content": "a", "status": "completed"}]
    assert normalize_plan({"items": [{"content": "b", "status": "weird"}]}) == [{"content": "b", "status": "pending"}]
    assert normalize_plan(None) == [] and normalize_plan("") == []
    assert len(normalize_plan([f"paso {i}" for i in range(100)])) == PLAN_MAX_ITEMS


def test_plan_open_lists_pending_and_in_progress():
    plan = normalize_plan([{"content": "a", "status": "completed"}, {"content": "b", "status": "in_progress"},
                           {"content": "c", "status": "pending"}])
    assert [i["content"] for i in plan_open(plan)] == ["b", "c"]
    assert plan_open([]) == []


# ------------------------------------------------------------------ continue or finish

def test_announced_work_is_detected_but_questions_and_results_are_not():
    assert announces_work("Todo el material está escrito y LaTeX ya está instalado. Voy a compilar.")
    assert announces_work("He corregido el error.\n\nAhora compilo de nuevo el documento")
    assert announces_work("Done with the parser. Let me run the tests.")
    assert not announces_work("¿Quieres que lo compile ahora?")
    assert not announces_work("Listo: el PDF está en salida/informe.pdf (12 páginas).")
    assert not announces_work("Voy a ser breve: la respuesta es 42.\n\nResultado final: 42 unidades.")
    assert not announces_work("")


def test_continue_reason_order_and_limits():
    open_plan = normalize_plan([{"content": "Compilar", "status": "in_progress"}])
    done_plan = normalize_plan([{"content": "Compilar", "status": "completed"}])
    assert continue_reason("Hecho.", open_plan, False, 0, 0, 6) == "plan"
    assert continue_reason("Hecho.", done_plan, True, 0, 0, 6) == "verify"
    assert continue_reason("", done_plan, False, 0, 0, 6) == "empty"  # e.g. a step that only produced reasoning
    assert continue_reason("Voy a compilar.", done_plan, False, 0, 0, 6) == "announced"
    assert continue_reason("Listo, el informe está en informe.pdf.", done_plan, False, 0, 0, 6) is None
    assert continue_reason("Listo.", [], False, 0, 0, 6) is None
    # never more than two reminders in a row, nor more than the per-turn budget
    assert continue_reason("Hecho.", open_plan, False, 2, 2, 6) is None
    assert continue_reason("Hecho.", open_plan, False, 0, 6, 6) is None


def test_nudge_message_lists_the_open_steps():
    plan = normalize_plan([{"content": "Escribir", "status": "completed"}, {"content": "Compilar", "status": "in_progress"},
                           {"content": "Revisar", "status": "pending"}])
    msg = nudge_message("plan", plan, "es")
    assert "Continúa con el plan" in msg and "Compilar" in msg and "Revisar" in msg and "Escribir" not in msg
    assert "completado" in msg  # tells the model how to finish for real
    assert "ws_run" in nudge_message("verify", plan, "es")
    assert nudge_message("announced", plan, "en").startswith("You said")


def test_context_errors_are_recognised():
    assert is_context_error("Engine returned 400: This model's maximum context length is 262144 tokens. However, you requested 270000")
    assert is_context_error("max_tokens must be at least 1, got -5")
    assert not is_context_error("Engine returned 500: CUDA error")


# ------------------------------------------------------------------ mid-turn compaction

def _step(i, size=4000):
    cid = f"c{i}"
    return [{"role": "assistant", "content": f"paso {i}", "tool_calls": [
                {"id": cid, "type": "function", "function": {"name": "ws_run", "arguments": '{"command": "make"}'}}]},
            {"role": "tool", "tool_call_id": cid, "content": "x" * size}]


def _turn(n_steps=8, injected_at=None):
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "antiguo"},
            {"role": "assistant", "content": "respuesta antigua"}, {"role": "user", "content": "haz la tarea"}]
    for i in range(n_steps):
        if i == injected_at:
            msgs.append({"role": "user", "content": "[Mensaje nuevo del usuario mientras trabajabas]: añade tests"})
        msgs += _step(i)
    return msgs


def test_step_groups_keep_tool_results_with_their_call():
    msgs = _turn(3, injected_at=1)
    groups = step_groups(msgs, 4)
    assert [[msgs[i]["role"] for i in g] for g in groups] == [["assistant", "tool"], ["user"], ["assistant", "tool"],
                                                              ["assistant", "tool"]]


def test_compaction_keeps_recent_steps_and_protected_messages():
    msgs = _turn(8, injected_at=2)
    injected = next(i for i, m in enumerate(msgs) if "Mensaje nuevo" in str(m["content"]))
    split = compaction_split(msgs, 4, keep_tokens=3000, ratio=3.2, protected={injected})
    assert split is not None
    assert split["protected"] == [injected]
    assert injected not in split["old"]
    # the old part is made of whole steps and never reaches into the kept tail
    assert all(i < split["tail"] for i in split["old"])
    assert msgs[split["tail"]]["role"] == "assistant"
    kept_groups = step_groups(msgs, split["tail"])
    assert len(kept_groups) >= 2
    old_roles = [msgs[i]["role"] for i in split["old"]]
    assert old_roles.count("assistant") == old_roles.count("tool")
    # system prompt, history and the request are never part of the summarised block
    assert min(split["old"]) >= 4


def test_compaction_needs_something_to_summarise():
    assert compaction_split(_turn(2), 4, keep_tokens=10, ratio=3.2) is None  # only the two most recent steps
    only_users = _turn(0) + [{"role": "user", "content": "a"}, {"role": "user", "content": "b"},
                             {"role": "user", "content": "c"}]
    assert compaction_split(only_users, 4, keep_tokens=1, ratio=3.2) is None


def test_transcript_names_tool_results_and_fallback_summary():
    pieces = turn_transcript(_step(0, size=5000))
    assert pieces[0].startswith("ASISTENTE: paso 0") and "ws_run" in pieces[0]
    assert pieces[1].startswith("RESULTADO de ws_run:") and pieces[1].endswith("[…]")
    summary = mechanical_summary(_step(0) + _step(1))
    assert summary.count("\n") == 3 and "ws_run" in summary


# ------------------------------------------------------------------ messages sent while the turn runs

def test_inbox_order_keeps_arrival_order_without_duplicates():
    assert inbox_order(["12", b"15", "12", "x", None, 20]) == [12, 15, 20]
    assert inbox_order(None) == [] and inbox_order([]) == []


def test_followup_only_for_leftover_messages_of_turns_not_stopped_by_the_user():
    assert followup_needed([31], "done", cancelled=False)
    assert followup_needed([31, 32], "error", cancelled=False)
    assert not followup_needed([31], "done", cancelled=True)  # Stop means stop: the messages stay unanswered
    assert not followup_needed([], "done", cancelled=False)


def test_history_bounds_of_normal_and_follow_up_turns():
    base = dict(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), model_id=uuid.uuid4(), content="x")
    assert Turn(**base).history_bounds() == (None, set())
    assert Turn(**base, user_message_id=40).history_bounds() == (40, set())
    # follow-up answering queued 41 and 43 after the answer 42 of the previous turn: history = up to 43, minus them
    assert Turn(**base, user_message_id=43, history_upto=44, history_exclude=[41, 43]).history_bounds() == (44, {41, 43})
