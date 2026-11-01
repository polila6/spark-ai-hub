"""Unit tests for pure logic (no services). Run: docker compose run --rm --no-deps backend pytest -q tests"""
import io
import zipfile
from pathlib import Path

import pytest

from app.logging_setup import redact
from app.services import compat, workflows
from app.services.benchsuites import SUITES, score
from app.services.storage import StorageError, safe_join
from app.services.uploads import AUDIO_MAGIC, IMAGE_MAGIC, UploadError, extract_image_zip, sniff

QWEN_CFG = {"architectures": ["Qwen3ForCausalLM"], "num_hidden_layers": 36, "num_attention_heads": 32,
            "num_key_value_heads": 8, "hidden_size": 4096, "head_dim": 128, "max_position_embeddings": 40960,
            "quantization_config": {"quant_method": "fp8"}}


def files(*pairs):
    return [{"path": p, "size": s, "sha256": None} for p, s in pairs]


def test_kv_cache_formula():
    # 2 (K,V) · 36 layers · 8 kv heads · 128 dim · 2 bytes · 8192 tokens = 1.208 GB
    assert compat.kv_cache_gb(QWEN_CFG, 8192) == pytest.approx(1.208, rel=1e-3)


def test_quantization_detection():
    assert compat.detect_quantization("x/y", QWEN_CFG, []) == "fp8"
    assert compat.detect_quantization("x/Model-AWQ", {}, []) == "awq"
    assert compat.detect_quantization("x/y", {"quantization_config": {"quant_method": "bitsandbytes", "load_in_4bit": True}}, []) == "bnb-4bit"
    assert compat.detect_quantization("x/y", {"torch_dtype": "bfloat16"}, []) == "bf16"
    assert compat.gguf_quant("model-Q4_K_M.gguf") == "q4_k_m"


def test_analyse_vllm_likely_and_nvidia_matrix():
    a = compat.analyse(repo_id="nvidia/Qwen3-8B-FP8", config=QWEN_CFG, files=files(("model.safetensors", 9_000_000_000), ("config.json", 1000)),
                       tags=[], pipeline_tag="text-generation", params_b=8.2, budget_gb=116, free_disk_gb=3000)
    v = next(e for e in a["engines"] if e["engine"] == "vllm")
    assert v["verdict"] == "likely" and v["confidence"] >= 0.85
    assert any("NVIDIA" in r for r in v["reasons"])


def test_analyse_rejects_oversized_model():
    a = compat.analyse(repo_id="x/huge", config=QWEN_CFG, files=files(("model.safetensors", 200_000_000_000)),
                       tags=[], pipeline_tag="text-generation", params_b=400, budget_gb=116, free_disk_gb=3000)
    assert a["overall"] == "unsupported"


def test_analyse_pickle_only_has_no_engine():
    a = compat.analyse(repo_id="x/old", config={"architectures": ["LlamaForCausalLM"]}, files=files(("pytorch_model.bin", 1_000_000)),
                       tags=[], pipeline_tag="text-generation", params_b=1, budget_gb=116, free_disk_gb=3000)
    assert a["pickle_only"] and a["recommended_engine"] is None


def test_analyse_custom_code_is_experimental():
    cfg = dict(QWEN_CFG, auto_map={"AutoModel": "modeling_x.X"})
    a = compat.analyse(repo_id="x/custom", config=cfg, files=files(("model.safetensors", 1_000_000_000), ("modeling_x.py", 100)),
                       tags=[], pipeline_tag="text-generation", params_b=1, budget_gb=116, free_disk_gb=3000)
    assert a["custom_code"] and next(e for e in a["engines"] if e["engine"] == "vllm")["verdict"] == "experimental"


def test_analyse_disk_shortage():
    a = compat.analyse(repo_id="x/y", config=QWEN_CFG, files=files(("model.safetensors", 50_000_000_000)),
                       tags=[], pipeline_tag="text-generation", params_b=8, budget_gb=116, free_disk_gb=10)
    assert a["overall"] == "unsupported" and any("free" in r for r in a["reasons"])


