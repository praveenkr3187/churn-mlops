"""Data-drift report: compare live inputs (prediction events) with the training
reference stored in the model's metadata, using the Population Stability Index.

    python scripts/drift_report.py --events logs/predictions.jsonl --version production
    python scripts/drift_report.py --csv data/customers_drifted.csv       # offline batch

PSI rule of thumb:  < 0.10 stable | 0.10-0.25 moderate shift | > 0.25 major shift.
Exit code 1 if any feature is > 0.25 (so a scheduled job can alert or trigger retraining).

In production the events come from your log pipeline (CloudWatch Logs ->
S3 export / Cloud Logging -> GCS sink). Same JSON, same script.
"""
import argparse
import json
import sys

import numpy as np
import pandas as pd

from churn import config, registry


def psi(expected: np.ndarray, actual: np.ndarray, eps: float = 1e-4) -> float:
    e = np.clip(expected, eps, None)
    a = np.clip(actual, eps, None)
    return float(np.sum((a - e) * np.log(a / e)))


def numeric_psi(ref: dict, values: pd.Series) -> float:
    edges = np.array(ref["bin_edges"], dtype=float)
    edges[0], edges[-1] = -np.inf, np.inf  # catch values outside the training range
    counts, _ = np.histogram(values.dropna(), bins=edges)
    return psi(np.array(ref["bin_fractions"]), counts / max(counts.sum(), 1))


def categorical_psi(ref: dict, values: pd.Series) -> float:
    cats = sorted(set(ref) | set(values.dropna().unique()))
    live = values.value_counts(normalize=True)
    return psi(np.array([ref.get(c, 0.0) for c in cats]), np.array([live.get(c, 0.0) for c in cats]))


def load_events(path: str) -> pd.DataFrame:
    rows = [json.loads(line) for line in open(path) if line.strip()]
    rows = [r["features"] for r in rows if r.get("event") == "prediction"]
    return pd.DataFrame(rows)


def report(live: pd.DataFrame, reference: dict) -> dict:
    out = {}
    for col in config.NUMERIC_FEATURES:
        out[col] = numeric_psi(reference[col], live[col])
    for col in config.CATEGORICAL_FEATURES:
        out[col] = categorical_psi(reference[col], live[col])
    return {k: round(v, 4) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}


def main() -> None:
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--events", help="prediction events (JSON lines)")
    src.add_argument("--csv", help="a batch of customers (CSV)")
    p.add_argument("--version", default="production")
    a = p.parse_args()

    live = load_events(a.events) if a.events else pd.read_csv(a.csv)
    md = registry.get_metadata(a.version)
    result = report(live, md["training_stats"])

    print(f"Drift vs training data of {md['version']} ({len(live):,} live rows)\n")
    print(f"{'feature':<22}{'PSI':>8}  status")
    worst = 0.0
    for f, v in result.items():
        status = "MAJOR SHIFT" if v > 0.25 else "moderate" if v > 0.10 else "ok"
        print(f"{f:<22}{v:>8.3f}  {status}")
        worst = max(worst, v)
    if worst > 0.25:
        print("\nAction: investigate upstream change; retrain on recent data (docs/OPERATIONS.md#drift)")
        sys.exit(1)


if __name__ == "__main__":
    main()
