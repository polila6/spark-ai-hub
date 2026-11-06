# Troubleshooting

| Symptom | Diagnosis | Fix |
|---|---|---|
| Page does not open | Device not on tailnet / Serve off | `tailscale status` on the client; on the Spark `tailscale serve status` must show `https://your-spark… (tailnet only)` → `http://127.0.0.1:8080` |
| 502 from the proxy | backend or frontend down | `scripts/sah.sh status`; `scripts/sah.sh logs backend` |
| "Waiting for resources: …" on a job | Unified-memory budget or GPU-heavy slot busy | Normal queueing. Unload idle models (Biblioteca), lower `system_reserve_gb` carefully, or wait. Message states the exact reason |
| Chat/API returns 503 `insufficient_resources` | Model does not fit while others are busy/pinned | Unload/unpin another model; retry after `Retry-After` |
| vLLM model fails to load | See model history (Biblioteca → modelo → Historial) and `Monitorización → contenedores → logs` | OOM → lower `max_model_len`/`max_num_seqs`/`gpu_memory_utilization`; "trust_remote_code" → admin approval; architecture not supported → use Ollama (GGUF) |
| Memory looks used but nothing loaded | Page cache on UMA (NVIDIA known issue) | NVIDIA guidance: `sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'` (host, admin only) |
| Download failed (network/Hub) | Job events show retries | Biblioteca → Descargas → Reanudar (partial files are kept); gated repo → set HF token |
| "Integrity verification failed" | sha256 mismatch or missing file | Resume (re-downloads mismatched blobs); never ignored silently |
| ComfyUI workflow "not installed" | Missing model files or nodes | Admin: Image/Video Studio → Instalar modelos del flujo |
| Image/video job fails | Job events contain the ComfyUI error | Lower resolution/frames; check `sah-comfyui` logs |
| Audio job fails | ML worker logs | `docker logs sah-mlworker`; unsupported audio format → convert to WAV/MP3 |
| Job stuck "running" after a crash | Worker reconciles every 30 s (stale heartbeat > 45 s) | Idempotent jobs are re-queued; others become `interrupted` → Retry |
| Login locked | 8 failed attempts → 15 min lock | Admin: Usuarios → Enlace de restablecimiento (also clears the lock) |
| Lost admin password | — | On the Spark: `scripts/sah.sh reset-link admin` prints a one-time reset link valid for 1 h (also clears the lock) |
| Disk alert | `storage_warn_percent` exceeded | Almacenamiento → clean orphan HF revisions, delete unused models; user data is never auto-deleted |
| Ollama models show 0 after restart | `ollama` container restarted | Normal: models load on demand; registry state is reconciled automatically |

Useful commands:

```bash
docker compose ps; docker ps -a --filter name=sah-
docker compose logs --tail 200 worker
docker logs --tail 200 sah-vllm
curl -s https://your-spark.your-tailnet.ts.net/api/v1/system/health
docker compose exec postgres psql -U sah -d sah -c "select type,status,count(*) from jobs group by 1,2"
```

Diagnostic bundle (admin): Ajustes → Diagnóstico → "Paquete de diagnóstico" (secrets redacted).

## The whole machine froze while loading a big model (web stuck, Tailscale "disconnected")
On the Spark the GPU memory is the system memory, and a large vLLM load peaks far above what the model holds afterwards (seen:
117 of 121 GB used while loading a 70 GB model). If anything else is using memory then — a big download, the robot server's text
encoder, an idle model not freed yet — the NVIDIA driver cannot allocate, the kernel logs `NV_ERR_NO_MEMORY`, tasks block for
minutes and the machine stops answering until it is power-cycled. Check after the fact with
`journalctl -b -1 -k | grep -E "NV_ERR_NO_MEMORY|oom-kill|blocked for more"`.

The hub now protects itself (`services/memguard.py`): before a heavy load it pauses running Hugging Face downloads (they resume
afterwards), frees the robot server's memory, evicts idle models and requires real free memory (reservation + `load_safety_gb`,
default 8 GB) — otherwise it refuses with the numbers. Downloads also wait while a large model loads and for the first minute after
a restart. A model that died for lack of memory is **not** re-loaded automatically (administrators get a notification); load it by
hand from the Library when the machine is quiet. To make a freeze recover on its own, run `sudo scripts/harden-memory.sh --apply`
(reserves 4 GiB of headroom, reboots 30 s after a task is blocked for 5 minutes, enables the hardware watchdog); `--revert` undoes it.
