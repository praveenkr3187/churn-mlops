"""Central configuration — the ONE place for settings and the data contract.

Twelve-factor style: every runtime value comes from an environment variable,
so the same image runs on a laptop, in docker-compose, on EKS or on GKE.
Only the env vars change (via Kubernetes ConfigMaps / Secrets).
"""
import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


# ------------------------------------------------------------------ paths --
# Local paths are relative to where you run commands (repo root, or /app in Docker).
PROJECT_ROOT = Path(_env("CHURN_HOME", str(Path.cwd())))
# Training data: local path or object-store URI (s3://bucket/data/customers.csv, gs://...).
DATA_PATH = _env("CHURN_DATA_PATH", str(PROJECT_ROOT / "data" / "customers.csv"))

# Registry location: a local folder OR an object-store URI.
#   ./models                 (laptop)
#   s3://my-bucket/models    (AWS — S3, or MinIO locally)
#   gs://my-bucket/models    (GCP — Cloud Storage)
MODEL_REGISTRY = _env("CHURN_MODEL_REGISTRY", str(PROJECT_ROOT / "models"))

# Which model version the API serves. "production" follows the promoted pointer.
MODEL_VERSION = _env("CHURN_MODEL_VERSION", "production")

# Where training-run reports are written (every run, pass or fail).
REPORT_DIR = _env("CHURN_REPORT_DIR", str(PROJECT_ROOT / "reports"))

# Optional local file copy of prediction events (stdout is the default sink).
PREDICTION_LOG = _env("CHURN_PREDICTION_LOG", "")

# --------------------------------------------------------------- security --
# Comma-separated API keys. Injected from a Kubernetes Secret (backed by AWS
# Secrets Manager / GCP Secret Manager via External Secrets). Empty = auth off,
# which is only allowed when ENV=local.
API_KEYS = {k.strip() for k in _env("CHURN_API_KEYS", "").split(",") if k.strip()}
ADMIN_API_KEYS = {k.strip() for k in _env("CHURN_ADMIN_API_KEYS", "").split(",") if k.strip()}
# Salt for pseudonymising customer IDs in logs (keeps joins possible, hides raw IDs).
PII_SALT = _env("CHURN_PII_SALT", "local-dev-salt")
ENV = _env("CHURN_ENV", "local")  # local | dev | staging | prod

# ---------------------------------------------------------- quality gates --
MIN_ROC_AUC = float(_env("CHURN_MIN_ROC_AUC", "0.75"))
MIN_RECALL = float(_env("CHURN_MIN_RECALL", "0.60"))
MAX_BRIER = float(_env("CHURN_MAX_BRIER", "0.20"))
# Fairness: max allowed gap in recall between groups of a sensitive attribute.
MAX_RECALL_GAP = float(_env("CHURN_MAX_RECALL_GAP", "0.15"))
# Champion/challenger: a new model may not be worse than production by more than this.
MAX_AUC_REGRESSION = float(_env("CHURN_MAX_AUC_REGRESSION", "0.01"))

# Business costs used to pick the decision threshold (not a magic 0.5).
# Missed churner = lifetime value (~500) x chance an offer would have kept them (~30%).
COST_FALSE_NEGATIVE = float(_env("CHURN_COST_FN", "150"))
COST_FALSE_POSITIVE = float(_env("CHURN_COST_FP", "50"))  # retention offer to a loyal customer

RANDOM_SEED = 42
TARGET = "churned"

# ---------------------------------------------------------- data contract --
# Single source of truth. data.py (batch validation) and schemas.py (API
# validation) are both generated from these, so they can never disagree.
NUMERIC_RANGES = {  # column: (min, max)
    "tenure_months": (0, 600),
    "monthly_charges": (0.01, 10_000),
    "total_charges": (0, 1_000_000),
    "num_support_tickets": (0, 1_000),
}
CATEGORIES = {
    "contract_type": ("month-to-month", "one_year", "two_year"),
    "payment_method": ("electronic_check", "credit_card", "bank_transfer", "mailed_check"),
    "internet_service": ("fiber", "dsl", "none"),
}
BINARY_FEATURES = ["senior_citizen"]
NUMERIC_FEATURES = list(NUMERIC_RANGES)
CATEGORICAL_FEATURES = list(CATEGORIES)
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES + BINARY_FEATURES

# Attributes we audit for fairness. They may or may not be model inputs.
SENSITIVE_ATTRIBUTES = ["senior_citizen"]
