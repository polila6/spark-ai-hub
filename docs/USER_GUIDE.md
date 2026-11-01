# User guide (researchers)

Access: https://your-spark.your-tailnet.ts.net from a device in the lab's Tailscale network. Accounts are created by an
administrator (invitation link). The interface is in Spanish by default; switch to English with the **ES/EN** button.

## Pages

| Page | What you can do |
|---|---|
| **Panel** | Live CPU / unified memory / GPU / disk, resident models, engines, your queue, alerts |
| **Descubrir modelos** | Search Hugging Face (task, library, quantisation filters), read model cards, files and revisions, see the deterministic compatibility analysis; **Recomendador**: give task + limits and get explained recommendations |
| **Biblioteca** | Installed models, state, memory (measured vs estimated), load/unload, smoke test, favourites (★), downloads you requested |
| **AI Playground** | Private chats with streaming, system prompt, temperature, token limit, TTFT/tokens-per-second per answer, saved prompts, export (Markdown/JSON) |
| **Comparar modelos** | Same prompt on 2–6 models, executed one after another; metrics, optional blinded evaluation, export, re-run |
| **Benchmarks** | Run versioned performance/quality suites; charts, details, CSV/JSON export |
| **Image Studio** | Text-to-image, image-to-image, inpainting (transparent area = mask), ×4 upscaling; seed/steps/CFG/sampler/size; history with metadata |
| **Video Studio** | Text-to-video (Wan 2.1 1.3B) as a background job; frames, fps, resolution, seed; animated WebP output |
| **Audio Studio** | Transcription (Whisper large-v3-turbo: language detection, translation to English, TXT/JSON/SRT) and Spanish TTS (MMS-TTS, CC-BY-NC licence) |
| **Laboratorio científico** | Upload datasets (CSV/JSON/JSONL/NPY/NPZ, image ZIP), register models for trusted adapters (safetensors only), create experiments, run batch inference on GPU, download predictions/metrics |
| **Trabajos** | Your jobs: progress, events, requested vs measured resources, artifacts, cancel, retry |
| **Centro API** | Create/revoke personal API keys, examples (OpenAI SDK, cURL, async jobs), test console, usage |
| **Almacenamiento** | Disk status and your quota usage |
| **Ajustes** | Language, theme, password, active sessions |

## Good practice

* Large models take time to load the first time (vLLM ~2 min). The scheduler unloads idle models automatically; if
  memory is busy your request/job waits and the reason is shown.
* Only one large vLLM model is resident at a time; small Ollama models can coexist.
* Everything you create is **private** unless you change its visibility. Other researchers cannot see your chats,
  datasets, outputs or experiments.
* Do not upload sensitive personal data unless your project allows it; the platform stores files on the DGX disk.
* Pickle checkpoints (`.pt/.pth/.ckpt/.bin`) are rejected for safety — convert to safetensors (docs/MODEL_ADAPTERS.md).
