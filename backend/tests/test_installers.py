"""Install recipes and the custom-code check. Run like the other unit tests (docker compose run --rm --no-deps backend pytest -q tests)."""
from app.routers.huggingface import _security
from app.services import compat, installers

CFG = {"architectures": ["Qwen3MoeForCausalLM"], "num_hidden_layers": 48, "num_attention_heads": 32, "num_key_value_heads": 4,
       "hidden_size": 2048, "head_dim": 128, "max_position_embeddings": 262144, "torch_dtype": "bfloat16"}


def files(*pairs):
    return [{"path": p, "size": s, "sha256": None} for p, s in pairs]


def analyse(repo, config, fl, **kw):
    return compat.analyse(repo_id=repo, config=config, files=fl, tags=[], pipeline_tag=kw.pop("pipeline_tag", "text-generation"),
                          params_b=kw.pop("params_b", 30), budget_gb=116, free_disk_gb=3000, **kw)


# ------------------------------------------------------------------ custom code


def test_a_tool_parser_script_is_not_custom_code():
    """Qwen3-Coder ships qwen3coder_tool_parser.py for vLLM; nothing loads it with trust_remote_code."""
    fl = files(("model-00001-of-00002.safetensors", 30_000_000_000), ("qwen3coder_tool_parser.py", 900), ("config.json", 800))
    a = analyse("Qwen/Qwen3-Coder-30B-A3B-Instruct", CFG, fl)
    assert a["custom_code"] is False
    assert a["scripts"] == ["qwen3coder_tool_parser.py"]
    assert next(e for e in a["engines"] if e["engine"] == "vllm")["verdict"] == "likely"
    warns = _security({"files": fl, "config": CFG, "license": "apache-2.0", "gated": False, "tags": []})
    assert not any(w["code"] == "custom_code" for w in warns)
    assert any(w["code"] == "extra_scripts" and w["level"] == "info" for w in warns)


def test_modelling_files_or_auto_map_are_custom_code():
    fl = files(("model.safetensors", 1_000_000_000), ("modeling_x.py", 10))
    assert compat.remote_code(CFG, [f["path"] for f in fl]) == (True, ["modeling_x.py"])
    assert compat.remote_code({**CFG, "auto_map": {"AutoModel": "m.M"}}, ["model.safetensors"])[0] is True
    assert compat.remote_code(CFG, ["model.safetensors", "README.md"]) == (False, [])
    warns = _security({"files": fl, "config": CFG, "license": "mit", "gated": False, "tags": []})
    assert any(w["code"] == "custom_code" and w["level"] == "danger" for w in warns)


# ------------------------------------------------------------------ GGUF


SHARDED = files(("m-Q8_0-00001-of-00003.gguf", 10_000_000_000), ("m-Q8_0-00002-of-00003.gguf", 10_000_000_000),
                ("m-Q8_0-00003-of-00003.gguf", 5_000_000_000))
WHOLE = files(("m-Q4_K_M.gguf", 4_000_000_000))


def test_ollama_prefers_a_quantisation_that_is_not_split():
    a = analyse("x/y-GGUF", None, SHARDED + WHOLE, pipeline_tag=None)
    ev = next(e for e in a["engines"] if e["engine"] == "ollama")
    assert ev["recommended_params"]["quant"] == "Q4_K_M"
    assert ev["recommended_params"]["split"] == ["q8_0"]


def test_a_split_gguf_is_reported_before_downloading_anything():
    quant, problem = installers.pick_gguf(SHARDED, None)
    assert quant == "q8_0" and problem and "split" in problem
    assert installers.pick_gguf(SHARDED + WHOLE, None) == ("q4_k_m", None)
    assert installers.pick_gguf(SHARDED + WHOLE, "Q8_0")[1] is not None  # asking for the split one still says why not
    assert installers.pick_gguf([], None)[1]


# ------------------------------------------------------------------ recipes


def test_files_recipe_always_applies_and_the_rest_state_why_not():
    fl = SHARDED
    out = {i["engine"]: i for i in installers.listing(analyse("x/y", None, fl, pipeline_tag=None), fl, None)}
    assert out["files"]["usable"] and out["files"]["unusable_reason"] is None
    assert not out["vllm"]["usable"] and "safetensors" in out["vllm"]["unusable_reason"]
    assert out["ollama"]["usable"]  # GGUF present; the split check happens when picking the quantisation


def test_forcing_is_only_offered_where_the_engine_could_in_principle_load_it():
    fl = files(("model.safetensors", 1_000_000_000))
    out = {i["engine"]: i for i in installers.listing(analyse("x/y", {"architectures": ["Foo"]}, fl), fl, {"architectures": ["Foo"]})}
    assert out["mlworker"]["usable"] and out["mlworker"]["needs_force"]  # no verdict, but could be tried
    assert not out["files"]["needs_force"]


