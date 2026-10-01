"""Training pipeline with governance gates.

    load -> validate -> split (train / validation / test)
         -> fit -> choose threshold by business cost (on validation)
         -> evaluate on test: discrimination, calibration, cost
         -> fairness audit -> champion/challenger vs production
         -> explainability (permutation importance)
         -> GATES -> register (+ model card) -> optionally alias 'staging'

Promotion to 'production' is a SEPARATE, human-approved step
(`python -m churn.promote`, run by the release pipeline after approval).

Run:  python -m churn.train [--alias staging]
Exit code 1 if any gate fails — CI and the retraining CronJob rely on this.
"""
import argparse
import hashlib
import json
import platform
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

from churn import config, registry
from churn.data import load_data, validate
from churn.features import build_pipeline
from churn.model_card import render_model_card


def data_fingerprint(df: pd.DataFrame) -> str:
    """Hash of the training data — answers 'what data made this model?'"""
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values).hexdigest()[:16]


# ------------------------------------------------------------- evaluation --
def expected_cost(y, pred) -> float:
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return (fn * config.COST_FALSE_NEGATIVE + fp * config.COST_FALSE_POSITIVE) / len(y)


def choose_threshold(y, proba) -> float:
    """Pick the threshold that minimises expected business cost.
    Chosen on the VALIDATION set — never on test (that would leak)."""
    grid = np.round(np.arange(0.05, 0.96, 0.01), 2)
    costs = [expected_cost(y, (proba >= t).astype(int)) for t in grid]
    return float(grid[int(np.argmin(costs))])


def calibration_table(y, proba, bins: int = 5) -> list[dict]:
    df = pd.DataFrame({"y": np.asarray(y), "p": proba})
    df["bin"] = pd.cut(df.p, np.linspace(0, 1, bins + 1), include_lowest=True)
    t = df.groupby("bin", observed=True).agg(predicted=("p", "mean"), actual=("y", "mean"), n=("y", "size"))
    return [
        {"bin": str(b), "predicted": round(r.predicted, 3), "actual": round(r.actual, 3), "n": int(r.n)}
        for b, r in t.iterrows()
    ]


def evaluate(y, proba, threshold: float) -> dict:
    pred = (proba >= threshold).astype(int)
    return {
        "roc_auc": round(roc_auc_score(y, proba), 4),
        "pr_auc": round(average_precision_score(y, proba), 4),
        "precision": round(precision_score(y, pred, zero_division=0), 4),
        "recall": round(recall_score(y, pred, zero_division=0), 4),
        "f1": round(f1_score(y, pred, zero_division=0), 4),
        "brier": round(brier_score_loss(y, proba), 4),
        "expected_cost_per_customer": round(expected_cost(y, pred), 2),
    }


def fairness_audit(X, y, proba, threshold: float) -> dict:
    """Per-group recall and positive rate for each sensitive attribute.
    Recall gap = are we missing churners in one group more than another?"""
    pred = (proba >= threshold).astype(int)
    out = {}
    for attr in config.SENSITIVE_ATTRIBUTES:
        groups = {}
        for g in sorted(X[attr].dropna().unique()):
            m = (X[attr] == g).to_numpy()
            groups[str(g)] = {
                "n": int(m.sum()),
                "recall": round(recall_score(y[m], pred[m], zero_division=0), 4),
                "positive_rate": round(float(pred[m].mean()), 4),
            }
        recalls = [v["recall"] for v in groups.values()]
        out[attr] = {"groups": groups, "recall_gap": round(max(recalls) - min(recalls), 4)}
    return out


def feature_importance(model, X, y, n: int = 1500) -> dict:
    Xs, ys = X.iloc[:n], y.iloc[:n]
    r = permutation_importance(model, Xs, ys, scoring="roc_auc", n_repeats=5,
                               random_state=config.RANDOM_SEED)
    imp = dict(zip(X.columns, r.importances_mean.round(4), strict=True))
    return dict(sorted(imp.items(), key=lambda kv: -kv[1]))


def training_stats(X: pd.DataFrame) -> dict:
    """Reference distributions stored with the model; monitoring compares live
    traffic against these to detect drift (see scripts/drift_report.py)."""
    stats = {}
    for col in config.NUMERIC_FEATURES:
        s = X[col].dropna()
        edges = np.unique(np.quantile(s, np.linspace(0, 1, 11)))
        counts, _ = np.histogram(s, bins=edges)
        stats[col] = {
            "mean": round(float(s.mean()), 4), "std": round(float(s.std()), 4),
            "bin_edges": [round(float(e), 4) for e in edges],
            "bin_fractions": [round(float(c / counts.sum()), 4) for c in counts],
        }
    for col in config.CATEGORICAL_FEATURES:
        stats[col] = X[col].value_counts(normalize=True).round(4).to_dict()
    return stats


