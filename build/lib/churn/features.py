"""Feature engineering + model as ONE sklearn Pipeline.

Key production lesson: preprocessing lives *inside* the saved artifact, so
training and serving can never drift apart (no "training/serving skew").
"""
import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from churn import config


def _add_ratio(X):
    """charges-per-month-of-tenure: a simple engineered feature."""
    X = X.copy()
    X["avg_charge_per_month"] = X["total_charges"] / np.maximum(X["tenure_months"], 1)
    return X


def build_pipeline(params: dict | None = None) -> Pipeline:
    params = params or {}
    numeric = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            # handle_unknown="ignore": an unseen category at serving time
            # becomes all-zeros instead of crashing the API.
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    preprocess = ColumnTransformer(
        [
            ("num", numeric, config.NUMERIC_FEATURES + ["avg_charge_per_month"]),
            ("cat", categorical, config.CATEGORICAL_FEATURES),
            ("bin", "passthrough", config.BINARY_FEATURES),
        ]
    )
    model = HistGradientBoostingClassifier(
        max_iter=params.get("max_iter", 200),
        learning_rate=params.get("learning_rate", 0.05),
        max_depth=params.get("max_depth", 4),
        random_state=config.RANDOM_SEED,
    )
    return Pipeline(
        [
            ("engineer", FunctionTransformer(_add_ratio)),
            ("preprocess", preprocess),
            ("model", model),
        ]
    )
