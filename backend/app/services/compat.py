"""Deterministic compatibility + resource estimation layer.

Hard technical constraints are evaluated with explicit rules. Every verdict carries the
reasons that produced it and a confidence value. No LLM is involved in these decisions.

Verdicts:
  verified      — a smoke test for this repo/revision/engine passed on this machine
  likely        — documented NVIDIA Spark recipe, or supported architecture + format and fits
  experimental  — may work: custom code, unusual quantisation, tight memory fit
  unsupported   — no engine can load this format, or memory clearly exceeds budget
  insufficient  — not enough metadata to decide
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

GB = 1e9
GIB = 1024**3

# NVIDIA DGX Spark vLLM playbook support matrix (dgx-spark-playbooks/nvidia/vllm, 2026-06-12).
NVIDIA_SPARK_VLLM = {
    "nvidia/Llama-3.1-8B-Instruct-FP8", "nvidia/Llama-3.1-8B-Instruct-NVFP4", "nvidia/Llama-3.3-70B-Instruct-NVFP4",
    "nvidia/Qwen3-8B-FP8", "nvidia/Qwen3-8B-NVFP4", "nvidia/Qwen3-14B-FP8", "nvidia/Qwen3-14B-NVFP4",
    "nvidia/Qwen3-32B-NVFP4", "nvidia/Qwen2.5-VL-7B-Instruct-NVFP4", "openai/gpt-oss-20b", "openai/gpt-oss-120b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-FP8",
    "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4", "nvidia/Phi-4-reasoning-plus-FP8",
    "nvidia/Phi-4-reasoning-plus-NVFP4", "nvidia/Phi-4-multimodal-instruct-FP8", "nvidia/Phi-4-multimodal-instruct-NVFP4",
    "Qwen/Qwen3-VL-Embedding-2B", "Qwen/Qwen3-VL-Reranker-2B", "Qwen/Qwen3-VL-Reranker-8B",
    "google/gemma-4-31B-it", "google/gemma-4-26B-A4B-it", "google/gemma-4-E4B-it", "google/gemma-4-E2B-it",
    "nvidia/Gemma-4-31B-IT-NVFP4",
}
# Models in that list that NVIDIA documents with a *different* container image.
NVIDIA_CUSTOM_IMAGE = {"google/gemma-4-31B-it", "google/gemma-4-26B-A4B-it", "google/gemma-4-E4B-it",
                       "google/gemma-4-E2B-it", "nvidia/Gemma-4-31B-IT-NVFP4"}

BYTES_PER_PARAM = {
    "fp32": 4.0, "bf16": 2.0, "fp16": 2.0, "fp8": 1.0, "int8": 1.0, "w8a8": 1.0,
    "nvfp4": 0.56, "mxfp4": 0.53, "int4": 0.56, "awq": 0.56, "gptq": 0.56, "bnb-4bit": 0.6,
    "q2_k": 0.36, "q3_k_m": 0.48, "q4_0": 0.56, "q4_k_m": 0.6, "q4_k_s": 0.58, "q5_k_m": 0.71,
    "q5_0": 0.69, "q6_k": 0.82, "q8_0": 1.06, "f16": 2.0,
}
PICKLE_EXT = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")
VLLM_QUANT_OK = {"fp8", "awq", "gptq", "compressed-tensors", "modelopt", "modelopt_fp4", "mxfp4", "nvfp4", "w8a8"}


@lru_cache
def vllm_architectures() -> set[str]:
    """Architectures registered in the pinned vLLM image (extracted at install time)."""
    for p in (Path("/data/config/vllm_architectures.json"), Path(__file__).parent.parent / "data" / "vllm_architectures.json"):
        if p.is_file():
            return set(json.loads(p.read_text()))
    return set()


@dataclass
class Estimate:
    weights_gb: float | None = None
    kv_cache_gb: float | None = None
    overhead_gb: float | None = None
    total_gb: float | None = None
    disk_gb: float | None = None
    context_length: int | None = None
    concurrency: int = 1
    basis: list[str] = field(default_factory=list)
    measured: bool = False


@dataclass
class EngineVerdict:
    engine: str
    verdict: str
    confidence: float
    reasons: list[str]
    limitations: list[str]
    estimate: dict
    recommended_params: dict


def detect_quantization(repo_id: str, config: dict | None, filenames: list[str]) -> str | None:
    qc = (config or {}).get("quantization_config") or {}
    method = (qc.get("quant_method") or "").lower()
    if method:
        if method == "bitsandbytes":
            return "bnb-4bit" if qc.get("load_in_4bit", True) else "int8"
        if method in ("modelopt",):
            algo = str(qc.get("quant_algo", "")).lower()
            return "nvfp4" if "fp4" in algo else "fp8"
        if method == "compressed-tensors":
            fmt = json.dumps(qc).lower()
            return "nvfp4" if "nvfp4" in fmt else ("fp8" if "float" in fmt or "fp8" in fmt else "w8a8")
        return method
    name = repo_id.lower()
    for tag in ("nvfp4", "mxfp4", "fp8", "awq", "gptq", "bnb-4bit", "int4", "int8"):
        if tag in name:
            return tag
    if any(f.endswith(".gguf") for f in filenames):
        return "gguf"
    dtype = str((config or {}).get("torch_dtype") or (config or {}).get("dtype") or "").lower()
    return {"bfloat16": "bf16", "float16": "fp16", "float32": "fp32"}.get(dtype)


def gguf_quant(filename: str) -> str | None:
    m = re.search(r"(?i)[-_.](q\d_k_[msl]|q\d_k|q\d_\d|iq\d_\w+|f16|bf16|q8_0)", filename)
    return m.group(1).lower() if m else None


def kv_cache_gb(config: dict | None, context: int, concurrency: int = 1, kv_bytes: float = 2.0) -> float | None:
    c = (config or {}).get("text_config") or config or {}
    layers = c.get("num_hidden_layers") or c.get("n_layer")
    heads = c.get("num_attention_heads") or c.get("n_head")
    kv_heads = c.get("num_key_value_heads") or heads
    hidden = c.get("hidden_size") or c.get("n_embd")
    head_dim = c.get("head_dim") or (hidden // heads if hidden and heads else None)
    if not (layers and kv_heads and head_dim):
        return None
    # Hybrid models (Qwen3.5/Qwen3-Next: Gated DeltaNet + attention) keep a KV cache only in full-attention layers.
    if isinstance(c.get("layer_types"), list) and c["layer_types"]:
        layers = sum(1 for t in c["layer_types"] if "full" in str(t)) or layers
    elif c.get("full_attention_interval"):
        layers = max(1, layers // int(c["full_attention_interval"]))
    return 2 * layers * kv_heads * head_dim * kv_bytes * context * concurrency / GB


def estimate(
    *,
    weights_bytes: int | None,
    params_b: float | None,
    quant: str | None,
    config: dict | None,
    context: int | None,
    concurrency: int = 1,
    measured_gb: float | None = None,
) -> Estimate:
    e = Estimate(concurrency=concurrency)
    if weights_bytes:
        e.weights_gb = weights_bytes / GB
        e.disk_gb = weights_bytes / GB
        e.basis.append("weights = size of weight files")
    elif params_b and quant in BYTES_PER_PARAM:
        e.weights_gb = params_b * BYTES_PER_PARAM[quant]
        e.basis.append(f"weights = {params_b:.1f}B params × {BYTES_PER_PARAM[quant]} B/param ({quant})")
    max_ctx = (config or {}).get("max_position_embeddings") or ((config or {}).get("text_config") or {}).get("max_position_embeddings")
    ctx = context or (min(int(max_ctx), 8192) if max_ctx else 4096)
    e.context_length = ctx
    kv = kv_cache_gb(config, ctx, concurrency)
    if kv is not None:
        e.kv_cache_gb = kv
        e.basis.append(f"KV cache = 2·layers·kv_heads·head_dim·2B·{ctx} ctx·{concurrency} seq")
    elif e.weights_gb:
        e.kv_cache_gb = max(0.5, 0.1 * e.weights_gb)
        e.basis.append("KV cache unknown → 10 % of weights (heuristic)")
    if e.weights_gb is not None:
        e.overhead_gb = 2.5 + 0.08 * e.weights_gb
        e.basis.append("overhead = 2.5 GB CUDA/runtime + 8 % activations")
        e.total_gb = round(e.weights_gb + (e.kv_cache_gb or 0) + e.overhead_gb, 2)
    if measured_gb:
        e.total_gb = round(max(e.total_gb or 0, measured_gb * 1.05), 2)
        e.measured = True
        e.basis.append(f"adjusted with local measurement {measured_gb:.1f} GB (+5 %)")
    for f in ("weights_gb", "kv_cache_gb", "overhead_gb", "disk_gb"):
        v = getattr(e, f)
        if v is not None:
            setattr(e, f, round(v, 2))
    return e


def parser_defaults(repo_id: str, config: dict | None) -> dict:
    """vLLM tool-calling / reasoning parsers for well-known model families (so tools work right after install)."""
    rid = repo_id.lower()
    c = config or {}
    mtype = f"{c.get('model_type', '')} {(c.get('text_config') or {}).get('model_type', '')}".lower()
    archs = " ".join(c.get("architectures") or []).lower()
    if "gpt-oss" in rid or "gpt_oss" in mtype:
        return {"enable_tool_calling": True, "tool_call_parser": "openai", "reasoning_parser": "openai_gptoss"}
    if any(k in rid for k in ("qwen3.5", "qwen3_5", "qwen3-coder", "qwen3-next")) or any(k in mtype for k in ("qwen3_5", "qwen3_next")):
        return {"enable_tool_calling": True, "tool_call_parser": "qwen3_coder", "reasoning_parser": "qwen3"}
    if "qwen3" in rid or "qwen3" in mtype:
        out = {"enable_tool_calling": True, "tool_call_parser": "hermes"}
        return out if "instruct-2507" in rid else {**out, "reasoning_parser": "qwen3"}
    if "qwen2" in rid or "qwen2" in mtype:
        return {"enable_tool_calling": True, "tool_call_parser": "hermes"}
    if ("llama-3" in rid or "llama3" in rid) and "llama" in archs:
        return {"enable_tool_calling": True, "tool_call_parser": "llama3_json"}
    if any(k in rid for k in ("mistral", "ministral", "mixtral", "devstral")):
        return {"enable_tool_calling": True, "tool_call_parser": "mistral"}
    if "deepseek-r1" in rid:
        return {"reasoning_parser": "deepseek_r1"}
    return {}


def analyse(
    *,
    repo_id: str,
    config: dict | None,
    files: list[dict],
    tags: list[str],
    pipeline_tag: str | None,
    params_b: float | None,
    budget_gb: float,
    free_disk_gb: float,
    verified_engines: set[str] | None = None,
    library: str | None = None,
    context: int | None = None,
    concurrency: int = 1,
) -> dict:
    """Return engine verdicts and an overall verdict for a repository."""
    names = [f["path"] for f in files]
    verified_engines = verified_engines or set()
    has_config = config is not None
    st_files = [f for f in files if f["path"].endswith(".safetensors")]
    gguf_files = [f for f in files if f["path"].endswith(".gguf")]
    pickle_files = [f for f in files if f["path"].endswith(PICKLE_EXT)]
    custom_code = bool((config or {}).get("auto_map")) or any(n.endswith(".py") for n in names)
    archs = (config or {}).get("architectures") or []
    quant = detect_quantization(repo_id, config, names)
    common_warn: list[str] = []
    if custom_code:
        common_warn.append("Repository contains custom Python code (auto_map/*.py): requires explicit trust_remote_code approval")
    if pickle_files and not st_files and not gguf_files:
        common_warn.append("Only pickle-based weights (.bin/.pt/.ckpt): executable on load — not loaded by default")

    verdicts: list[EngineVerdict] = []
    is_text = pipeline_tag in (None, "text-generation", "conversational", "text2text-generation",
                               "image-text-to-text", "feature-extraction", "sentence-similarity")

    # ---- vLLM
    if st_files and has_config and is_text:
        wbytes = sum(f.get("size") or 0 for f in st_files)
        est = estimate(weights_bytes=wbytes, params_b=params_b, quant=quant, config=config, context=context,
                       concurrency=concurrency)
        reasons, lim = [], list(common_warn)
        supported = vllm_architectures()
        arch_ok = bool(archs) and (not supported or all(a in supported for a in archs))
        conf = 0.5
        if repo_id in NVIDIA_SPARK_VLLM:
            reasons.append("Listed in NVIDIA DGX Spark vLLM support matrix")
            conf = 0.85
            if repo_id in NVIDIA_CUSTOM_IMAGE:
                lim.append("NVIDIA documents a custom vLLM image for this family; the pinned NGC image may not load it")
                conf = 0.55
        if archs:
            reasons.append(f"Architecture {', '.join(archs)} " + ("is registered in the pinned vLLM image" if arch_ok and supported
                                                                 else "registration unknown (architecture list not loaded)" if not supported
                                                                 else "is NOT registered in the pinned vLLM image"))
        if quant:
            reasons.append(f"Quantisation/precision detected: {quant}")
        verdict = "likely"
        if not archs:
            verdict, conf = "insufficient", 0.2
        elif supported and not arch_ok:
            verdict, conf = "unsupported", 0.8
        if quant == "bnb-4bit":
            verdict = "experimental" if verdict == "likely" else verdict
            lim.append("bitsandbytes 4-bit is slow/limited in vLLM on aarch64; prefer FP8/NVFP4/AWQ variants")
        elif quant and quant not in VLLM_QUANT_OK | {"bf16", "fp16", "fp32", None}:
            lim.append(f"Quantisation '{quant}' support in vLLM unverified")
        if custom_code and verdict == "likely":
            verdict = "experimental"
        if est.total_gb is not None:
            if est.total_gb > budget_gb:
                verdict, conf = "unsupported", 0.9
                reasons.append(f"Estimated {est.total_gb} GB exceeds schedulable memory budget {budget_gb:.0f} GB")
            elif est.total_gb > 0.85 * budget_gb and verdict == "likely":
                verdict = "experimental"
                lim.append(f"Tight fit: {est.total_gb} GB of {budget_gb:.0f} GB budget; other workloads must be stopped")
            else:
                reasons.append(f"Estimated memory {est.total_gb} GB within budget {budget_gb:.0f} GB")
        if "vllm" in verified_engines:
            verdict, conf = "verified", 0.98
            reasons.insert(0, "Smoke test passed on this DGX Spark")
        util = None
        if est.total_gb:
            # vLLM pre-allocates util × total memory; leave KV headroom for ~4 concurrent sequences.
            kv = est.kv_cache_gb or 0
            need = (est.weights_gb or 0) + 4 * kv + (est.overhead_gb or 0) + 2.0
            util = round(min(0.9, max(0.15, need / (budget_gb + 14.0))), 3)
        verdicts.append(EngineVerdict("vllm", verdict, conf, reasons, lim, asdict(est),
                                      {"max_model_len": est.context_length, "gpu_memory_utilization": util,
                                       "max_num_seqs": max(4, concurrency * 4), **parser_defaults(repo_id, config)}))

    # ---- Ollama (GGUF)
    if gguf_files:
        by_quant: dict[str, int] = {}
        for f in gguf_files:
            q = gguf_quant(f["path"]) or "unknown"
            by_quant[q] = by_quant.get(q, 0) + (f.get("size") or 0)
        pref = next((q for q in ("q4_k_m", "q5_k_m", "q8_0", "q4_0", "q6_k") if q in by_quant), next(iter(by_quant)))
        est = estimate(weights_bytes=by_quant[pref], params_b=params_b, quant=pref, config=config, context=context,
                       concurrency=concurrency)
        verdict, conf = "likely", 0.7
        reasons = [f"GGUF files present ({', '.join(sorted(by_quant))}); Ollama can pull hf.co/{repo_id}:{pref.upper()}",
                   "Ollama/llama.cpp supports most GGUF architectures"]
        lim = list(common_warn)
        if est.total_gb and est.total_gb > budget_gb:
            verdict, conf = "unsupported", 0.9
            reasons.append(f"Estimated {est.total_gb} GB exceeds budget {budget_gb:.0f} GB")
        if "ollama" in verified_engines:
            verdict, conf = "verified", 0.98
            reasons.insert(0, "Smoke test passed on this DGX Spark")
        if any("-00001-of-" in f["path"] for f in gguf_files):
            lim.append("Split GGUF detected; Ollama pull of split files may require merge")
        verdicts.append(EngineVerdict("ollama", verdict, conf, reasons, lim, asdict(est),
                                      {"quant": pref.upper(), "num_ctx": est.context_length,
                                       "variants": {k: round(v / GB, 2) for k, v in by_quant.items()}}))

    # ---- ComfyUI (image/video)
    if pipeline_tag in ("text-to-image", "image-to-image", "text-to-video", "image-to-video") or library == "diffusers":
        single = [f for f in st_files if "/" not in f["path"]]
        est = Estimate(disk_gb=round(sum(f.get("size") or 0 for f in st_files) / GB, 2))
        if single:
            est.weights_gb = round(max(f.get("size") or 0 for f in single) / GB, 2)
            est.total_gb = round(est.weights_gb * 1.6 + 3, 2)
            est.basis.append("diffusion: 1.6 × checkpoint + 3 GB working memory (heuristic)")
        verdict = "experimental" if single else "insufficient"
        reasons = ["Diffusion model; usable only through a registered ComfyUI workflow of the same family"]
        if "comfyui" in verified_engines:
            verdict = "verified"
        verdicts.append(EngineVerdict("comfyui", verdict, 0.5, reasons, list(common_warn), asdict(est), {}))

    # ---- Audio (ML worker)
    if pipeline_tag in ("automatic-speech-recognition", "text-to-speech", "text-to-audio"):
        wbytes = sum(f.get("size") or 0 for f in st_files) or None
        est = estimate(weights_bytes=wbytes, params_b=params_b, quant=quant, config=None, context=1)
        verdict = "likely" if (st_files and archs) else "insufficient"
        family_ok = any(a.lower().startswith(("whisper", "vits")) for a in archs)
        if not family_ok and verdict == "likely":
            verdict = "experimental"
        if "mlworker" in verified_engines:
            verdict = "verified"
        verdicts.append(EngineVerdict("mlworker", verdict, 0.6 if family_ok else 0.3,
                                      [f"Audio pipeline '{pipeline_tag}' via transformers in ML worker",
                                       "Whisper and VITS/MMS families have adapters" if family_ok else
                                       "No dedicated adapter for this architecture"],
                                      list(common_warn), asdict(est), {}))

    # ---- Scientific Lab (timm image classifiers via the trusted image_classifier adapter)
    if (library == "timm" or pipeline_tag == "image-classification") and any(f["path"] == "model.safetensors" for f in files):
        wbytes = sum(f.get("size") or 0 for f in st_files)
        est = Estimate(weights_gb=round(wbytes / GB, 3), total_gb=round(wbytes / GB * 3 + 1.0, 2), disk_gb=round(wbytes / GB, 3),
                       basis=["3 × weights + 1 GB (batch inference heuristic)"])
        verdicts.append(EngineVerdict("mlworker", "verified" if "mlworker" in verified_engines else "likely", 0.7,
                                      ["timm safetensors weights: usable through the Scientific Lab 'image_classifier' adapter"],
                                      list(common_warn), asdict(est), {}))

    total_disk = sum(f.get("size") or 0 for f in files) / GB
    order = {"verified": 0, "likely": 1, "experimental": 2, "insufficient": 3, "unsupported": 4}
    best = min(verdicts, key=lambda v: (order[v.verdict], -v.confidence)) if verdicts else None
    overall = best.verdict if best else ("unsupported" if (pickle_files or files) else "insufficient")
    reasons = [] if best else ["No engine adapter accepts this repository's formats/task"]
    if total_disk > free_disk_gb:
        overall = "unsupported"
        reasons.append(f"Repository needs {total_disk:.1f} GB but only {free_disk_gb:.0f} GB free above safety threshold")
    return {
        "repo_id": repo_id,
        "overall": overall,
        "recommended_engine": best.engine if best and overall != "unsupported" else None,
        "reasons": reasons,
        "warnings": common_warn,
        "quantization": quant,
        "architectures": archs,
        "custom_code": custom_code,
        "pickle_only": bool(pickle_files and not st_files and not gguf_files),
        "disk_gb_total": round(total_disk, 2),
        "engines": [asdict(v) for v in verdicts],
    }
