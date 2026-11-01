"""Trusted scientific adapter specifications (validated server-side before registration).

Adapters are code shipped with Spark AI Hub and executed only inside the isolated ML worker.
Uploaded artifacts are *data*: safetensors weights (header-validated) and JSON configs.
Pickle-based checkpoints are rejected. See docs/MODEL_ADAPTERS.md for the plugin contract.
"""
from __future__ import annotations

from pathlib import Path

from .uploads import UploadError, safetensors_header

ADAPTERS = {
    "linear_regression": {
        "name": {"es": "Regresión lineal / MLP tabular", "en": "Linear regression / tabular MLP"},
        "description": "Tabular regression: y = W·x + b (optionally a 2-layer MLP). Input CSV/NPY with feature columns.",
        "required_files": ["model.safetensors", "config.json"],
        "config_schema": {"feature_columns": "list[str]", "target_names": "list[str]",
                          "hidden": "int|null", "x_mean": "list[float]|null", "x_std": "list[float]|null"},
        "dataset_formats": [".csv", ".npy", ".json", ".jsonl"],
        "metrics": ["mse", "mae", "r2 (if target columns present)"],
    },
    "image_classifier": {
        "name": {"es": "Clasificador de imágenes (timm)", "en": "Image classifier (timm)"},
        "description": "Computer-vision classification with a timm architecture from safetensors weights. "
                       "Input: ZIP of images (optionally class folders for accuracy).",
        "required_files": ["model.safetensors", "config.json"],
        "config_schema": {"architecture": "str (timm name, e.g. resnet18)", "num_classes": "int",
                          "label_names": "list[str]|null", "input_size": "[3,H,W]", "mean": "list", "std": "list"},
        "dataset_formats": [".zip"],
        "metrics": ["top1/top5 accuracy (if folder labels match label_names)"],
    },
}

TIMM_ALLOWED = {"resnet18", "resnet34", "resnet50", "efficientnet_b0", "mobilenetv3_large_100", "convnext_tiny",
                "vit_small_patch16_224", "vit_base_patch16_224", "regnety_016"}


def validate_artifacts(adapter: str, model_dir: Path, config: dict) -> dict:
    spec = ADAPTERS.get(adapter)
    if not spec:
        raise UploadError("unknown_adapter", f"Unknown adapter '{adapter}'")
    for f in spec["required_files"]:
        if not (model_dir / f).exists():
            raise UploadError("missing_file", f"Adapter '{adapter}' requires {f}")
    tensors = safetensors_header(model_dir / "model.safetensors")
    if adapter == "linear_regression":
        fc = config.get("feature_columns")
        if not isinstance(fc, list) or not fc:
            raise UploadError("invalid_config", "config.feature_columns must be a non-empty list")
        names = set(tensors)
        if config.get("hidden"):
            need = {"fc1.weight", "fc1.bias", "fc2.weight", "fc2.bias"}
        else:
            need = {"weight", "bias"}
        if not need <= names:
            raise UploadError("invalid_weights", f"Expected tensors {sorted(need)}, found {sorted(names)[:10]}")
        first = tensors["fc1.weight" if config.get("hidden") else "weight"]["shape"]
        if first[1] != len(fc):
            raise UploadError("shape_mismatch", f"Weight expects {first[1]} features, config lists {len(fc)}")
    elif adapter == "image_classifier":
        arch = config.get("architecture")
        if arch not in TIMM_ALLOWED:
            raise UploadError("invalid_config", f"architecture must be one of {sorted(TIMM_ALLOWED)}")
    return {"tensors": len(tensors), "parameters": sum(_numel(v["shape"]) for v in tensors.values())}


def _numel(shape: list[int]) -> int:
    n = 1
    for s in shape:
        n *= s
    return n