def test_file_selection_follows_the_recipe_and_the_patterns():
    fl = files(("model.safetensors", 5), ("pytorch_model.bin", 5), ("config.json", 1), ("m.gguf", 5), ("onnx/m.onnx", 5), ("tok.py", 1))
    assert [f["path"] for f in installers.select_files(fl, "vllm")] == ["model.safetensors", "config.json", "tok.py"]
    assert [f["path"] for f in installers.select_files(fl, "files")] == ["model.safetensors", "config.json", "m.gguf", "onnx/m.onnx", "tok.py"]
    assert [f["path"] for f in installers.select_files(fl, "files", include=["*.gguf", "*.json"])] == ["config.json", "m.gguf"]
    assert [f["path"] for f in installers.select_files(fl, "files", exclude=["onnx/*", "*.py"])] == ["model.safetensors", "config.json", "m.gguf"]
    only_pickle = files(("pytorch_model.bin", 5), ("config.json", 1))
    assert [f["path"] for f in installers.select_files(only_pickle, "vllm")] == ["config.json"]  # pickles never by default
    assert [f["path"] for f in installers.select_files(only_pickle, "files")] == ["pytorch_model.bin", "config.json"]  # unless it is what you asked for


def test_comfyui_takes_one_top_level_checkpoint():
    fl = files(("a.safetensors", 5), ("b.safetensors", 5), ("sub/c.safetensors", 5))
    assert [f["path"] for f in installers.select_files(fl, "comfyui")] == ["a.safetensors"]


# ------------------------------------------------------------------ the plan endpoint, end to end with the Hub mocked


def _plan(monkeypatch, info, manager=True, **body):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.models import Role
    from app.routers import huggingface as H

    db = MagicMock()
    db.scalars.return_value = []
    monkeypatch.setattr(H.hf, "model_info", lambda *a, **k: info)
    monkeypatch.setattr(H.scheduler, "budget", lambda db: {"schedulable_gb": 116})
    monkeypatch.setattr(H.storage, "available_for_downloads", lambda db: 2_000_000_000_000)
    monkeypatch.setattr(H, "_token", lambda db: None)
    p = SimpleNamespace(is_manager=manager, user=SimpleNamespace(role=Role.admin if manager else Role.researcher))
    return H.plan(H.PlanIn(repo_id=info["id"], **body), p, db)


def _info(repo, fl, config=None, **kw):
    return {"id": repo, "sha": "a" * 40, "files": fl, "config": config, "tags": [], "pipeline_tag": kw.pop("pipeline_tag", "text-generation"),
            "parameters": kw.pop("parameters", None), "gated": False, "license": "apache-2.0", "library": None, **kw}


def test_plan_for_qwen3_coder_has_no_blocker_and_no_danger(monkeypatch):
    fl = files(("model-00001-of-00002.safetensors", 30_000_000_000), ("qwen3coder_tool_parser.py", 900))
    pl = _plan(monkeypatch, _info("Qwen/Qwen3-Coder-30B-A3B-Instruct", fl, CFG, parameters=30e9))
    assert pl["engine"] == "vllm" and pl["blockers"] == []
    assert not any(w["level"] == "danger" for w in pl["warnings"])
    assert pl["engine_analysis"]["recommended_params"]["tool_call_parser"] == "qwen3_coder"


def test_plan_falls_back_to_files_when_no_engine_takes_the_repo(monkeypatch):
    fl = files(("adapter_model.bin", 1_000), ("README.md", 10))
    pl = _plan(monkeypatch, _info("x/some-adapter", fl, None, pipeline_tag=None))
    assert pl["engine"] == "files" and pl["blockers"] == [] and pl["download_bytes"] == 1_010
    assert any(w["code"] == "files_only" for w in pl["warnings"])


def test_split_gguf_blocks_ollama_but_installs_as_files(monkeypatch):
    info = _info("x/big-GGUF", SHARDED, None, pipeline_tag=None)
    blocked = _plan(monkeypatch, info)
    assert blocked["engine"] == "ollama" and any("split" in b for b in blocked["blockers"]) and not blocked["overridable"]
    ok = _plan(monkeypatch, info, engine="files", include_patterns=["*Q8_0*"])
    assert ok["engine"] == "files" and ok["blockers"] == [] and ok["download_bytes"] == 25_000_000_000


def test_unsupported_verdict_can_be_forced_only_by_a_manager(monkeypatch):
    fl = files(("model.safetensors", 200_000_000_000))  # does not fit in 116 GB
    info = _info("x/huge", fl, CFG, parameters=100e9)
    stopped = _plan(monkeypatch, info, engine="vllm")
    assert stopped["blockers"] and stopped["overridable"] and not stopped["forced"]
    forced = _plan(monkeypatch, info, engine="vllm", force=True)
    assert forced["blockers"] == [] and forced["forced"]
    assert any(w["code"] == "forced" for w in forced["warnings"])
    researcher = _plan(monkeypatch, info, manager=False, engine="vllm", force=True)
    assert researcher["blockers"] and not researcher["forced"] and not researcher["overridable"]


def test_forcing_never_lifts_a_missing_prerequisite(monkeypatch):
    info = _info("x/gguf-only", WHOLE, None, pipeline_tag=None)
    pl = _plan(monkeypatch, info, engine="vllm", force=True)
    assert pl["blockers"] and "safetensors" in pl["blockers"][0]
