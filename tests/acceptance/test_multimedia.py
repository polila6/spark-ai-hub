"""Multimedia acceptance: image (17), video/multimedia artifacts (18), audio round-trip, CV adapter (19)."""
import io
import zipfile

import pytest

from client import Hub
from test_acceptance import PW




def _model(h, pred):
    ms = [m for m in h.get("/api/v1/models").json() if pred(m)]
    if not ms:
        pytest.skip("required model not installed")
    return ms[0]


def test_17_image_generation_via_studio(alice):
    r = alice.post("/api/v1/media/generate", json={"workflow_id": "sd15-txt2img", "inputs": {
        "prompt": "a golden retriever sitting on grass, photo", "seed": 7, "steps": 20, "width": 512, "height": 512}})
    assert r.status_code == 200, r.text
    j = alice.wait_job(r.json()["job_id"], timeout=1800)
    assert j["status"] == "completed", j["error"]
    img = [a for a in j["artifacts"] if a["kind"] == "image"][0]
    data = alice.get(img["url"]).content
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 50_000
    assert img["meta"]["params"]["seed"] == 7 and img["meta"]["workflow"] == "sd15-txt2img"
    print("image", img["meta"]["generation_seconds"], "s")


def test_17b_img2img_and_upscale(alice):
    up = alice.post("/api/v1/media/generate", json={"workflow_id": "sd15-txt2img", "inputs": {"prompt": "a red apple", "seed": 3, "steps": 12}})
    j = alice.wait_job(up.json()["job_id"], timeout=900)
    src = [a for a in j["artifacts"] if a["kind"] == "image"][0]
    png = alice.get(src["url"]).content
    a = alice.post("/api/v1/media/uploads?kind=image", files={"file": ("apple.png", png, "image/png")}).json()
    r = alice.post("/api/v1/media/generate", json={"workflow_id": "upscale-esrgan-x4", "inputs": {"image": a["id"]}})
    j = alice.wait_job(r.json()["job_id"], timeout=900)
    assert j["status"] == "completed", j["error"]
    out = [x for x in j["artifacts"] if x["kind"] == "image"][0]
    assert out["size_bytes"] > len(png)
    r = alice.post("/api/v1/media/generate", json={"workflow_id": "sd15-img2img", "inputs": {"image": a["id"], "prompt": "a green apple", "denoise": 0.6, "steps": 12}})
    j = alice.wait_job(r.json()["job_id"], timeout=900)
    assert j["status"] == "completed", j["error"]


def test_18_video_generation_job(alice):
    r = alice.post("/api/v1/media/generate", json={"workflow_id": "wan21-t2v-1.3b", "inputs": {
        "prompt": "waves crashing on a beach, cinematic", "seed": 11, "width": 832, "height": 480, "frames": 33, "fps": 16, "steps": 30}})
    assert r.status_code == 200, r.text
    assert r.json()["estimate"]["gpu_heavy"] is True
    j = alice.wait_job(r.json()["job_id"], timeout=3600, poll=10)
    assert j["status"] == "completed", j["error"]
    v = [a for a in j["artifacts"] if a["kind"] == "video"][0]
    assert v["mime"] == "image/webp" and v["meta"]["frames"] == 33 and v["meta"]["duration_s"] == pytest.approx(2.06, 0.01)
    assert any(a["kind"] == "workflow" for a in j["artifacts"])
    print("video", v["meta"]["generation_seconds"], "s", v["size_bytes"], "bytes")


def test_audio_tts_then_asr_roundtrip(alice):
    tts = _model(alice, lambda m: m["task"] == "text-to-speech" and m["status"] in ("installed", "ready"))
    asr = _model(alice, lambda m: m["task"] == "automatic-speech-recognition" and m["status"] in ("installed", "ready"))
    text = "Hola, esto es una prueba de la plataforma de inteligencia artificial."
    j = alice.wait_job(alice.post("/api/v1/media/audio/tts", json={"text": text, "model_id": tts["id"]}).json()["job_id"], timeout=900)
    assert j["status"] == "completed", j["error"]
    wav = [a for a in j["artifacts"] if a["kind"] == "audio"][0]
    audio = alice.get(wav["url"]).content
    assert audio[:4] == b"RIFF"
    up = alice.post("/api/v1/media/uploads?kind=audio", files={"file": ("speech.wav", audio, "audio/wav")}).json()
    j = alice.wait_job(alice.post("/api/v1/media/audio/transcribe", json={"artifact_id": up["id"], "model_id": asr["id"]}).json()["job_id"], timeout=900)
    assert j["status"] == "completed", j["error"]
    got = j["result"]["text"].lower()
    print("ASR:", got, "| language:", j["result"]["language"])
    assert "prueba" in got and "plataforma" in got
    assert {"transcript.txt", "transcript.json", "transcript.srt"} <= {a["filename"] for a in j["artifacts"]}


def test_19b_cv_adapter_on_generated_images(alice):
    src = _model(alice, lambda m: (m["repo_id"] or "").startswith("timm/resnet18") and m["source"] == "hf")
    classes = {"golden retriever": "a photo of a golden retriever dog", "tabby": "a photo of a tabby cat"}
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        for folder, prompt in classes.items():
            for seed in (1, 2, 3):
                j = alice.wait_job(alice.post("/api/v1/media/generate", json={"workflow_id": "sd15-txt2img", "inputs": {
                    "prompt": prompt, "seed": seed, "steps": 20}}).json()["job_id"], timeout=900, verbose=False)
                img = [a for a in j["artifacts"] if a["kind"] == "image"][0]
                z.writestr(f"{folder}/{seed}.png", alice.get(img["url"]).content)
    ds = alice.post("/api/v1/datasets", files={"file": ("pets.zip", zbuf.getvalue(), "application/zip")},
                    data={"name": "generated-pets"}).json()
    assert ds["schema"]["images"] == 6
    m = alice.post("/api/v1/science/models/from-hf", json={"source_model_id": src["id"], "name": "resnet18-imagenet",
                                                           "adapter": "image_classifier"})
    assert m.status_code == 200, m.text
    e = alice.post("/api/v1/experiments", json={"name": "resnet18 on generated pets", "model_id": m.json()["id"],
                                                "dataset_id": ds["id"]}).json()
    j = alice.wait_job(alice.post(f"/api/v1/experiments/{e['id']}/run", json={}).json()["job_id"], timeout=900)
    assert j["status"] == "completed", j["error"]
    print("CV metrics", j["result"]["metrics"], j["result"]["device"])
    assert j["result"]["metrics"]["images"] == 6 and j["result"]["metrics"]["top5_accuracy"] >= 0.5
