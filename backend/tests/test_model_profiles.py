"""Launch profiles and YaRN context extension (pure logic). Run: docker compose run --rm --no-deps backend pytest -q tests"""
from app.services import compat

FLASH_NEXT = "nvidia/Qwen3.8-Flash-Next-FP4-Local"
FLASH_CFG = {"architectures": ["Qwen4ExpForConditionalGeneration"], "model_type": "qwen4_exp",
             "text_config": {"model_type": "qwen4_exp_text", "max_position_embeddings": 262144, "mtp_num_hidden_layers": 1,
                             "rope_parameters": {"mrope_interleaved": True, "mrope_section": [11, 11, 10],
                                                 "rope_type": "default", "rope_theta": 10000000,
                                                 "partial_rotary_factor": 0.25}}}


def test_profile_defaults_for_flash_next():
    d = compat.parser_defaults(FLASH_NEXT, FLASH_CFG)
    assert d["profile"] == "qwen38_flash_next" and d["tool_call_parser"] == "qwen3_xml"
    assert d["kv_cache_dtype"] == "fp8" and d["max_model_len"] == 262144


def test_profile_architecture_is_accepted():
    files = [{"path": f"model-{i:05d}.safetensors", "size": 3 * 10**9, "sha256": None} for i in range(33)]
    files.append({"path": "config.json", "size": 1000, "sha256": None})
    a = compat.analyse(repo_id=FLASH_NEXT, config=FLASH_CFG, files=files, tags=[], pipeline_tag="image-text-to-text",
                       params_b=125, budget_gb=110, free_disk_gb=2000)
    v = next(e for e in a["engines"] if e["engine"] == "vllm")
    assert v["verdict"] != "unsupported"
    assert v["estimate"]["weights_gb"] < 70  # the n-gram table stays on disk
    assert v["recommended_params"]["profile"] == "qwen38_flash_next"


def test_yarn_only_beyond_native_window():
    assert compat.yarn_scaling(FLASH_CFG, 262144) is None
    r = compat.yarn_scaling(FLASH_CFG, 524288)
    assert r["factor"] == 2.0 and r["original_max_position_embeddings"] == 262144 and r["in_text_config"]
    assert r["mrope_section"] == [11, 11, 10] and r["rope_theta"] == 10000000 and r["rope_type"] == "yarn"


def test_yarn_max_limits():
    assert compat.yarn_max(FLASH_NEXT, FLASH_CFG) == 1_048_576
    assert compat.yarn_max("meta/llama", {"model_type": "llama", "max_position_embeddings": 8192}) is None
    assert compat.yarn_max("Qwen/Qwen3-8B", {"model_type": "qwen3", "max_position_embeddings": 40960}) == 163840
