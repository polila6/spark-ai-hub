"""The director: what a message is offered before it is answered."""
from types import SimpleNamespace

from app.services import director as D

ALL = {"web", "skills", "agents", "code", "research"}


def ask(text, level=2, allow=None, available=ALL, **kw):
    return D.Ask(content=text, level=level, allow=allow or {}, available=set(available), **kw)


def boom(_msgs):
    raise AssertionError("the model must not be asked")


def test_a_greeting_gets_no_tools_and_no_reasoning():
    d = D.decide(ask("Hola, gracias"), boom)
    assert not d.think and d.tools() == [] and d.source == "rules"


def test_a_short_plain_question_is_answered_directly_at_the_fast_levels():
    d = D.decide(ask("¿Cuál es la capital de Portugal?", level=1), boom)
    assert not d.think and d.tools() == [] and d.source == "rules"


def test_the_balanced_level_asks_the_model_about_a_plain_question_and_it_says_nothing_is_needed():
    d = D.decide(ask("¿Cuál es la capital de Portugal?"), lambda _: '{"think":false,"web":false,"code":false,"skills":false,"agents":false,"research":false,"reason":"conocimiento general"}')
    assert d.source == "llm" and not d.think and d.tools() == [] and d.reason == "conocimiento general"


def test_a_url_or_current_facts_turn_on_the_web_only():
    assert D.decide(ask("Resume https://example.org/paper.pdf"), boom).tools() == ["web"]
    d = D.decide(ask("Busca las últimas noticias sobre Kimodo"), boom)
    assert d.tools() == ["web"] and not d.think


def test_code_and_data_turn_on_code():
    assert "code" in D.decide(ask("Haz una gráfica con pandas de este csv"), boom).tools()
    assert "code" in D.decide(ask("Analiza esto", attachments=["ventas.xlsx"]), boom).tools()


def test_off_is_never_overridden_and_on_is_never_dropped():
    d = D.decide(ask("Busca las últimas noticias", allow={"web": "off"}), boom)
    assert "web" not in d.tools()
    d = D.decide(ask("Hola", allow={"web": "on", "think": "on"}), boom)
    assert d.web and d.think  # forced, even for a greeting


def test_capabilities_that_do_not_exist_are_never_turned_on():
    d = D.decide(ask("Busca las últimas noticias", available={"code"}), boom)
    assert d.tools() == []


def test_the_instant_level_offers_nothing_by_itself_and_never_asks_the_model():
    d = D.decide(ask("Busca las últimas noticias y razona paso a paso", level=0), boom)
    assert d.tools() == [] and not d.think and d.steps == 3
    assert D.decide(ask("Busca noticias", level=0, allow={"web": "on"}), boom).web


def test_the_fast_level_decides_with_rules_only():
    d = D.decide(ask("Explícame con calma cómo mejorar la organización de mi equipo de investigación", level=1), boom)
    assert not d.think and d.source == "rules" and d.steps == 8


def test_an_unclear_message_is_put_to_the_model_at_the_balanced_level():
    calls = []

    def llm(msgs):
        calls.append(msgs)
        return 'sure: {"think": false, "web": true, "code": false, "skills": false, "agents": false, "research": false, "reason": "actualidad"}'

    d = D.decide(ask("¿Qué tal va el proyecto de la nueva sede del CERN?"), llm)
    assert len(calls) == 1 and d.source == "llm" and d.web and not d.think and not d.code and d.reason == "actualidad"
    assert "Available:" in calls[0][1]["content"] and "CERN" in calls[0][1]["content"]


def test_the_model_cannot_turn_on_what_is_off_or_missing():
    def llm(_):
        return '{"think": true, "web": true, "code": true, "skills": true, "agents": true, "research": true, "reason": "todo"}'

    d = D.decide(ask("Mmm, ¿y qué opinas de la estrategia que propone este documento y de sus consecuencias para el grupo?",
                     allow={"web": "off", "code": "off"}, available={"web", "code", "skills"}), llm)
    assert not d.web and not d.code and not d.agents and not d.research


def test_a_failing_or_unreadable_director_falls_back_to_everything_allowed():
    def broken(_):
        raise RuntimeError("engine down")

    text = "Mmm, ¿y qué opinas de lo que comentamos sobre el grupo?"
    for fn in (broken, lambda _: "no json at all"):
        d = D.decide(ask(text), fn)
        assert d.source == "fallback" and {"web", "skills", "agents", "code"} <= set(d.tools()) and "research" not in d.tools()


def test_no_model_to_ask_also_falls_back():
    d = D.decide(ask("Mmm, ¿y qué opinas de lo que comentamos sobre el grupo?"), None)
    assert d.source == "fallback" and d.web


def test_deep_and_maximum_levels_think_unless_it_is_small_talk():
    q = "Compara dos enfoques para reorganizar el pipeline de datos del laboratorio y dime cuál elegirías."
    assert D.decide(ask(q, level=2), boom).think  # the rules see "compara"
    assert D.decide(ask("Hola", level=4), boom).think is False
    assert D.decide(ask("Dime algo útil sobre cómo escribir un buen resumen para un congreso de física.", level=3), boom).think
    assert not D.decide(ask("Dime algo útil sobre cómo escribir un buen resumen para un congreso de física.", level=1), boom).think