def test_gguf_goes_to_ollama():
    a = compat.analyse(repo_id="u/m-GGUF", config=None, files=files(("m-Q4_K_M.gguf", 2_500_000_000), ("m-Q8_0.gguf", 4_300_000_000)),
                       tags=["gguf"], pipeline_tag="text-generation", params_b=4, budget_gb=116, free_disk_gb=3000)
    o = next(e for e in a["engines"] if e["engine"] == "ollama")
    assert o["recommended_params"]["quant"] == "Q4_K_M"


def test_workflow_validation_and_graph():
    wf = workflows.WORKFLOWS["sd15-txt2img"]
    p = workflows.validate(wf, {"prompt": "cat", "steps": 10})
    assert p["seed"] is not None and p["width"] == 512
    g = workflows.build(wf, p, None, "sah/x/out")
    assert g["3"]["class_type"] == "KSampler" and g["3"]["inputs"]["steps"] == 10
    with pytest.raises(workflows.WorkflowError):
        workflows.validate(wf, {"prompt": "x", "steps": 1000})
    with pytest.raises(workflows.WorkflowError):
        workflows.validate(wf, {"prompt": "x", "sampler": "rm -rf /"})
    v = workflows.validate(workflows.WORKFLOWS["wan21-t2v-1.3b"], {"prompt": "x", "frames": 30})
    assert (v["frames"] - 1) % 4 == 0


def test_safe_join_blocks_traversal(tmp_path):
    assert safe_join(tmp_path, "a", "b.txt") == (tmp_path / "a" / "b.txt").resolve()
    with pytest.raises(StorageError):
        safe_join(tmp_path, "../etc/passwd")


def test_zip_slip_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.png", b"x")
    p = tmp_path / "a.zip"
    p.write_bytes(buf.getvalue())
    with pytest.raises(UploadError):
        extract_image_zip(p, tmp_path / "out")
    assert not (tmp_path.parent / "evil.png").exists()


