"""Spark AI Hub ML worker: isolated GPU service for audio and trusted scientific adapters.

Runs as uid 1000 on an internal network without egress, with read-only access to model weights
and datasets. It never unpickles uploaded files: weights are loaded with safetensors only.
"""
from __future__ import annotations

import csv
import gc
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Spark AI Hub ML worker", docs_url=None, redoc_url=None)
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
OUT_ROOT = Path("/data/outputs")
ALLOWED_ROOTS = (Path("/models/hf"), Path("/models/custom"), Path("/data/datasets"), OUT_ROOT)
_cache: "OrderedDict[str, object]" = OrderedDict()
_lock = threading.Lock()
_cancel: set[str] = set()


def _check(path: str, roots=ALLOWED_ROOTS) -> Path:
    p = Path(path).resolve()
    if not any(p == r or r in p.parents for r in roots):
        raise HTTPException(400, f"path not allowed: {path}")
    return p


def _cached(key: str, loader):
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
        while len(_cache) >= 2:
            _cache.popitem(last=False)
            gc.collect()
            torch.cuda.empty_cache()
        obj = loader()
        _cache[key] = obj
        return obj


@app.get("/health")
def health():
    return {"device": DEVICE, "torch": torch.__version__,
            "cuda": torch.version.cuda, "gpu": torch.cuda.get_device_name(0) if DEVICE == "cuda" else None,
            "cached_models": list(_cache.keys())}


@app.post("/unload")
def unload():
    with _lock:
        _cache.clear()
    gc.collect()
    torch.cuda.empty_cache()
    return {"ok": True}


@app.post("/cancel/{job_id}")
def cancel(job_id: str):
    _cancel.add(job_id)
    return {"ok": True}


# ------------------------------------------------------------------ audio


class AsrIn(BaseModel):
    job_id: str
    model_path: str
    input: str
    output_dir: str
    language: str | None = None
    task: str = "transcribe"
    timestamps: bool = True


def _srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


@app.post("/asr")
def asr(body: AsrIn):
    from transformers import pipeline

    mp = _check(body.model_path, (Path("/models/hf"),))
    out = _check(body.output_dir, (OUT_ROOT,))
    out.mkdir(parents=True, exist_ok=True)
    pipe = _cached(f"asr:{mp}", lambda: pipeline("automatic-speech-recognition", model=str(mp),
                                                 torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
                                                 device=DEVICE))
    if body.input == "__synthetic_tone__":
        sr = 16000
        t = np.linspace(0, 2, 2 * sr, endpoint=False)
        audio = {"raw": (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), "sampling_rate": sr}
        duration = 2.0
    else:
        src = _check(body.input, (OUT_ROOT,))
        from transformers.pipelines.audio_utils import ffmpeg_read

        raw = ffmpeg_read(src.read_bytes(), 16000)
        audio = {"raw": raw, "sampling_rate": 16000}
        duration = len(raw) / 16000
    gen = {"task": body.task}
    if body.language:
        gen["language"] = body.language
    t0 = time.time()
    res = pipe(audio, chunk_length_s=30, batch_size=8, return_timestamps=body.timestamps, generate_kwargs=gen,
               return_language=True)
    dt = time.time() - t0
    chunks = res.get("chunks") or []
    lang = next((c.get("language") for c in chunks if c.get("language")), None) or body.language
    segs = [{"start": (c.get("timestamp") or [None, None])[0], "end": (c.get("timestamp") or [None, None])[1],
             "text": c.get("text", "").strip()} for c in chunks]
    files = []
    if body.input != "__synthetic_tone__":
        (out / "transcript.txt").write_text(res["text"].strip() + "\n", encoding="utf-8")
        (out / "transcript.json").write_text(json.dumps({"text": res["text"], "language": lang, "segments": segs},
                                                        ensure_ascii=False, indent=2), encoding="utf-8")
        files = ["transcript.txt", "transcript.json"]
        if body.timestamps and segs:
            lines = []
            for i, s in enumerate(segs, 1):
                if s["start"] is None:
                    continue
                end = s["end"] if s["end"] is not None else s["start"] + 2
                lines += [str(i), f"{_srt_time(s['start'])} --> {_srt_time(end)}", s["text"], ""]
            (out / "transcript.srt").write_text("\n".join(lines), encoding="utf-8")
            files.append("transcript.srt")
    return {"text": res["text"].strip(), "language": lang, "segments": segs, "duration_s": round(duration, 2),
            "processing_s": round(dt, 2), "realtime_factor": round(dt / duration, 3) if duration else None,
            "files": files, "device": DEVICE}


