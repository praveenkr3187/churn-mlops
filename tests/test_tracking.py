"""MLflow tracking is optional: off by default, and when on it records every
run (failed ones too) with a link to the registry version."""
import pytest

from churn import tracking

REPORT = {
    "run_id": "abc123", "params": {"max_iter": 60}, "decision_threshold": 0.2,
    "metrics": {"roc_auc": 0.85, "recall": 0.8}, "baseline_roc_auc": 0.5,
    "fairness": {"senior_citizen": {"recall_gap": 0.05}},
    "gates": [{"name": "min_roc_auc", "passed": True}], "passed": True, "champion": None,
}


def test_disabled_without_tracking_uri(monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    assert not tracking.enabled()
    tracking.log_run(REPORT)  # no-op, must not raise


def test_logs_run_with_registry_link(monkeypatch, tmp_path):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file://{tmp_path}/mlruns")
    monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "churn-test")
    mlflow.set_tracking_uri(f"file://{tmp_path}/mlruns")

    tracking.log_run(REPORT, version="v1", model_card="# card")

    runs = mlflow.search_runs(experiment_names=["churn-test"], output_format="list")
    assert len(runs) == 1
    r = runs[0]
    assert r.data.metrics["roc_auc"] == 0.85
    assert r.data.metrics["fairness_recall_gap/senior_citizen"] == 0.05
    assert r.data.tags["registry_version"] == "v1"
    assert r.data.tags["gates_passed"] == "true"


def test_tracking_failure_never_breaks_training(monkeypatch):
    pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:9")  # nothing listening
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "2")
    tracking.log_run(REPORT)  # prints a warning, does not raise
