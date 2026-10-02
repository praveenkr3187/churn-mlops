"""Model tests: not just 'does it run' but 'does it behave sensibly'."""
import pandas as pd
import pytest

from churn import registry
from churn import train as train_mod
from churn.predict import ChurnPredictor


def test_beats_baseline(trained):
    assert trained["metrics"]["roc_auc"] > trained["baseline_roc_auc"] + 0.2


def test_all_gates_recorded_and_passed(trained):
    names = {g["name"] for g in trained["gates"]}
    assert {"min_roc_auc", "min_recall", "max_brier (calibration)"} <= names
    assert any(n.startswith("fairness_recall_gap") for n in names)
    assert all(g["passed"] for g in trained["gates"])


def test_threshold_is_cost_based(trained):
    # FN cost > FP cost, so the optimal threshold must be below 0.5
    assert 0.05 <= trained["decision_threshold"] < 0.5


def test_metadata_for_governance(trained):
    for key in ("metrics", "fairness", "calibration", "feature_importance",
                "training_stats", "env", "data", "artifact_sha256"):
        assert key in registry.get_metadata(trained["version"])


def test_directional_behaviour(trained, customer):
    """A loyal two-year customer must be lower risk than a brand-new
    month-to-month customer with many tickets."""
    p = ChurnPredictor()
    loyal = {**customer, "tenure_months": 60, "contract_type": "two_year",
             "num_support_tickets": 0, "payment_method": "credit_card", "total_charges": 5700.0}
    risky, safe = p.predict_records([customer, loyal])
    assert risky["churn_probability"] > safe["churn_probability"]


def test_invariance_to_customer_id(trained, customer):
    """Changing a non-feature field must not change the score."""
    p = ChurnPredictor()
    a, b = p.predict_records([customer, {**customer, "customer_id": "OTHER"}])
    assert a["churn_probability"] == b["churn_probability"]


def test_handles_missing_total_charges(trained, customer):
    out = ChurnPredictor().predict_records([{**customer, "total_charges": None}])
    assert 0.0 <= out[0]["churn_probability"] <= 1.0


def test_champion_challenger_gate_blocks_regression(trained, monkeypatch):
    """A much weaker challenger must be rejected when a production model exists."""
    monkeypatch.setattr(train_mod.config, "MIN_ROC_AUC", 0.0)
    monkeypatch.setattr(train_mod.config, "MIN_RECALL", 0.0)
    monkeypatch.setattr(train_mod.config, "MAX_BRIER", 1.0)
    monkeypatch.setattr(train_mod.config, "MAX_RECALL_GAP", 1.0)
    with pytest.raises(SystemExit) as exc:
        train_mod.train({"max_iter": 1, "max_depth": 1, "learning_rate": 0.001},
                        report_dir=str(train_mod.Path(registry.config.MODEL_REGISTRY).parent / "r"))
    assert exc.value.code == 1


def test_deterministic(trained, customer):
    p = ChurnPredictor()
    df = pd.DataFrame([customer])
    assert p.predict_frame(df).churn_probability.iloc[0] == p.predict_frame(df).churn_probability.iloc[0]
