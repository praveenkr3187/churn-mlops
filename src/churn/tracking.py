"""Optional experiment tracking in MLflow.

The registry (registry.py) stays the source of truth for what gets DEPLOYED.
MLflow is where people COMPARE RUNS: params, metrics, gate results and model
cards for every training run, including the ones that failed their gates.

Enabled only when MLFLOW_TRACKING_URI is set and the `mlflow` extra is
installed, so training never depends on a tracking server being up:

    pip install -e ".[mlflow]"
    MLFLOW_TRACKING_URI=http://localhost:5001 python -m churn.train
"""
import os
import sys


def enabled() -> bool:
    return bool(os.environ.get("MLFLOW_TRACKING_URI"))


def log_run(report: dict, version: str | None = None, model_card: str | None = None) -> None:
    """Record one training run. Never raises: tracking must not fail training."""
    if not enabled():
        return
    try:
        import mlflow
    except ImportError:
        print("MLFLOW_TRACKING_URI is set but mlflow is not installed; skipping tracking", file=sys.stderr)
        return
    try:
        mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT_NAME", "churn"))
        with mlflow.start_run(run_name=report["run_id"]) as run:
            mlflow.log_params({**report["params"], "decision_threshold": report["decision_threshold"]})
            mlflow.log_metrics({**report["metrics"], "baseline_roc_auc": report["baseline_roc_auc"]})
            for attr, f in report["fairness"].items():
                mlflow.log_metric(f"fairness_recall_gap/{attr}", f["recall_gap"])
            mlflow.set_tags({
                "gates_passed": str(report["passed"]).lower(),
                "failed_gates": ",".join(g["name"] for g in report["gates"] if not g["passed"]) or "none",
                # Links the experiment run to the deployable artifact in the registry.
                "registry_version": version or "not-registered",
                "champion": (report["champion"] or {}).get("version", "none"),
                "env": os.environ.get("CHURN_ENV", "dev"),
            })
            mlflow.log_dict(report, "run_report.json")
            if model_card:
                mlflow.log_text(model_card, "model_card.md")
        print(f"Tracked in MLflow: run {run.info.run_id}")
    except Exception as exc:
        print(f"MLflow tracking failed (training continues): {exc}", file=sys.stderr)
