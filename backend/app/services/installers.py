"""Install recipes: what each way of installing a Hugging Face repository needs, in one place.

A recipe knows (1) whether it can take a repository at all, (2) which files it downloads and (3) what it leaves
the model as. Adding a way to install something new is one entry in INSTALLERS plus, if it needs one, an engine.
Everything here is pure (no database, no network) so it is unit tested.

`files` is the recipe that always applies: it downloads whatever you choose and registers it without loading it
into any engine. It is the way out for repositories that no engine takes by itself (sharded GGUF, adapters, tokenizers,
datasets, checkpoints for external tools).
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from . import compat


@dataclass(frozen=True)
class Installer:
    engine: str
    label: str
    summary: str


INSTALLERS: dict[str, Installer] = {i.engine: i for i in (
    Installer("vllm", "vLLM", "Transformers models in safetensors; loaded on demand by the vLLM container"),
    Installer("ollama", "Ollama", "GGUF models; Ollama downloads and keeps them"),
    Installer("comfyui", "ComfyUI", "Image/video checkpoints used through a ComfyUI workflow"),
    Installer("mlworker", "ML worker", "Speech and classifier models run by the ML worker"),
    Installer("files", "Solo archivos", "Download the chosen files and register them without loading them into any engine"),
)}
DEFAULT_ORDER = tuple(INSTALLERS)


def _paths(files: list[dict], suffix: tuple[str, ...]) -> list[dict]:
    return [f for f in files if f["path"].endswith(suffix)]


def prerequisite(engine: str, files: list[dict], config: dict | None) -> str | None:
    """Why this recipe cannot take the repository at all (even forced), or None."""
    if engine == "files":
        return None if files else "The repository has no files"
    st = _paths(files, (".safetensors",))
    if engine == "vllm":
        return None if st and config is not None else "vLLM needs safetensors weights and a config.json"
    if engine == "mlworker":
        return None if st else "The ML worker needs safetensors weights"
    if engine == "comfyui":
        return None if any("/" not in f["path"] for f in st) else "ComfyUI needs a single .safetensors checkpoint at the top level"
    if engine == "ollama":
        return None if gguf_variants(files) else "Ollama needs GGUF files"
    return f"Unknown way of installing: {engine}"


def listing(analysis: dict, files: list[dict], config: dict | None) -> list[dict]:
    """Every recipe with what to expect: `detected` = an engine verdict exists, `usable` = it can be chosen at all,
    `needs_force` = the verdict is 'unsupported' (an administrator may still go ahead)."""
    verdicts = {e["engine"]: e for e in analysis.get("engines", [])}
    out = []
    for engine in DEFAULT_ORDER:
        rec = INSTALLERS[engine]
        why = prerequisite(engine, files, config)
        v = verdicts.get(engine)
        out.append({
            "engine": engine, "label": rec.label, "summary": rec.summary,
            "detected": v is not None, "verdict": v["verdict"] if v else None,
            "usable": why is None, "unusable_reason": why,
            "needs_force": bool(v and v["verdict"] == "unsupported") or (v is None and why is None and engine != "files"),
            "recommended": engine == analysis.get("recommended_engine"),
        })
    return out


def gguf_variants(files: list[dict]) -> dict[str, dict]:
    """Quantisation → {bytes, split, files}. A quantisation is split when its GGUF comes in numbered shards."""
    out: dict[str, dict] = {}
    for f in _paths(files, (".gguf",)):
        q = compat.gguf_quant(f["path"]) or "unknown"
        v = out.setdefault(q, {"bytes": 0, "split": False, "files": []})
        v["bytes"] += f.get("size") or 0
        v["split"] = v["split"] or bool(compat.SPLIT_GGUF.search(f["path"]))
        v["files"].append(f["path"])
    return out


def pick_gguf(files: list[dict], wanted: str | None, preferred: str | None = None) -> tuple[str | None, str | None]:
    """(quantisation, problem). Ollama cannot pull split GGUF from Hugging Face, so a split choice is a problem."""
    variants = gguf_variants(files)
    if not variants:
        return None, "Ollama needs GGUF files"
    quant = (wanted or preferred or "").lower() or None
    if quant is None or quant not in variants:
        whole = [q for q in variants if not variants[q]["split"]] or list(variants)
        quant = next((q for q in ("q4_k_m", "q5_k_m", "q8_0", "q4_0", "q6_k") if q in whole), whole[0])
    if variants[quant]["split"]:
        return quant, (f"The {quant.upper()} GGUF is split into shards and Ollama cannot pull split files from "
                       "Hugging Face. Install it as files, or choose another quantisation")
    return quant, None


def select_files(files: list[dict], engine: str, include: list[str] | None = None, exclude: list[str] | None = None) -> list[dict]:
    """Files to download. Explicit patterns win; otherwise each recipe keeps what its engine reads."""
    if include:
        chosen = [f for f in files if any(fnmatch.fnmatch(f["path"], pat) for pat in include)]
    elif engine in ("vllm", "mlworker"):
        has_st = any(f["path"].endswith(".safetensors") for f in files)
        chosen = []
        for f in files:
            n = f["path"]
            if n.endswith(compat.PICKLE_EXT) and has_st:
                continue  # the safetensors copy is the same weights
            if n.endswith((".gguf", ".onnx", ".msgpack", ".h5", ".ot")) or n.startswith(("onnx/", "openvino/")):
                continue
            if n.endswith(compat.PICKLE_EXT):
                continue  # never download pickles by default
            chosen.append(f)
    elif engine == "comfyui":
        chosen = [f for f in files if f["path"].endswith(".safetensors") and "/" not in f["path"]][:1]
    else:  # files / anything else: everything, except pickles that have a safetensors twin
        has_st = any(f["path"].endswith(".safetensors") for f in files)
        chosen = [f for f in files if not (has_st and f["path"].endswith(compat.PICKLE_EXT))]
    if exclude:
        chosen = [f for f in chosen if not any(fnmatch.fnmatch(f["path"], pat) for pat in exclude)]
    return chosen
