"""Live model quality: join prediction events with ground-truth feedback (by the
pseudonymised customer hash) and compute the metrics the model was gated on.
This is the only way to see if the model still WORKS, not just if it runs.

    python scripts/live_eval.py --events logs/predictions.jsonl

Exit code 1 if live recall falls below the training gate (config.MIN_RECALL).
"""
import argparse
import json
import sys

import pandas as pd
from sklearn.metrics import precision_score, recall_score, roc_auc_score

from churn import config


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", required=True)
    p.add_argument("--min-labels", type=int, default=50)
    a = p.parse_args()

    rows = [json.loads(line) for line in open(a.events) if line.strip()]
    preds = pd.DataFrame([r for r in rows if r.get("event") == "prediction" and r.get("customer_hash")])
    fb = pd.DataFrame([r for r in rows if r.get("event") == "feedback"])
    if preds.empty or fb.empty:
        sys.exit("need both prediction and feedback events")

    # Latest prediction per customer, then attach the label.
    latest = preds.groupby("customer_hash").tail(1)
    joined = latest.merge(fb[["customer_hash", "churned"]].drop_duplicates("customer_hash", keep="last"),
                          on="customer_hash")
    n = len(joined)
    print(f"{len(latest):,} customers scored, {len(fb):,} labels, {n:,} joined")
    if n < a.min_labels:
        sys.exit(f"only {n} labelled predictions (< {a.min_labels}); not enough to judge")

    y, p, yhat = joined.churned.astype(int), joined.churn_probability, joined.will_churn.astype(int)
    res = {"n": n, "recall": round(recall_score(y, yhat), 4),
           "precision": round(precision_score(y, yhat, zero_division=0), 4)}
    if y.nunique() == 2:
        res["roc_auc"] = round(roc_auc_score(y, p), 4)
    for version, g in joined.groupby("model_version"):
        res[f"recall[{version}]"] = round(recall_score(g.churned.astype(int), g.will_churn.astype(int)), 4)
    print(json.dumps(res, indent=2))
    if res["recall"] < config.MIN_RECALL:
        sys.exit(f"LIVE RECALL {res['recall']} below gate {config.MIN_RECALL} — model degraded")


if __name__ == "__main__":
    main()
