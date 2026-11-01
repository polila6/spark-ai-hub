"""Operations acceptance: benchmarks (16), comparison, backup/restore (21), recovery and persistence (20)."""
import subprocess
import time
from pathlib import Path

import pytest

from client import Hub, admin
from test_acceptance import PW

ROOT = Path(__file__).resolve().parents[2]




def _llm(h, backend):
    ms = [m for m in h.get("/api/v1/models").json() if m["backend"] == backend and "chat" in m["capabilities"]
          and m["status"] in ("installed", "ready")]
    if not ms:
        pytest.skip(f"no {backend} chat model")
    return ms[0]


@pytest.mark.parametrize("backend,suite", [("ollama", "perf-short-v1"), ("ollama", "arith-exact-v1"), ("ollama", "json-format-v1"),
                                           ("vllm", "perf-short-v1"), ("vllm", "mcq-general-v1")])
def test_16_benchmark_runs_and_is_saved(alice, backend, suite):
    m = _llm(alice, backend)
    run = alice.post("/api/v1/benchmarks", json={"model_id": m["id"], "suite": suite}).json()
    j = alice.wait_job(run["job_id"], timeout=3600, poll=5, verbose=False)
    assert j["status"] == "completed", j["error"]
    d = alice.get(f"/api/v1/benchmarks/{run['id']}").json()
    assert d["status"] == "completed" and d["samples"] and d["environment"]["gpu"] == "NVIDIA GB10"
    assert d["model"]["revision"] or backend == "ollama"
    csv = alice.get(f"/api/v1/benchmarks/{run['id']}/export", params={"fmt": "csv"}).text
    assert csv.startswith("run_id,suite,model")
    print(f"\n{backend} {m['alias']} {suite}:", d["summary"])


def test_comparison_sequential(alice):
    a, b = _llm(alice, "ollama"), _llm(alice, "vllm")
    c = alice.post("/api/v1/comparisons", json={"prompt": "Explain in two sentences what a GPU is.", "model_ids": [a["id"], b["id"]],
                                                "max_tokens": 200}).json()
    j = alice.wait_job(c["job_id"], timeout=3600, poll=5, verbose=False)
    assert j["status"] == "completed", j["error"]
    res = alice.get(f"/api/v1/comparisons/{c['id']}").json()["results"]
    assert all(r["status"] == "done" and r["output"] for r in res)
    for r in res:
        print(f"\n{r['alias']}: ttft={r['metrics']['ttft_ms']} ms tps={r['metrics']['tokens_per_s']} load={r.get('load_seconds')}")


def test_21_backup_and_restore(adm):
    j = adm.wait_job(adm.post("/api/v1/admin/backups", json={"include_user_data": True}).json()["job_id"], timeout=1800)
    assert j["status"] == "completed", j["error"]
    name = j["result"]["backup"]
    assert j["result"]["verified"] and j["result"]["table_counts"]["users"] >= 3
    r = adm.wait_job(adm.post(f"/api/v1/admin/backups/{name}/restore-test").json()["job_id"], timeout=1800)
    assert r["status"] == "completed", r["error"]
    assert r["result"]["ok"] and r["result"]["restored_counts"] == r["result"]["manifest_counts"]
    print("\nbackup", name, j["result"]["size_bytes"], "bytes; restored counts", r["result"]["restored_counts"])


def test_job_recovery_after_worker_restart(alice):
    j = alice.post("/api/v1/jobs/test", json={"seconds": 90}).json()
    for _ in range(30):
        if alice.get(f"/api/v1/jobs/{j['id']}").json()["status"] == "running":
            break
        time.sleep(2)
    subprocess.run(["docker", "compose", "restart", "worker"], cwd=ROOT, check=True, capture_output=True)
    time.sleep(70)  # > stale heartbeat window
    s = alice.get(f"/api/v1/jobs/{j['id']}").json()
    ev = [e["message"] for e in alice.get(f"/api/v1/jobs/{j['id']}/events").json()]
    assert any("re-queued" in m for m in ev), ev
    assert s["attempts"] >= 2 or s["status"] == "queued"
    alice.post(f"/api/v1/jobs/{j['id']}/cancel")


def test_20_persistence_across_full_restart(adm, alice):
    before = {"users": len(adm.get("/api/v1/users").json()), "models": len(adm.get("/api/v1/models").json()),
              "convs": len(alice.get("/api/v1/playground/conversations").json()["mine"]),
              "benchmarks": len(adm.get("/api/v1/benchmarks").json())}
    subprocess.run(["docker", "compose", "restart"], cwd=ROOT, check=True, capture_output=True)
    import httpx

    from client import BASE

    for _ in range(60):  # wait for health without logging in (logins are rate-limited)
        try:
            if httpx.get(f"{BASE}/api/v1/system/health", timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(3)
    time.sleep(10)
    h = admin()
    a2 = Hub("alice-test", PW)
    after = {"users": len(h.get("/api/v1/users").json()), "models": len(h.get("/api/v1/models").json()),
             "convs": len(a2.get("/api/v1/playground/conversations").json()["mine"]),
             "benchmarks": len(h.get("/api/v1/benchmarks").json())}
    assert before == after, (before, after)
