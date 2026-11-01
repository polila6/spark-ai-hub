# Compatibility Matrix (DGX Spark GB10, aarch64, driver 580.142 / CUDA 13.0)

**Verified** = exercised on this machine during deployment (2026-10-29) with a real workload; evidence in TEST_REPORT.md.
**Documented** = NVIDIA lists it for DGX Spark but it was not run here. **Not used** = rejected with reason.

## Platform components

| Component | Version / image | arm64 | Status | Source |
|---|---|---|---|---|
| GPU in containers | NVIDIA Container Toolkit 1.19.0 (`--gpus all`) | ✔ | **Verified** | host |
| Ollama | `ollama/ollama:0.34.4` (CUDA v13 runner, GB10 cc 12.1 detected as iGPU, 121.7 GiB) | ✔ | **Verified** (chat, streaming, OpenAI API, load/unload) | docs.ollama.com |
| vLLM | `nvcr.io/nvidia/vllm:26.09-py3` → vLLM `0.29.0+5013de39.dev`, 376 registered architectures | ✔ | **Verified** (load, chat, streaming, concurrency 4) | NVIDIA dgx-spark-playbooks/nvidia/vllm |
| PyTorch base | `nvcr.io/nvidia/pytorch:26.09-py3` → torch `2.14.0a0+b2c75dd062.nv26.09`, CUDA available on GB10 | ✔ | **Verified** | NGC |
| ComfyUI | v0.42.5 on NGC PyTorch 26.09 (NGC torch preserved) | ✔ | **Verified** (txt2img, img2img, ESRGAN upscale, Wan 2.1 t2v) | dgx-spark-playbooks/nvidia/comfy-ui |
| transformers / timm (ML worker) | transformers 5.22.0, timm 1.0.30 | ✔ | **Verified** (Whisper ASR, VITS TTS, timm ResNet18) | — |
| PostgreSQL | `postgres:16.13` | ✔ | **Verified** (backup/restore test) | Docker Hub |
| Redis | `redis:8.6.8-alpine` | ✔ | **Verified** | Docker Hub |
| Caddy | `caddy:2.11.4-alpine` | ✔ | **Verified** | Docker Hub |
| Backend | Python 3.12.13-slim, FastAPI 0.162, SQLAlchemy 2.1.6, huggingface_hub 0.36.2 (+hf-xet 1.7.1) | ✔ | **Verified** | backend/requirements.txt |
| Frontend | Node 22, Next.js 15.5.28, React 19.1.10, Tailwind 3.4.19 | ✔ | **Verified** (Playwright 1.64.1 arm64) | frontend/package-lock.json |
| Tailscale Serve | 1.104.4, `https:443 → 127.0.0.1:8080`, tailnet only | — | **Verified** | tailscale.com/kb/1312/serve |
| LiteLLM Proxy | — | — | **Not used** (ADR-002: built-in gateway, scheduler-aware, same RBAC) | — |
| faster-whisper / CTranslate2 | — | no CUDA aarch64 wheels | **Not used** (ADR-006) | — |
| pyannote diarisation | — | — | **Not installed** (gated models, not required) | — |

## Models verified on this machine

| Model (revision pinned in registry) | Engine | Result |
|---|---|---|
| `nvidia/Qwen3-8B-FP8` | vLLM | Verified: load 101–136 s, ≈21.9–23.5 GB measured, 26.9 tok/s, TTFT 59 ms |
| `unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M` | Ollama | Verified: load 3–16 s, 7.5–7.8 GB, 81 tok/s, TTFT 33 ms |
| `openai/whisper-large-v3-turbo` | ML worker | Verified: Spanish speech transcribed exactly, language detected |
| `facebook/mms-tts-spa` (CC-BY-NC-4.0) | ML worker | Verified: Spanish speech synthesis |
| `timm/resnet18.a1_in1k` | ML worker (Scientific Lab) | Verified: GPU batch classification |
| `Lykon/DreamShaper` (DreamShaper_8_pruned) | ComfyUI | Verified: 512×512 txt2img ≈3 s, img2img |
| `Comfy-Org/Real-ESRGAN_repackaged` (x4plus) | ComfyUI | Verified: ×4 upscale |
| `Comfy-Org/Wan_2.1_ComfyUI_repackaged` (t2v 1.3B fp16 + umt5 fp8 + VAE) | ComfyUI | Verified: 832×480×33 frames ≈73 s |
| `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` (TI2V 5B) | ComfyUI | Workflow registered, **not installed** (≈18 GB; optional, admin install) |

## vLLM models documented by NVIDIA for Spark (prior for the recommender)

From the vLLM playbook support matrix (updated 2026-06-12): `nvidia/Llama-3.1-8B-Instruct-FP8/NVFP4`,
`nvidia/Qwen3-8B-FP8/NVFP4`, `nvidia/Qwen3-14B-FP8/NVFP4`, `nvidia/Qwen3-32B-NVFP4`, `nvidia/Llama-3.3-70B-Instruct-NVFP4`,
`openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16/FP8`,
`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4`, Phi-4 variants, `Qwen/Qwen3-VL-Embedding-2B`, rerankers. Gemma 4 family
requires a custom vLLM image per NVIDIA (flagged as a limitation by the rules engine). Only `nvidia/Qwen3-8B-FP8` was run here.

## Format → engine rules (deterministic layer, `backend/app/services/compat.py`)

| Artifact | Engine | Notes |
|---|---|---|
| safetensors + `config.json` with architecture registered in the pinned vLLM image | vLLM | FP8/NVFP4/MXFP4/AWQ/GPTQ/compressed-tensors; bnb-4bit → experimental |
| `*.gguf` | Ollama (`hf.co/<repo>:<QUANT>`) | Ollama verifies layer sha256 |
| `.bin/.pt/.pth/.ckpt/.pkl` only | none | pickle = executable; not downloaded by default |
| `auto_map` / `*.py` | experimental | needs admin approval of `trust_remote_code` |
| diffusion single-file safetensors | ComfyUI | only via registered workflows of the same family |
| Whisper / VITS (transformers) | ML worker | audio adapters |
| timm image classifiers (safetensors) | ML worker | Scientific Lab `image_classifier` adapter |
