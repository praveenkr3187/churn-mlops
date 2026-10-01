"""Test configuration.

Env vars are set BEFORE churn is imported, so every module sees an isolated
temp registry/dataset and the API runs with auth ON (like production).
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

TMP = Path(tempfile.mkdtemp(prefix="churn-tests-"))
os.environ.update(
    {
        "CHURN_ENV": "test",
        "CHURN_DATA_PATH": str(TMP / "customers.csv"),
        "CHURN_MODEL_REGISTRY": str(TMP / "registry"),
        "CHURN_PREDICTION_LOG": str(TMP / "predictions.jsonl"),
        "CHURN_API_KEYS": "test-key",
        "CHURN_ADMIN_API_KEYS": "admin-key",
        "CHURN_PII_SALT": "test-salt",
    }
)

import pytest  # noqa: E402
from generate_data import generate  # noqa: E402

generate(3000, seed=7).to_csv(TMP / "customers.csv", index=False)


@pytest.fixture(scope="session")
def trained():
    from churn import registry
    from churn.train import train

    md = train({"max_iter": 60}, report_dir=str(TMP / "reports"))
    registry.promote(md["version"], "production", reason="test setup")
    return md


@pytest.fixture
def client(trained):
    from fastapi.testclient import TestClient

    from churn.api import STATE, app

    STATE["predictor"] = None
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth():
    return {"X-API-Key": "test-key"}


@pytest.fixture
def customer():
    return {
        "customer_id": "T1",
        "tenure_months": 2,
        "monthly_charges": 95.0,
        "total_charges": 190.0,
        "num_support_tickets": 5,
        "contract_type": "month-to-month",
        "payment_method": "electronic_check",
        "internet_service": "fiber",
        "senior_citizen": 1,
    }