def test_zip_only_images_extracted(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cat/1.png", b"png")
        z.writestr("cat/run.py", b"print(1)")
    p = tmp_path / "a.zip"
    p.write_bytes(buf.getvalue())
    info = extract_image_zip(p, tmp_path / "out")
    assert info["images"] == 1 and not (tmp_path / "out" / "cat" / "run.py").exists()


def test_magic_sniffing():
    assert sniff(b"\x89PNG\r\n\x1a\n....", IMAGE_MAGIC) == ".png"
    assert sniff(b"RIFF\x00\x00\x00\x00WAVEfmt ", AUDIO_MAGIC) == ".wav"
    assert sniff(b"#!/bin/sh\nrm -rf", IMAGE_MAGIC) is None


def test_secret_redaction():
    s = redact("token hf_abcdefghijklmnop and key sah-1234567890abcdef Authorization: Bearer xyz password=hunter2")
    assert "abcdefghijklmnop" not in s and "1234567890abcdef" not in s and "hunter2" not in s and "xyz" not in s


def test_deterministic_scorers():
    assert score("exact_int", {"a": 391}, "The answer is 391") == 1.0
    assert score("exact_int", {"a": 391}, "390") == 0.0
    assert score("letter", {"a": "B"}, "B") == 1.0
    assert score("json_keys", {"keys": ["name", "category"]}, '{"name": "apple", "category": "fruit"}') == 1.0
    assert score("json_keys", {"keys": ["name", "category"]}, "sure! {name: apple}") == 0.0
    assert score("exact_int", {"a": 4}, "<think>2+2 is 5?</think>4") == 1.0
    assert all(len(v.get("cases", [])) or v["kind"] == "llm_perf" for v in SUITES.values())


def test_vllm_architecture_list_loaded():
    archs = compat.vllm_architectures()
    assert "Qwen3ForCausalLM" in archs and len(archs) > 100



def test_tool_calling_gate():
    from types import SimpleNamespace as NS

    from app.engines import EngineError
    from app.services.llm import sanitize_chat_payload, supports_tools

    body = {"messages": [], "tools": [{"type": "function", "function": {"name": "f"}}]}
    vllm_plain = NS(backend="vllm", alias="m", capabilities=["chat", "tools"], runtime_config=NS(params={}))
    vllm_tools = NS(backend="vllm", alias="m", capabilities=["chat"],
                    runtime_config=NS(params={"enable_tool_calling": True, "tool_call_parser": "hermes"}))
    ollama = NS(backend="ollama", alias="o", engine_ref="hf.co/x:Q4", capabilities=["chat", "tools"], runtime_config=None)
    assert not supports_tools(vllm_plain)  # vLLM rejects tools unless started with a parser
    assert supports_tools(vllm_tools) and supports_tools(ollama)
    with pytest.raises(EngineError):
        sanitize_chat_payload(vllm_plain, body)
    assert sanitize_chat_payload(ollama, body)["model"] == "hf.co/x:Q4"


# ---------------------------------------------------------------- personal cloud / harness (v0.2)


def test_mail_templates_render_links_and_access_steps():
    from app.services.mailer import render

    subj, text, html = render("invite", "es", {"inviter": "Admin", "link": "https://hub/invite/abc", "hours": 72,
                                               "username": "ana", "share": "https://login.tailscale.com/admin/invite/x"})
    assert "invitado" in subj and "https://hub/invite/abc" in text and "ana" in text
    assert "tailscale.com/download" in text and "https://login.tailscale.com/admin/invite/x" in text
    assert '<a href="https://hub/invite/abc"' in html
    _, text_en, _ = render("invite", "en", {"inviter": "A", "link": "L", "hours": 1, "username": "u"})
    assert "administrator will send it separately" in text_en  # no share link configured


def test_file_classification_rejects_spoofed_and_executable_files():
    from app.services.files import FileError, classify

    assert classify("paper.pdf", b"%PDF-1.7 ...") == ("document", ".pdf")
    assert classify("data.csv", b"a,b\n1,2") == ("data", ".csv")
    assert classify("photo.png", b"\x89PNG\r\n\x1a\n....") == ("image", ".png")
    assert classify("notes", b"plain text") == ("document", ".txt")
    for name, head in (("fake.pdf", b"MZ\x90\x00"), ("model.pkl", b"\x80\x04"), ("x.exe", b"MZ"), ("s.xlsx", b"%PDF")):
        with pytest.raises(FileError):
            classify(name, head)


def test_chunking_keeps_pages_and_overlap():
    from app.services.kb import chunk_pages

    paras = "\n\n".join(f"Párrafo {i} " + "palabra " * 60 for i in range(12))
    chunks = chunk_pages([(3, paras), (4, "corto")], size=800, overlap=100)
    assert all(len(t) <= 900 for _, t in chunks)
    assert {p for p, _ in chunks} == {3, 4}
    assert chunks[-1] == (4, "corto")
    assert any(t.startswith(chunks[0][1][-100:][:20]) or chunks[0][1][-100:] in t for _, t in chunks[1:2])


def _run_dataq(tmp_path, name, content: bytes, sql=None):
    import json
    import subprocess
    import sys
    from pathlib import Path

    import app.services.dataq_runner as runner

    f = tmp_path / name
    f.write_bytes(content)
    req = {"op": "query" if sql else "describe", "sql": sql, "path": str(f), "ext": Path(name).suffix}
    out = subprocess.run([sys.executable, "-I", runner.__file__], input=json.dumps(req), capture_output=True, text=True,
                         timeout=60)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_data_queries_csv_sqlite_and_sandbox(tmp_path):
    import sqlite3

    r = _run_dataq(tmp_path, "v.csv", b"city,sales\nValencia,10\nMadrid,7\nValencia,5\n",
                   "SELECT city, sum(sales) AS s FROM datos GROUP BY city ORDER BY s DESC")
    assert r["ok"] and r["rows"] == [["Valencia", 15], ["Madrid", 7]]
    d = _run_dataq(tmp_path, "v2.csv", b"a,b\n1,x\n")
    assert d["tables"][0]["rows"] == 1 and [c["name"] for c in d["tables"][0]["columns"]] == ["a", "b"]
    # external access is disabled after loading: the query cannot read other files
    bad = _run_dataq(tmp_path, "v3.csv", b"a\n1\n", "SELECT * FROM read_csv_auto('/etc/passwd')")
    assert not bad["ok"]
    db = tmp_path / "t.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    con.commit()
    con.close()
    r = _run_dataq(tmp_path, "t2.sqlite", db.read_bytes(), "SELECT sum(x) FROM t")
    assert r["ok"] and r["rows"] == [[6]]
    w = _run_dataq(tmp_path, "t3.sqlite", db.read_bytes(), "DELETE FROM t")
    assert not w["ok"]  # read-only


def test_anthropic_translation_roundtrip():
    from app.routers.anthropic_gateway import StreamTranslator, from_openai, to_openai

    body = {"model": "m", "max_tokens": 100, "system": [{"type": "text", "text": "be brief"}],
            "tools": [{"name": "read", "description": "read file", "input_schema": {"type": "object", "properties": {"p": {"type": "string"}}}}],
            "tool_choice": {"type": "any"},
            "messages": [{"role": "user", "content": "hola"},
                         {"role": "assistant", "content": [{"type": "text", "text": "voy"},
                                                           {"type": "tool_use", "id": "t1", "name": "read", "input": {"p": "a"}}]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "DATA"},
                                                      {"type": "text", "text": "sigue"}]}]}
    oa = to_openai(body)
    roles = [m["role"] for m in oa["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assert oa["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"p": "a"}'
    assert oa["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "DATA"}
    assert oa["tool_choice"] == "required" and oa["tools"][0]["function"]["name"] == "read"
    resp = from_openai({"choices": [{"message": {"content": None, "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{\"p\":\"b\"}"}}]},
                                     "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}, "m")
    assert resp["stop_reason"] == "tool_use" and resp["content"][0]["input"] == {"p": "b"}
    tr = StreamTranslator("m", thinking=False)
    out = tr.start() + tr.feed({"choices": [{"delta": {"content": "Hi"}}]}) + tr.feed(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c2", "function": {"name": "read", "arguments": "{\"p\""}}]}}]}) + tr.feed(
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ":1}"}}]}, "finish_reason": "tool_calls"}]}) + tr.end()
    s = out.decode()
    assert s.count("event: content_block_start") == 2 and '"text_delta"' in s and '"input_json_delta"' in s
    assert '"stop_reason":"tool_use"' in s and s.rstrip().endswith('{"type":"message_stop"}')


