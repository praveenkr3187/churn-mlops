"""Data loading + validation.

Validation is the first line of defence: a model trained on bad data fails
silently, so fail loudly and early. Rules come from config (the data contract).
"""
import fsspec
import pandas as pd

from churn import config


class DataValidationError(ValueError):
    pass


def load_data(path: str | None = None) -> pd.DataFrame:
    """Read from a local path or an object store (s3://, gs://) — same code."""
    path = str(path or config.DATA_PATH)
    fs, p = fsspec.core.url_to_fs(path)
    if not fs.exists(p):
        raise FileNotFoundError(
            f"{path} not found. Run `make data` (or scripts/generate_data.py) first."
        )
    with fs.open(p, "rb") as f:
        return pd.read_csv(f)


def validate(df: pd.DataFrame, require_target: bool = True, max_null_rate: float = 0.05) -> pd.DataFrame:
    """Check schema, ranges, categories and null budget. Returns df if valid."""
    expected = config.FEATURES + ([config.TARGET] if require_target else [])
    missing_cols = [c for c in expected if c not in df.columns]
    if missing_cols:
        raise DataValidationError(f"Missing columns: {missing_cols}")
    if len(df) == 0:
        raise DataValidationError("Dataset is empty")

    errors: list[str] = []
    for col, (lo, hi) in config.NUMERIC_RANGES.items():
        s = df[col].dropna()
        if (s < lo).any():
            errors.append(f"{col} has values below {lo} (negative?)")
        if (s > hi).any():
            errors.append(f"{col} has values above {hi}")

    for col, allowed in config.CATEGORIES.items():
        unknown = set(df[col].dropna().unique()) - set(allowed)
        if unknown:
            errors.append(f"{col} has unknown categories: {sorted(unknown)}")

    for col in config.BINARY_FEATURES:
        if not set(df[col].dropna().unique()) <= {0, 1}:
            errors.append(f"{col} must be 0/1")

    null_rate = df[config.FEATURES].isna().mean()
    too_many = null_rate[null_rate > max_null_rate]
    if not too_many.empty:
        errors.append(f"Too many nulls: {too_many.round(3).to_dict()}")

    if require_target:
        if not set(df[config.TARGET].unique()) <= {0, 1}:
            errors.append("target must be binary 0/1")
        elif df[config.TARGET].nunique() < 2:
            errors.append("target has a single class — cannot train")

    if "customer_id" in df.columns and df["customer_id"].dropna().duplicated().any():
        errors.append("duplicate customer_id rows")

    if errors:
        raise DataValidationError("; ".join(errors))
    return df