# ------------------------------------------------------------------- gates --
def run_gates(metrics: dict, fairness: dict, champion: dict | None) -> list[dict]:
    gates = [
        {"name": "min_roc_auc", "value": metrics["roc_auc"], "limit": config.MIN_ROC_AUC,
         "passed": metrics["roc_auc"] >= config.MIN_ROC_AUC},
        {"name": "min_recall", "value": metrics["recall"], "limit": config.MIN_RECALL,
         "passed": metrics["recall"] >= config.MIN_RECALL},
        {"name": "max_brier (calibration)", "value": metrics["brier"], "limit": config.MAX_BRIER,
         "passed": metrics["brier"] <= config.MAX_BRIER},
    ]
    for attr, f in fairness.items():
        gates.append({"name": f"fairness_recall_gap[{attr}]", "value": f["recall_gap"],
                      "limit": config.MAX_RECALL_GAP, "passed": f["recall_gap"] <= config.MAX_RECALL_GAP})
    if champion:
        floor = round(champion["roc_auc"] - config.MAX_AUC_REGRESSION, 4)
        gates.append({"name": f"not_worse_than_production[{champion['version']}]",
                      "value": metrics["roc_auc"], "limit": floor,
                      "passed": metrics["roc_auc"] >= floor})
    return gates


def evaluate_champion(X_test, y_test) -> dict | None:
    """Score the current production model on the SAME test set."""
    version = registry.current("production")
    if not version:
        return None
    try:
        model, md = registry.load(version)
    except registry.RegistryError as exc:
        print(f"Champion {version} not comparable: {exc}", file=sys.stderr)
        return None
    auc = roc_auc_score(y_test, model.predict_proba(X_test)[:, 1])
    return {"version": version, "roc_auc": round(auc, 4)}


# --------------------------------------------------------------------- run --
def train(params: dict | None = None, alias: str | None = None, report_dir: str | None = None) -> dict:
    report_dir = report_dir or config.REPORT_DIR
    run_id = uuid.uuid4().hex[:8]
    df = validate(load_data())
    X, y = df[config.FEATURES], df[config.TARGET]

    # 60 / 20 / 20: train, validation (threshold), test (final, untouched)
    X_tmp, X_test, y_tmp, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=config.RANDOM_SEED)
    X_train, X_val, y_train, y_val = train_test_split(
        X_tmp, y_tmp, test_size=0.25, stratify=y_tmp, random_state=config.RANDOM_SEED)

    baseline = DummyClassifier(strategy="prior").fit(X_train, y_train)
    baseline_auc = roc_auc_score(y_test, baseline.predict_proba(X_test)[:, 1])

    model = build_pipeline(params)
    model.fit(X_train, y_train)

    threshold = choose_threshold(y_val, model.predict_proba(X_val)[:, 1])
    proba_test = model.predict_proba(X_test)[:, 1]
    metrics = evaluate(y_test, proba_test, threshold)
    fairness = fairness_audit(X_test, y_test.to_numpy(), proba_test, threshold)
    champion = evaluate_champion(X_test, y_test)
    gates = run_gates(metrics, fairness, champion)
    passed = all(g["passed"] for g in gates)

    report = {
        "run_id": run_id,
        "finished_at": datetime.now(UTC).isoformat(),
        "params": params or {},
        "decision_threshold": threshold,
        "metrics": metrics,
        "baseline_roc_auc": round(baseline_auc, 4),
        "champion": champion,
        "fairness": fairness,
        "gates": gates,
        "passed": passed,
    }
    # Every run is recorded — failed ones too (lightweight experiment tracking).
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    Path(report_dir, f"run_{run_id}.json").write_text(json.dumps(report, indent=2, default=str))

    print(json.dumps({k: report[k] for k in ("decision_threshold", "metrics", "champion")}, indent=2))
    for g in gates:
        print(f"  [{'PASS' if g['passed'] else 'FAIL'}] {g['name']}: {g['value']} (limit {g['limit']})")

    if not passed:
        print("QUALITY GATES FAILED — model NOT registered.", file=sys.stderr)
        sys.exit(1)

    version = registry.new_version()
    metadata = {
        "version": version,
        "run_id": run_id,
        "trained_at": report["finished_at"],
        "model_type": type(model.named_steps["model"]).__name__,
        "params": {k: v for k, v in model.named_steps["model"].get_params().items()
                   if isinstance(v, (int, float, str, bool, type(None)))},
        "features": config.FEATURES,
        "decision_threshold": threshold,
        "costs": {"false_negative": config.COST_FALSE_NEGATIVE, "false_positive": config.COST_FALSE_POSITIVE},
        "metrics": metrics,
        "baseline_roc_auc": report["baseline_roc_auc"],
        "champion": champion,
        "fairness": fairness,
        "gates": gates,
        "calibration": calibration_table(y_test, proba_test),
        "feature_importance": feature_importance(model, X_test, y_test),
        "data": {
            "uri": str(config.DATA_PATH),
            "rows": len(df),
            "fingerprint": data_fingerprint(df),
            "positive_rate": round(float(y.mean()), 4),
            "split": {"train": len(X_train), "validation": len(X_val), "test": len(X_test)},
        },
        "training_stats": training_stats(X_train),
        "env": {"python": platform.python_version(), "sklearn": sklearn.__version__,
                "pandas": pd.__version__, "numpy": np.__version__},
    }
    path = registry.save(model, metadata, version, model_card=render_model_card(metadata))
    print(f"Registered {version} -> {path}")
    if alias:
        registry.promote(version, alias, reason=f"auto after training run {run_id}")
        print(f"Aliased {version} as '{alias}'")
    Path(report_dir, "latest_version.txt").write_text(version)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--max-iter", type=int, default=200)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--alias", choices=registry.ALIASES, default=None,
                        help="Alias the new version (use 'staging'; production needs approval)")
    args = parser.parse_args()
    train({"learning_rate": args.learning_rate, "max_iter": args.max_iter,
           "max_depth": args.max_depth}, alias=args.alias)


if __name__ == "__main__":
    main()