def test_qwen_image_workflow_graphs():
    from app.services import workflows

    wf = workflows.WORKFLOWS["qwen-image-txt2img"]
    p = workflows.validate(wf, {"prompt": "a cat", "width": 1328, "height": 1328})
    g = workflows.build(wf, p, None, "x/out")
    assert g["58"]["class_type"] == "EmptySD3LatentImage" and g["3"]["inputs"]["steps"] == 8
    assert g["73"]["inputs"]["lora_name"].startswith("Qwen-Image-Lightning")
    ed = workflows.WORKFLOWS["qwen-image-edit"]
    pe = workflows.validate(ed, {"prompt": "make it blue", "image": "00000000-0000-0000-0000-000000000000"})
    ge = workflows.build(ed, pe, "in.png", "x/out")
    assert ge["6"]["class_type"] == "TextEncodeQwenImageEditPlus" and ge["3"]["inputs"]["model"] == ["75", 0]
    assert set(ed.nodes) >= {n["class_type"] for n in ge.values()}
    assert set(wf.nodes) >= {n["class_type"] for n in g.values()}


def test_kv_cache_counts_only_full_attention_layers():
    hybrid = {"text_config": {"num_hidden_layers": 40, "num_key_value_heads": 2, "head_dim": 256, "num_attention_heads": 16,
                              "layer_types": ["linear_attention"] * 3 + ["full_attention"] + ["linear_attention"] * 3 + ["full_attention"]}}
    assert compat.kv_cache_gb(hybrid, 1000) == pytest.approx(2 * 2 * 2 * 256 * 2 * 1000 / 1e9)
    interval = {"num_hidden_layers": 40, "num_key_value_heads": 2, "head_dim": 256, "num_attention_heads": 16,
                "full_attention_interval": 4}
    assert compat.kv_cache_gb(interval, 1000) == pytest.approx(2 * 10 * 2 * 256 * 2 * 1000 / 1e9)


def test_web_fetch_blocks_internal_addresses():
    from app.services import web

    for url in ["http://127.0.0.1/", "http://10.0.0.5/", "http://192.168.1.1/", "http://100.100.100.100/",
                "http://169.254.169.254/latest/meta-data", "http://[::1]/", "http://[fd7a:115c:a1e0::1]/",
                "ftp://example.com/", "http://user:pw@example.com/", "http://example.com:5432/"]:
        with pytest.raises(web.WebError):
            web._check_url(url)