class TtsIn(BaseModel):
    job_id: str
    model_path: str
    text: str
    output_dir: str
    seed: int = 0
    speaking_rate: float = 1.0


@app.post("/tts")
def tts(body: TtsIn):
    import soundfile as sf
    from transformers import AutoTokenizer, VitsModel

    mp = _check(body.model_path, (Path("/models/hf"),))
    out = _check(body.output_dir, (OUT_ROOT,))
    out.mkdir(parents=True, exist_ok=True)

    def load():
        return VitsModel.from_pretrained(str(mp)).to(DEVICE).eval(), AutoTokenizer.from_pretrained(str(mp))

    model, tok = _cached(f"tts:{mp}", load)
    model.speaking_rate = body.speaking_rate
    torch.manual_seed(body.seed)
    t0 = time.time()
    with torch.inference_mode():
        inputs = tok(body.text, return_tensors="pt").to(DEVICE)
        wav = model(**inputs).waveform[0].float().cpu().numpy()
    sr = model.config.sampling_rate
    sf.write(out / "speech.wav", wav, sr)
    return {"files": ["speech.wav"], "sample_rate": sr, "duration_s": round(len(wav) / sr, 2),
            "processing_s": round(time.time() - t0, 2), "device": DEVICE}


# ------------------------------------------------------------------ scientific adapters


class SciIn(BaseModel):
    job_id: str
    adapter: str
    model_dir: str
    dataset: str
    output_dir: str
    options: dict = {}


def _load_table(path: Path, columns: list[str]) -> tuple[np.ndarray, list[dict]]:
    if path.suffix == ".npy":
        arr = np.load(path, allow_pickle=False)
        return arr.astype(np.float32), [{} for _ in range(len(arr))]
    if path.suffix in (".json", ".jsonl"):
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.suffix == ".jsonl" \
            else json.loads(path.read_text())
    else:
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    missing = [c for c in columns if c not in rows[0]]
    if missing:
        raise HTTPException(422, f"dataset lacks feature columns: {missing}")
    x = np.array([[float(r[c]) for c in columns] for r in rows], dtype=np.float32)
    return x, rows


