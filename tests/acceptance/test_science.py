"""Scientific Lab end-to-end: dataset upload → trusted adapter model → experiment → batch inference."""
import os
from pathlib import Path

import pytest

from client import Hub
from test_acceptance import PW, _make_researcher, admin  # noqa: F401

EX = Path(os.environ.get("SAH_REGRESSION_EXAMPLE", "/tmp/sah-regression"))




def test_19_regression_adapter_runs(alice):
    if not EX.exists():
        pytest.skip("run adapters/examples/make_regression_example.py first")
    ds = alice.post("/api/v1/datasets", files={"file": ("test.csv", (EX / "test.csv").read_bytes(), "text/csv")},
                    data={"name": "regression-test-500", "description": "synthetic y=2.5x1-1.2x2+0.7x3+4"}).json()
    assert ds["schema"]["rows"] == 500
    files = [("files", ("model.safetensors", (EX / "model.safetensors").read_bytes(), "application/octet-stream"))]
    m = alice.post("/api/v1/science/models", data={"name": "linreg-example", "adapter": "linear_regression",
                                                   "config_json": (EX / "config.json").read_text()}, files=files)
    assert m.status_code == 200, m.text
    m = m.json()
    e = alice.post("/api/v1/experiments", json={"name": "linreg on test split", "model_id": m["id"], "dataset_id": ds["id"]}).json()
    job = alice.post(f"/api/v1/experiments/{e['id']}/run", json={}).json()
    j = alice.wait_job(job["job_id"], timeout=900)
    assert j["status"] == "completed", j["error"]
    mse = j["result"]["metrics"]["y"]["mse"]
    assert mse < 0.02 and j["result"]["metrics"]["y"]["r2"] > 0.99
    assert j["result"]["device"] == "cuda"
    names = {a["filename"] for a in j["artifacts"]}
    assert {"predictions.csv", "metrics.json"} <= names
    print("regression metrics", j["result"]["metrics"], "device", j["result"]["device"])