def test_thinking_off_wins_over_every_level():
    assert not D.decide(ask("Demuestra que la raíz de 2 es irracional", level=4, allow={"think": "off"}), boom).think


def test_deep_research_brings_the_web_and_agents_with_it():
    d = D.decide(ask("Investiga a fondo el estado del arte en motion generation", allow={"research": "auto"}), boom)
    assert d.research and d.web and d.agents


def test_in_a_workspace_the_commands_stay_and_the_step_limit_is_not_touched():
    d = D.decide(ask("Hola", level=0, workspace=True), boom)
    assert d.code and d.steps is None
    assert not D.decide(ask("Hola", workspace=True, allow={"code": "off"}), boom).code


def test_without_a_director_everything_allowed_is_offered_as_before():
    d = D.decide(ask("Hola", director=False), boom)
    assert {"web", "skills", "agents", "code"} <= set(d.tools()) and "research" not in d.tools() and d.source == "fixed"


def test_old_clients_keep_working():
    a = D.normalize_allow(None, {"thinking": True, "web": False, "research": True})
    assert a["think"] == "on" and a["web"] == "off" and a["research"] == "on"
    assert D.normalize_allow({"think": "off", "bogus": "on", "web": "maybe"}, None)["think"] == "off"
    assert D.normalize_allow({"web": "maybe"}, None)["web"] == "auto"


def test_the_public_decision_is_small_and_readable():
    p = D.decide(ask("Hola"), boom).public()
    assert set(p) == {"think", "tools", "level", "source", "reason", "ms", "steps"} and p["reason"]


# ------------------------------------------------------------------ inside the agent loop


def _runner(monkeypatch, content, backend="vllm", **turn_kw):
    from app.services import agent

    monkeypatch.setattr(agent, "get_setting", lambda db, k: True)
    monkeypatch.setattr(agent, "prior_messages", lambda *a, **k: [])
    t = agent.Turn(user_id="u", conversation_id="c", model_id="m", content=content, director=True, **turn_kw)
    events = []
    r = agent.Runner(t, events.append, lambda: False)
    r.flags = {"web": True, "skills": True, "agents": True, "auto_install": False, "code": True}
    r._director_complete = lambda model, msgs: boom(msgs)
    return r, SimpleNamespace(backend=backend), events


def test_the_runner_narrows_the_flags_and_the_thinking(monkeypatch):
    r, model, events = _runner(monkeypatch, "Hola, buenas", thinking=True, allow={"think": "auto"})
    d = r._decide(SimpleNamespace(commit=lambda: None), SimpleNamespace(id="c"), model, [], False)
    assert not any(r.flags[k] for k in ("web", "skills", "agents", "code")) and r.t.thinking is False
    assert events[0]["type"] == "director" and events[0]["tools"] == [] and d.source == "rules"


def test_the_runner_keeps_what_the_message_needs(monkeypatch):
    r, model, events = _runner(monkeypatch, "Busca las últimas noticias sobre vLLM")
    r._decide(SimpleNamespace(commit=lambda: None), SimpleNamespace(id="c"), model, [], False)
    assert r.flags["web"] and not r.flags["code"] and r.max_steps >= 1


def test_a_model_without_thinking_support_is_left_alone(monkeypatch):
    r, model, _ = _runner(monkeypatch, "Hola", backend="ollama", thinking=None)
    r._decide(SimpleNamespace(commit=lambda: None), SimpleNamespace(id="c"), model, [], False)
    assert r.t.thinking is None


def test_image_mode_skips_the_director(monkeypatch):
    r, model, events = _runner(monkeypatch, "un gato", mode="image")
    assert r._decide(SimpleNamespace(commit=lambda: None), SimpleNamespace(id="c"), model, [], False) is None and events == []


def test_a_yes_after_a_question_is_an_answer_not_small_talk():
    d = D.decide(ask("sí", previous="¿Quieres que busque las novedades en la web?"), lambda _: '{"think":false,"web":true,"code":false,"skills":false,"agents":false,"research":false,"reason":"confirma buscar"}')
    assert d.source == "llm" and d.web  # it asked the model, with the previous message as context
    assert D.decide(ask("sí"), boom).tools() == []  # with nothing before it, it is just a reply
    assert D.decide(ask("Hola", previous="…"), boom).tools() == []  # greetings stay small talk


def test_a_workspace_task_keeps_its_commands_skills_and_agents():
    d = D.decide(ask("continúa", workspace=True, level=1), boom)
    assert {"code", "skills", "agents"} <= set(d.tools()) and d.steps is None


def test_forcing_a_capability_lifts_the_step_cap():
    assert D.decide(ask("Busca noticias", level=0, allow={"web": "on"}), boom).steps is None
    assert D.decide(ask("Hola", level=0), boom).steps == 3


def test_without_a_director_the_models_own_thinking_default_is_left_alone(monkeypatch):
    r, model, _ = _runner(monkeypatch, "Hola", thinking=None)
    r.t.director = False
    r._decide(SimpleNamespace(commit=lambda: None), SimpleNamespace(id="c"), model, [], False)
    assert r.t.thinking is None