def test_html_to_text_keeps_content_drops_chrome():
    from app.services.web import html_to_text

    html = (b"<html><head><title>T</title><script>alert(1)</script></head><body><nav>menu</nav><main><h1>Hola</h1>"
            b"<p>Primer p\xc3\xa1rrafo.</p><ul><li>uno</li></ul><pre><span>a</span><span> = 1</span></pre></main>"
            b"<footer>pie</footer></body></html>")
    title, text = html_to_text(html)
    assert title == "T" and "# Hola" in text and "Primer párrafo." in text and "- uno" in text and "a = 1" in text
    assert "menu" not in text and "pie" not in text and "alert" not in text


def test_skill_md_parsing_and_sources():
    from app.services.skills import parse_skill_md, parse_source

    info = parse_skill_md("---\nname: Doc Coauthoring\ndescription: Co-write docs.\nlicense: MIT\n---\n# Title\nBody")
    assert info["name"] == "doc-coauthoring" and info["description"] == "Co-write docs." and info["license"] == "MIT"
    assert info["content"].startswith("# Title")
    assert parse_skill_md("# Informe Ensayos\n\nRedacta informes.")["name"] == "informe-ensayos"
    assert parse_source("anthropics/skills/skills/pdf") == {"kind": "github", "owner": "anthropics", "repo": "skills",
                                                            "ref": None, "path": "skills/pdf"}
    s = parse_source("https://github.com/o/r/blob/main/skills/x/SKILL.md")
    assert (s["owner"], s["repo"], s["ref"], s["path"]) == ("o", "r", "main", "skills/x")
    s = parse_source("https://raw.githubusercontent.com/o/r/main/SKILL.md")
    assert (s["ref"], s["path"]) == ("main", "")
    assert parse_source("https://example.org/a/SKILL.md")["kind"] == "url"


def test_context_token_estimates():
    from app.services.agent import est_tokens

    assert est_tokens("a" * 320) == 101
    msgs = [{"role": "user", "content": [{"type": "text", "text": "x" * 32}, {"type": "image_url", "image_url": {}}]}]
    assert est_tokens(msgs) > 1100


def test_parser_defaults_for_known_families():
    from app.services.compat import parser_defaults

    assert parser_defaults("Qwen/Qwen3.5-35B-A3B-FP8", {"model_type": "qwen3_5_moe"})["tool_call_parser"] == "qwen3_coder"
    assert parser_defaults("Qwen/Qwen3-4B-Instruct-2507", {"model_type": "qwen3"}) == {"enable_tool_calling": True, "tool_call_parser": "hermes"}
    assert parser_defaults("openai/gpt-oss-20b", {"model_type": "gpt_oss"})["reasoning_parser"] == "openai_gptoss"
    assert parser_defaults("meta-llama/Llama-3.1-8B-Instruct", {"architectures": ["LlamaForCausalLM"]})["tool_call_parser"] == "llama3_json"
    assert parser_defaults("google/gemma-3-4b-it", {"model_type": "gemma3"}) == {}


