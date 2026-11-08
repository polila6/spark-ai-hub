"""Messages sent while the assistant thinks: urgent ones interrupt the running generation and keep what it had reasoned.
Run: docker compose run --rm --no-deps backend pytest -q tests/test_steering.py"""
from types import SimpleNamespace

from app.services import agent as agent_mod
from app.services import turns


class FakeRedis:
    def __init__(self):
        self.kv, self.lists = {}, {}

    def eval(self, script, n, accept, inbox, *args):  # emulates _QUEUE_LUA / _CLOSE_LUA
        if "rpush" in script:
            if accept not in self.kv:
                return 0
            self.lists.setdefault(inbox, []).append(str(args[0]).encode())
            return 1
        items = self.lists.pop(inbox, [])
        if not items or args[0] == "1":
            self.kv.pop(accept, None)
        return items

    def set(self, k, v, ex=None):
        self.kv[k] = v

    def exists(self, k):
        return 1 if k in self.kv else 0

    def delete(self, *ks):
        for k in ks:
            self.kv.pop(k, None)
            self.lists.pop(k, None)

    def pipeline(self, transaction=True):
        outer = self

        class P:
            def __init__(self):
                self.ops = []

            def lrange(self, k, a, b):
                self.ops.append(("l", k))

            def delete(self, k):
                self.ops.append(("d", k))

            def execute(self):
                out = []
                for op, k in self.ops:
                    if op == "l":
                        out.append(list(outer.lists.get(k, [])))
                    else:
                        outer.delete(k)
                        out.append(1)
                return out
        return P()


def _turn_open(r, cid="c1"):
    r.kv[f"sah:turn:{cid}:accept"] = "1"  # as turns.start() leaves it


def test_urgent_message_sets_the_steer_flag_and_taking_it_clears_it(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(turns, "get_redis", lambda: r)
    key = turns._k("c1", ":accept")
    r.kv[key] = "1"
    assert turns.queue_message("c1", 5) and not turns.steer_pending("c1")  # a normal message waits for the step to end
    assert turns.queue_message("c1", 6, urgent=True) and turns.steer_pending("c1")
    assert turns.take_inbox("c1") == [5, 6] and not turns.steer_pending("c1")


def test_closing_the_inbox_drops_a_stale_steer_flag_and_refuses_when_no_turn(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(turns, "get_redis", lambda: r)
    assert not turns.queue_message("c2", 1, urgent=True) and not turns.steer_pending("c2")  # no running turn: no flag
    r.kv[turns._k("c2", ":accept")] = "1"
    turns.queue_message("c2", 1, urgent=True)
    turns.close_inbox("c2", final=True)
    assert not turns.steer_pending("c2")


def _runner(monkeypatch, steer_after):
    r = object.__new__(agent_mod.Runner)
    r.stop, r.emit = (lambda: False), (lambda e: None)
    r.t = SimpleNamespace(conversation_id="c1", params={}, lang="es")
    r.model = SimpleNamespace(backend="ollama")
    r._steered, r._steer_t = False, 0.0
    ticks = {"n": 0}

    def pending(cid):
        ticks["n"] += 1
        return ticks["n"] > steer_after
    monkeypatch.setattr(agent_mod.turns_svc, "steer_pending", pending)
    clock = {"t": 0.0}

    def monotonic():
        clock["t"] += 1.0  # every look at the clock is a second: the 0.3 s throttle never holds the check back
        return clock["t"]
    monkeypatch.setattr(agent_mod, "time", SimpleNamespace(monotonic=monotonic, time=lambda: 0.0))
    monkeypatch.setattr(agent_mod.llm, "sanitize_chat_payload", lambda m, b: b)

    def stream(model, payload, cancel_check=None):
        for i in range(50):
            if cancel_check and cancel_check():
                return
            yield {"choices": [{"delta": {"reasoning_content": f"r{i} ", "content": "" if i < 3 else f"t{i} "}}]}
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "x", "function": {"name": "ws_wr", "arguments": '{"pa'}}]}}]}
    monkeypatch.setattr(agent_mod.llm, "stream_json", stream)
    return r


def test_an_urgent_message_cuts_the_generation_and_keeps_the_partial_reasoning(monkeypatch):
    r = _runner(monkeypatch, steer_after=3)
    got = []
    text, calls, finish = r._llm_step([], None, 100, interruptible=True, on_reasoning=got.append)
    assert r._steered and finish is None
    assert 0 < len(got) < 50 and "".join(got).startswith("r0 r1")  # reasoning so far survives
    assert calls  # the tool call cut halfway is returned raw: the main loop discards it when `_steered` is set


def test_without_interruptible_the_same_generation_runs_to_the_end(monkeypatch):
    r = _runner(monkeypatch, steer_after=0)
    got = []
    r._llm_step([], None, 100, interruptible=False, on_reasoning=got.append)
    assert len(got) == 50 and not r._steered


def test_a_stop_request_wins_over_steering(monkeypatch):
    r = _runner(monkeypatch, steer_after=0)
    r.stop = lambda: True
    r._llm_step([], None, 100, interruptible=True)
    assert not r._steered
