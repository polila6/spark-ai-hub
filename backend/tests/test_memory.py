"""The assistant's memory: what it learns, what it never stores, how it merges, and what leaves with the person."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import Memory
from app.services import memory as M

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def mem(content, source="auto", category="other", importance=3, mentions=1, age_days=0):
    t = NOW - timedelta(days=age_days)
    return Memory(id=uuid.uuid4(), owner_id=uuid.uuid4(), content=content, source=source, category=category, importance=importance,
                  mentions=mentions, created_at=t, updated_at=t)


class FakeDb:
    def __init__(self):
        self.added, self.deleted = [], []

    def add(self, m):
        self.added.append(m)

    def delete(self, m):
        self.deleted.append(m)


def test_credentials_and_identity_numbers_are_never_stored():
    for bad in ["Su contraseña es hunter22", "Usa la api_key sk-abcdefghijklmnopqrstuvwxyz", "Mi DNI es 12345678Z",
                "Tarjeta 4111 1111 1111 1111", "Su IBAN es ES9121000418450200051332", "token " + "ghp" + "_" + "abcdefghijklmnopqrstuv",  # (built in pieces so the repository leak scan has nothing to match)
                "conecta con postgres://admin:s3cretpass@db.example.org/x"]:
        assert M.clean(bad) is None, bad
    assert M.clean("Trabaja con ROS 2 en robots móviles") == "Trabaja con ROS 2 en robots móviles"
    assert M.clean("ok") is None  # too vague to be a fact
    assert len(M.clean("x" * 900 + " fin de la frase larga")) == M.MAX_CONTENT


def test_small_talk_memory_off_and_explicit_remember_are_not_learned_from():
    assert M.worth_learning("Estoy escribiendo mi tesis sobre robots blandos y uso Python", {})
    assert not M.worth_learning("gracias", {})
    assert not M.worth_learning("hola qué tal", {})
    assert not M.worth_learning("Estoy escribiendo mi tesis sobre robots blandos", {"memory_enabled": False})
    assert not M.worth_learning("Estoy escribiendo mi tesis sobre robots blandos", {"memory_auto": False})
    assert not M.worth_learning("Recuerda que mi tesis es sobre robots blandos", {})  # the explicit path already saved it


def test_the_same_fact_in_other_words_is_recognised():
    assert M.similar("Trabaja con ROS 2 en robots móviles", "Trabaja con ROS 2 en robots moviles.")
    assert M.similar("Prefiere respuestas cortas", "Prefiere respuestas cortas y directas")
    assert not M.similar("Trabaja con ROS 2", "Le gusta el café sin azúcar")


def test_the_model_reply_becomes_safe_operations():
    a, b = mem("Vive en Valencia"), mem("Usa Windows")
    known = {str(a.id), str(b.id)}
    reply = json.dumps({"add": [{"content": "Investiga en robótica blanda", "category": "work", "importance": 5},
                                {"content": "su contraseña es abc12345", "category": "profile"},
                                {"content": "Prefiere Python", "category": "inventada", "importance": 99}],
                        "update": [{"id": str(a.id), "content": "Vive en Madrid desde 2026"}, {"id": "no-existe", "content": "Algo que no existe"}],
                        "forget": [str(b.id), "tampoco"]})
    ops = M.parse_ops("Aquí va: " + reply, known)
    assert ops.add == [{"content": "Investiga en robótica blanda", "category": "work", "importance": 5}]  # the secret one dropped, the 4th change over budget
    assert ops.forget == [str(b.id)]
    assert [u["id"] for u in ops.update] == [str(a.id)]
    total = len(ops.add) + len(ops.update) + len(ops.forget)
    assert total <= M.MAX_CHANGES  # never more than three changes per exchange
    assert all("contraseña" not in x["content"] for x in ops.add)
    assert M.parse_ops("sin json", known).empty() and M.parse_ops('{"add": "no es lista"}', known).empty()


def test_categories_and_importance_are_corrected():
    ops = M.parse_ops('{"add":[{"content":"Prefiere Python para todo","category":"inventada","importance":99}]}', set())
    assert ops.add == [{"content": "Prefiere Python para todo", "category": "other", "importance": 5}]


def test_adding_a_known_fact_confirms_it_instead_of_duplicating():
    known = mem("Trabaja con ROS 2 en robots móviles", mentions=2, importance=3)
    existing = [known]
    done = M.apply(FakeDb(), "u", M.Ops(add=[{"content": "Trabaja con ROS 2 en robots móviles", "category": "work", "importance": 3}]), existing)
    assert done == {"added": 0, "updated": 0, "forgotten": 0, "confirmed": 1}
    assert known.mentions == 3 and known.importance == 4  # confirmed three times: it matters more


def test_the_assistant_never_rewrites_or_forgets_what_the_person_wrote():
    mine = mem("Mi tesis trata de robots blandos", source="user")
    imported = mem("Vive en Valencia", source="import")
    learned = mem("Usa Windows", source="auto")
    db = FakeDb()
    done = M.apply(db, "u", M.Ops(update=[{"id": str(mine.id), "content": "Otra cosa distinta", "importance": 1},
                                           {"id": str(learned.id), "content": "Usa Linux desde 2026", "importance": 4}],
                                  forget=[str(mine.id), str(imported.id)]), [mine, imported, learned])
    assert mine.content == "Mi tesis trata de robots blandos" and imported.content == "Vive en Valencia"
    assert db.deleted == [] and done["forgotten"] == 0
    assert learned.content == "Usa Linux desde 2026" and learned.importance == 4 and done["updated"] == 1


def test_new_facts_are_stored_as_learned_with_their_category():
    db, existing = FakeDb(), []
    done = M.apply(db, "u1", M.Ops(add=[{"content": "Investiga en robótica blanda", "category": "work", "importance": 5}]), existing, "c1")
    assert done["added"] == 1 and db.added[0].source == "auto" and db.added[0].category == "work" and db.added[0].importance == 5


def test_beyond_the_cap_the_least_useful_learned_ones_go_first_and_the_persons_own_never():
    own = [mem(f"Hecho propio {i}", source="user", importance=1) for i in range(5)]
    learned = [mem(f"Hecho aprendido {i}", importance=1 + i % 5, age_days=i) for i in range(M.MAX_AUTO + 10)]
    gone = M.evict(own + learned)
    assert len(gone) == 10 and all(g.source == "auto" for g in gone) and all(g not in own for g in gone)
    assert max(g.importance for g in gone) <= 1  # the least important went


def test_the_prompt_lists_the_most_important_first_grouped_by_category():
    ms = [mem("Detalle menor", importance=1, category="context"), mem("Es investigador en robótica", importance=5, category="profile"),
          mem("Prefiere respuestas cortas", importance=4, category="preference")]
    block = M.render_prompt_block(ms, True)
    assert block.index("[Sobre mí]") < block.index("[Cómo me gusta que respondas]") < block.index("[Contexto]")
    assert "- Es investigador en robótica" in block and "[Context]" in M.render_prompt_block(ms, False)
    assert len(M.ranked([mem(f"h{i}") for i in range(200)])) == M.PROMPT_ITEMS


def _user():
    return SimpleNamespace(display_name="Ana", username="ana", preferences={"about_me": "Doctoranda en robótica", "custom_instructions": "Responde corto"})


def test_the_export_carries_everything_and_nothing_secret():
    ms = [mem("Investiga en robótica blanda", category="work", importance=5), mem("Prefiere Python", category="preference", source="user")]
    data = M.export_json(_user(), ms, NOW)
    assert data["format"] == "spark-ai-hub-memory" and data["version"] == 1 and data["profile"]["about_me"] == "Doctoranda en robótica"
    assert [x["content"] for x in data["memories"]][0] == "Investiga en robótica blanda"  # the more important first
    assert set(data["memories"][0]) == {"content", "category", "importance", "mentions", "source", "created_at", "updated_at"}
    assert "owner_id" not in json.dumps(data) and "conversation_id" not in json.dumps(data)
    md = M.export_markdown(_user(), ms, True, NOW)
    assert md.startswith("# Lo que sabe mi asistente sobre Ana") and "## Mi trabajo\n- Investiga en robótica blanda" in md and "Responde corto" in md
    assert "Paste it as" in M.export_markdown(_user(), ms, False, NOW) and "## My work" in M.export_markdown(_user(), ms, False, NOW)


def test_an_export_can_be_imported_again_and_so_can_a_plain_list():
    ms = [mem("Investiga en robótica blanda", category="work", importance=5), mem("Prefiere Python", category="preference")]
    again = M.parse_import(None, M.export_json(_user(), ms, NOW))
    assert [(x["content"], x["category"], x["importance"]) for x in again] == [("Investiga en robótica blanda", "work", 5), ("Prefiere Python", "preference", 3)]
    plain = M.parse_import("Lo que sabe otra IA de mí:\n- Soy ingeniera\n* Vivo en Valencia\n1. Uso Linux\n- ok\nlínea suelta\n- mi contraseña es abc12345", None)
    assert [x["content"] for x in plain] == ["Soy ingeniera", "Vivo en Valencia", "Uso Linux"]  # too short and secret ones are dropped


def test_learning_end_to_end_with_a_stubbed_model(monkeypatch):
    """learn(): reads the exchange, asks the model, applies the changes. Everything else is mocked."""
    from contextlib import contextmanager

    user = SimpleNamespace(id="u1", preferences={})
    model = SimpleNamespace(id="m1", backend="vllm", runtime_config=None)
    msgs = [SimpleNamespace(id=2, role="assistant", content="Claro, te ayudo con ROS 2."),
            SimpleNamespace(id=1, role="user", content="Estoy trabajando en un robot móvil con ROS 2 para mi tesis")]
    seen = {}

    class DB:
        def get(self, cls, key):
            return user if key == "u1" else model

        def scalars(self, q):
            return list(msgs) if "messages" in str(q) else []

        def scalar(self, q):
            return 1

        def expunge(self, m):
            pass

    @contextmanager
    def session():
        yield DB()

    monkeypatch.setattr("app.db.SessionLocal", session)
    monkeypatch.setattr(M, "list_for", lambda db, uid: [])
    monkeypatch.setattr(M, "apply", lambda db, uid, ops, existing, cid=None: seen.setdefault("ops", ops) and {"added": len(ops.add), "updated": 0, "forgotten": 0, "confirmed": 0})
    monkeypatch.setattr(DB, "commit", lambda self: None, raising=False)
    out = M.learn("u1", "c1", "m1", complete=lambda m: '{"add":[{"content":"Hace su tesis con un robot móvil y ROS 2","category":"project","importance":4}]}')
    assert out["added"] == 1 and seen["ops"].add[0]["category"] == "project"
    prompt = M.build_prompt([], "Mi tesis es sobre robots", "ok")
    assert "USER: Mi tesis es sobre robots" in prompt[1]["content"] and "CURRENT MEMORY" in prompt[1]["content"]