def test_scheduled_task_next_run_and_description():
    from datetime import datetime, timezone

    from app.services import tasks

    s = tasks.validate({"freq": "daily", "time": "08:30", "weekdays": [0, 1, 2, 3, 4]}, "Europe/Madrid")
    friday_evening = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)  # Fri 21:00 in Madrid
    nxt = tasks.next_run(s, "Europe/Madrid", friday_evening)
    assert nxt == datetime(2026, 11, 9, 7, 30, tzinfo=timezone.utc)  # Monday 08:30 CET
    w = tasks.validate({"freq": "weekly", "weekday": 4, "time": "18:00"}, "Europe/Madrid")
    assert tasks.next_run(w, "Europe/Madrid", datetime(2026, 11, 1, 12, tzinfo=timezone.utc)).isoformat().startswith("2026-11-06T17:00")
    summer = tasks.next_run(tasks.validate({"freq": "monthly", "day": 15, "time": "09:00"}, "Europe/Madrid"),
                            "Europe/Madrid", datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert summer == datetime(2026, 6, 15, 7, 0, tzinfo=timezone.utc)  # CEST keeps the wall-clock time
    h = tasks.validate({"freq": "hourly", "minute": 5, "every": 6}, "UTC")
    assert tasks.next_run(h, "UTC", datetime(2026, 11, 1, 7, 10, tzinfo=timezone.utc)).hour == 12
    assert tasks.describe(s) == "de lunes a viernes a las 08:30"
    import pytest
    with pytest.raises(tasks.TaskError):
        tasks.validate({"freq": "daily", "time": "25:00"}, "UTC")


def test_canvas_edits_are_exact():
    import pytest

    from app.models import CanvasDoc
    from app.services import canvas

    d = CanvasDoc(content="Hola Ana.\nUn saludo.\nUn saludo.", title="t")
    assert canvas.apply_edits(d, [{"find": "Hola Ana.", "replace": "Estimada Ana:"}]).startswith("Estimada Ana:")
    with pytest.raises(canvas.CanvasError):
        canvas.apply_edits(d, [{"find": "Un saludo.", "replace": "x"}])  # ambiguous
    assert canvas.apply_edits(d, [{"find": "Un saludo.", "replace": "x", "all": True}]).count("x") == 2
    with pytest.raises(canvas.CanvasError):
        canvas.apply_edits(d, [{"find": "no está", "replace": "x"}])


def test_voice_reads_prose_only():
    from app.services.voice import speakable

    t = speakable("## Título\n- uno con **negrita** y [enlace](https://x.org)\n```py\nprint(1)\n```\nFin $x^2$ https://a.b")
    assert "Título" in t and "negrita" in t and "enlace" in t and "https" not in t and "print" not in t and "**" not in t


def test_connector_validation_and_private_addresses(monkeypatch):
    import pytest

    from app.services import connectors

    assert connectors.validate("github", {"repo": "anthropics/skills", "path": "skills", "extra": 1}) == {"repo": "anthropics/skills", "path": "skills"}
    for kind, cfg in [("github", {"repo": "nope"}), ("zotero", {"library_id": "abc"}), ("webdav", {"url": "ftp://x"}), ("smb", {"host": "h"})]:
        with pytest.raises(connectors.ConnectorError):
            connectors.validate(kind, cfg)
    monkeypatch.setattr(connectors, "get_setting", lambda db, k: ["192.168.1.0/24", "nas.lab"])
    fake = lambda ip: (lambda *a, **k: [(2, 1, 6, "", (ip, 0))])  # noqa: E731
    monkeypatch.setattr(connectors.socket, "getaddrinfo", fake("192.168.1.20"))
    assert connectors.resolve(None, "files.local", 445) == "192.168.1.20"
    monkeypatch.setattr(connectors.socket, "getaddrinfo", fake("10.0.0.5"))
    with pytest.raises(connectors.ConnectorError):
        connectors.resolve(None, "other.local", 445)
    assert connectors.resolve(None, "nas.lab", 445) == "10.0.0.5"  # allowed by host name
    for bad in ("127.0.0.1", "169.254.169.254", "0.0.0.0"):
        monkeypatch.setattr(connectors.socket, "getaddrinfo", fake(bad))
        with pytest.raises(connectors.ConnectorError):
            connectors.resolve(None, "nas.lab", 445)  # never, even when listed


def test_oidc_id_token_claims_and_parsers():
    import base64
    import json

    from app.routers.oidc import _claims
    from app.services.compat import parser_defaults

    payload = base64.urlsafe_b64encode(json.dumps({"sub": "1", "email": "a@b.org"}).encode()).rstrip(b"=").decode()
    assert _claims(f"x.{payload}.sig")["email"] == "a@b.org"
    q = parser_defaults("Qwen/Qwen3.5-35B-A3B-FP8", {"text_config": {"model_type": "qwen3_5_moe", "mtp_num_hidden_layers": 1}})
    assert q["speculative_tokens"] == 2


def test_workspace_paths_refuse_links_fifos_and_escapes(tmp_path, monkeypatch):
    import os
    import uuid

    import pytest

    from app.models import Workspace
    from app.services import workspace as w

    ws = Workspace(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="t")
    monkeypatch.setattr(w, "root", lambda _ws: tmp_path / "ws")
    w.ensure(ws)
    (tmp_path / "secret.txt").write_text("top secret")
    (tmp_path / "ws" / "datos").mkdir()
    (tmp_path / "ws" / "datos" / "a.csv").write_text("x,y\n1,2\n")
    os.symlink(tmp_path / "secret.txt", tmp_path / "ws" / "fuga")
    os.symlink(tmp_path, tmp_path / "ws" / "dir")
    os.mkfifo(tmp_path / "ws" / "tubo")
    assert w.read(ws, "datos/a.csv")["content"].startswith("x,y")
    assert w.table_preview(ws, "datos/a.csv")["sheets"][0]["rows"][1] == ["1", "2"]
    for bad in ("fuga", "dir/secret.txt", "tubo", "../secret.txt", "datos/../../secret.txt"):
        with pytest.raises(w.WorkspaceError):
            w.read(ws, bad)
    with pytest.raises(w.WorkspaceError):
        w.raw(ws, "dir/secret.txt")
    assert {e["path"]: e["type"] for e in w.tree(ws)}["fuga"] == "link"
    assert w.clean_rel("/a//b/./c") == "a/b/c"


class _FakeRedis:
    def __init__(self):
        self.kv, self.lists, self.sets = {}, {}, {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return False
        self.kv[k] = v
        return True

    def exists(self, k):
        return int(k in self.kv)

    def delete(self, *ks):
        for k in ks:
            self.kv.pop(k, None)
            self.lists.pop(k, None)

    def rpush(self, k, v):
        self.lists.setdefault(k, []).append(v)

    def lrange(self, k, a, b):
        return self.lists.get(k, [])[a: None if b == -1 else b + 1]

    def expire(self, *a):
        return True

    def sadd(self, k, v):
        self.sets.setdefault(k, set()).add(v)

    def srem(self, k, v):
        self.sets.get(k, set()).discard(v)

    def smembers(self, k):
        return set(self.sets.get(k, set()))


def test_dead_turns_are_closed_and_deleted_chats_stop(monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone

    from app.services import turns

    fake = _FakeRedis()
    monkeypatch.setattr(turns, "get_redis", lambda: fake)
    turns.start("c1", "u1", "vivo")
    assert [t["conversation_id"] for t in turns.running_for_user("u1")] == ["c1"]  # fresh turn has a heartbeat
    # the process died: no heartbeat and started long ago
    fake.delete(turns._k("c1", ":beat"))
    info = json.loads(fake.get(turns._k("c1")))
    info["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    fake.set(turns._k("c1"), json.dumps(info))
    assert turns.running_for_user("u1") == []
    assert turns.active("c1")["status"] == "interrupted"
    assert turns.events("c1")[-1]["type"] == "error"
    turns.start("c1", "u1", "otra vez")  # a dead turn no longer blocks the chat
    turns.forget("c1", "u1")  # chat deleted while answering
    assert turns.running_for_user("u1") == [] and fake.exists(turns._k("c1", ":cancel"))


def test_webdav_listing_reads_every_propstat_and_recurses(monkeypatch):
    import httpx

    from app.services import connectors

    root = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
<d:response><d:href>/dav/docs/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
<d:response><d:href>/dav/docs/informe.md</d:href>
  <d:propstat><d:prop><d:getetag/></d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>
  <d:propstat><d:prop><d:getlastmodified>Sun, 01 Nov 2026</d:getlastmodified><d:getcontentlength>42</d:getcontentlength><d:resourcetype/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
<d:response><d:href>/dav/docs/sub/</d:href>
  <d:propstat><d:prop><d:getetag/></d:prop><d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
</d:multistatus>"""
    sub = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
<d:response><d:href>/dav/docs/sub/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
<d:response><d:href>/dav/docs/sub/nota%20uno.txt</d:href><d:propstat><d:prop><d:getetag>"e1"</d:getetag><d:getcontentlength>7</d:getcontentlength><d:resourcetype/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
</d:multistatus>"""

    def fake(db, method, url, **kw):
        return httpx.Response(207, content=(sub if url.rstrip("/").endswith("/sub") else root).encode())
    monkeypatch.setattr(connectors, "_request", fake)
    items = connectors.WebDav(None, {"url": "https://cloud.example.org/dav/docs/"}, None).list()
    assert sorted((i["path"], i["etag"], i["size"]) for i in items) == [
        ("informe.md", "Sun, 01 Nov 2026", 42), ("sub/nota uno.txt", "e1", 7)]