def adapter_linear_regression(body: SciIn, mdir: Path, ds: Path, out: Path) -> dict:
    from safetensors.torch import load_file

    cfg = json.loads((mdir / "config.json").read_text())
    w = load_file(str(mdir / "model.safetensors"), device=DEVICE)
    x, rows = _load_table(ds, cfg["feature_columns"])
    if cfg.get("x_mean") is not None:
        x = (x - np.array(cfg["x_mean"], dtype=np.float32)) / np.array(cfg["x_std"], dtype=np.float32)
    xt = torch.from_numpy(x).to(DEVICE)
    with torch.inference_mode():
        if cfg.get("hidden"):
            h = torch.relu(xt @ w["fc1.weight"].T + w["fc1.bias"])
            y = h @ w["fc2.weight"].T + w["fc2.bias"]
        else:
            y = xt @ w["weight"].T + w["bias"]
    pred = y.float().cpu().numpy()
    targets = cfg.get("target_names") or [f"y{i}" for i in range(pred.shape[1])]
    with open(out / "predictions.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["row"] + [f"pred_{t}" for t in targets])
        for i, r in enumerate(pred):
            wr.writerow([i] + [f"{v:.6g}" for v in r])
    metrics = {}
    if rows and all(t in rows[0] for t in targets):
        yt = np.array([[float(r[t]) for t in targets] for r in rows], dtype=np.float64)
        err = pred - yt
        ss_res = (err ** 2).sum(0)
        ss_tot = ((yt - yt.mean(0)) ** 2).sum(0)
        metrics = {t: {"mse": float((err[:, i] ** 2).mean()), "mae": float(np.abs(err[:, i]).mean()),
                       "r2": float(1 - ss_res[i] / ss_tot[i]) if ss_tot[i] > 0 else None} for i, t in enumerate(targets)}
    return {"rows": int(len(pred)), "metrics": metrics, "files": ["predictions.csv"]}


def adapter_image_classifier(body: SciIn, mdir: Path, ds: Path, out: Path) -> dict:
    import timm
    from PIL import Image
    from safetensors.torch import load_file
    from timm.data import create_transform, resolve_data_config

    cfg = json.loads((mdir / "config.json").read_text())
    arch = cfg["architecture"]
    ncls = int(cfg.get("num_classes") or 1000)

    def load():
        m = timm.create_model(arch, pretrained=False, num_classes=ncls)
        m.load_state_dict(load_file(str(mdir / "model.safetensors")))
        return m.to(DEVICE).eval()

    model = _cached(f"cv:{mdir}", load)
    dc = resolve_data_config({k: cfg[k] for k in ("input_size", "mean", "std", "interpolation", "crop_pct") if k in cfg},
                             model=model)
    tf = create_transform(**dc)
    labels = cfg.get("label_names")
    if not labels and ncls == 1000:
        try:
            from timm.data import ImageNetInfo

            info = ImageNetInfo()
            labels = [info.index_to_description(i) for i in range(1000)]
        except Exception:  # noqa: BLE001
            labels = None
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    files = sorted(p for p in ds.rglob("*") if p.suffix.lower() in exts)
    bs = int(body.options.get("batch_size", 32))
    top1 = top5 = labelled = 0
    torch.cuda.reset_peak_memory_stats() if DEVICE == "cuda" else None
    with open(out / "predictions.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["file", "top1_label", "top1_prob", "top5"])
        for i in range(0, len(files), bs):
            if body.job_id in _cancel:
                raise HTTPException(409, "cancelled")
            batch = files[i:i + bs]
            imgs = torch.stack([tf(Image.open(p).convert("RGB")) for p in batch]).to(DEVICE)
            with torch.inference_mode(), torch.autocast(DEVICE, dtype=torch.bfloat16, enabled=DEVICE == "cuda"):
                probs = model(imgs).float().softmax(-1)
            p5, i5 = probs.topk(5, dim=-1)
            for path, pp, ii in zip(batch, p5.cpu().tolist(), i5.cpu().tolist()):
                names = [labels[j] if labels else str(j) for j in ii]
                wr.writerow([str(path.relative_to(ds)), names[0], f"{pp[0]:.4f}",
                             "; ".join(f"{n}:{v:.3f}" for n, v in zip(names, pp))])
                folder = path.parent.name.lower() if path.parent != ds else None
                if folder:
                    labelled += 1
                    top1 += int(folder in names[0].lower())
                    top5 += int(any(folder in n.lower() for n in names))
    metrics = {"images": len(files)}
    if labelled:
        metrics.update(labelled=labelled, top1_accuracy=round(top1 / labelled, 4), top5_accuracy=round(top5 / labelled, 4),
                       note="label match = folder name contained in predicted label name")
    return {"rows": len(files), "metrics": metrics, "files": ["predictions.csv"]}


ADAPTERS = {"linear_regression": adapter_linear_regression, "image_classifier": adapter_image_classifier}


@app.post("/sci/infer")
def sci_infer(body: SciIn):
    fn = ADAPTERS.get(body.adapter)
    if not fn:
        raise HTTPException(400, f"unknown adapter {body.adapter}")
    mdir = _check(body.model_dir, (Path("/models/custom"),))
    ds = _check(body.dataset, (Path("/data/datasets"),))
    out = _check(body.output_dir, (OUT_ROOT,))
    out.mkdir(parents=True, exist_ok=True)
    if DEVICE == "cuda":
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    res = fn(body, mdir, ds, out)
    res["processing_s"] = round(time.time() - t0, 3)
    res["device"] = DEVICE
    res["peak_memory_mb"] = round(torch.cuda.max_memory_allocated() / 1e6, 1) if DEVICE == "cuda" else None
    (out / "metrics.json").write_text(json.dumps({k: res[k] for k in ("metrics", "rows", "processing_s", "device",
                                                                      "peak_memory_mb")}, indent=2))
    res["files"].append("metrics.json")
    _cancel.discard(body.job_id)
    return res
