# Benchmarks

## Suites (versioned, `backend/app/services/benchsuites.py`)

| Suite | Kind | Definition | Scoring |
|---|---|---|---|
| `perf-short-v1` | performance | fixed short prompt, 128 output tokens, 3 repeats at concurrency 1 and 4, 1 warm-up excluded | — |
| `perf-long-v1` | performance | ~2k-token prompt (prefill-heavy), 256 output tokens, 2 repeats | — |
| `arith-exact-v1` | quality | 20 integer arithmetic items | exact integer match (last integer in answer, `<think>` stripped) |
| `json-format-v1` | instruction following | 10 items: return JSON with exactly `name`, `category` | valid JSON + exact key set |
| `mcq-general-v1` | factual | 12 multiple-choice items | letter exact match |

Quality suites are small and narrow: they measure specific abilities, not general model quality. **No LLM judge is
used**; any future judge-based suite must be labelled as subjective.

## Metrics (client-side, streaming)

* **TTFT**: request start → first content/reasoning token.
* **tokens/s (decode)**: `(completion_tokens − 1) / (latency − TTFT)`; `completion_tokens` from the engine usage block
  (`token_count_source = engine_usage`); falls back to chunk counting, flagged as approximation.
* **throughput (concurrency c)**: Σ completion tokens of the batch / wall time of the slowest request.
* **latency**, **error rate**, p50/p90.
* Memory: measured footprint of the resident model (Ollama `/api/ps` or MemAvailable delta) and the scheduler reservation.

Each run stores the model snapshot (alias, repo, **revision**, backend, quantisation, runtime params), the config
(temperature, seed) and the environment (GPU, driver, engine version/image, load time) in PostgreSQL
(`benchmark_runs`, `benchmark_samples`). Export: CSV/JSON per run (`/api/v1/benchmarks/{id}/export`).

## Measured on a DGX Spark GB10 (2026-10-29)

| Model | Engine | Suite | Result |
|---|---|---|---|
| qwen3-4b-instruct (unsloth GGUF Q4_K_M) | Ollama 0.34.4 | perf-short-v1 | TTFT p50 **32.7 ms**, **81.2 tok/s** (c=1); 68.1 tok/s per stream, **253 tok/s** aggregate (c=4); 0 errors |
| qwen3-4b-instruct | Ollama | arith-exact-v1 | **95 %** (19/20) |
| qwen3-4b-instruct | Ollama | json-format-v1 | **100 %** (10/10) |
| qwen3-8b-fp8 (nvidia/Qwen3-8B-FP8) | vLLM 0.29 (NGC 26.09) | perf-short-v1 | TTFT p50 **59.4 ms**, **26.9 tok/s** (c=1); **112 tok/s** aggregate (c=4); 0 errors |
| qwen3-8b-fp8 | vLLM | mcq-general-v1 | **83.3 %** (10/12) |

Load times measured: Ollama 3.1–16 s (cold vs warm page cache), vLLM 101–136 s (includes torch.compile/CUDA graph capture;
cached afterwards in `/srv/spark-ai-hub/cache/vllm`). Repeated runs show run-to-run variance (e.g. an earlier
qwen3-4b perf run measured 39.2 tok/s while another model was resident) — compare runs under the same conditions.

Multimedia (measured): SD 1.5 (DreamShaper 8) 512×512, 20 steps ≈ **3.1 s**; Wan 2.1 1.3B text-to-video 832×480,
33 frames, 30 steps ≈ **73 s**; Whisper large-v3-turbo / MMS-TTS: see TEST_REPORT.
