"""Builds the example tabular regression model for the Scientific Lab 'linear_regression' adapter.

Synthetic but fully reproducible: y = 2.5·x1 − 1.2·x2 + 0.7·x3 + 4.0 + ε (ε ~ N(0, 0.1²)), seed 42.
The model is fitted with ordinary least squares (numpy) and saved as safetensors + config.json.
Outputs (in the given directory): model.safetensors, config.json, train.csv, test.csv.

    python make_regression_example.py OUT_DIR
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
from safetensors.numpy import save_file

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(42)
true_w, true_b = np.array([2.5, -1.2, 0.7]), 4.0


def make(n):
    x = rng.normal(0, 1, size=(n, 3))
    y = x @ true_w + true_b + rng.normal(0, 0.1, size=n)
    return x, y


xtr, ytr = make(2000)
xte, yte = make(500)
mean, std = xtr.mean(0), xtr.std(0)
xn = (xtr - mean) / std
A = np.hstack([xn, np.ones((len(xn), 1))])
coef, *_ = np.linalg.lstsq(A, ytr, rcond=None)
save_file({"weight": coef[:3].reshape(1, 3).astype(np.float32), "bias": np.array([coef[3]], dtype=np.float32)},
          str(out / "model.safetensors"))
cfg = {"feature_columns": ["x1", "x2", "x3"], "target_names": ["y"], "hidden": None,
       "x_mean": mean.tolist(), "x_std": std.tolist(), "fit": "numpy.linalg.lstsq on train.csv (seed 42)"}
(out / "config.json").write_text(json.dumps(cfg, indent=2))
for name, x, y in (("train.csv", xtr, ytr), ("test.csv", xte, yte)):
    with open(out / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["x1", "x2", "x3", "y"])
        w.writerows([[f"{a:.6f}", f"{b:.6f}", f"{c:.6f}", f"{t:.6f}"] for (a, b, c), t in zip(x, y)])
pred = ((xte - mean) / std) @ coef[:3] + coef[3]
print(json.dumps({"test_mse_numpy": float(((pred - yte) ** 2).mean())}))
