"""Generate a realistic synthetic telecom-churn dataset.

Synthetic so the project runs offline with zero downloads, but the
relationships are realistic: short tenure, month-to-month contracts,
electronic-check payments and many support tickets all raise churn risk.

Usage:
    python scripts/generate_data.py --rows 10000 --out data/customers.csv
    python scripts/generate_data.py --rows 2000 --drift --out data/customers_drifted.csv
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def generate(n_rows: int, seed: int = 42, drift: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    contract = rng.choice(
        ["month-to-month", "one_year", "two_year"],
        size=n_rows,
        p=[0.75, 0.15, 0.10] if drift else [0.55, 0.25, 0.20],
    )
    tenure = np.where(
        contract == "month-to-month",
        rng.integers(1, 36, n_rows),
        rng.integers(6, 72, n_rows),
    )
    internet = rng.choice(["fiber", "dsl", "none"], size=n_rows, p=[0.45, 0.35, 0.20])
    base_price = np.select(
        [internet == "fiber", internet == "dsl"], [80.0, 55.0], default=25.0
    )
    if drift:  # simulate a price hike after the model was trained
        base_price = base_price * 1.25
    monthly = np.round(base_price + rng.normal(0, 10, n_rows), 2).clip(18, 150)
    total = np.round(monthly * tenure * rng.uniform(0.95, 1.05, n_rows), 2)
    payment = rng.choice(
        ["electronic_check", "credit_card", "bank_transfer", "mailed_check"],
        size=n_rows,
        p=[0.35, 0.25, 0.25, 0.15],
    )
    tickets = rng.poisson(1.2 if not drift else 2.0, n_rows)
    senior = rng.binomial(1, 0.16, n_rows)

    # Ground-truth churn logit
    logit = (
        -1.2
        - 0.045 * tenure
        + 0.018 * (monthly - 60)
        + 0.45 * tickets
        + np.select(
            [contract == "month-to-month", contract == "one_year"], [1.4, 0.2], default=-1.0
        )
        + np.where(payment == "electronic_check", 0.6, 0.0)
        + np.where(internet == "fiber", 0.35, 0.0)
        + 0.3 * senior
    )
    prob = 1 / (1 + np.exp(-logit))
    churned = rng.binomial(1, prob)

    df = pd.DataFrame(
        {
            "customer_id": [f"C{100000 + i}" for i in range(n_rows)],
            "tenure_months": tenure,
            "monthly_charges": monthly,
            "total_charges": total,
            "num_support_tickets": tickets,
            "contract_type": contract,
            "payment_method": payment,
            "internet_service": internet,
            "senior_citizen": senior,
            "churned": churned,
        }
    )

    # Real data is messy: inject ~1% missing total_charges.
    missing = rng.random(n_rows) < 0.01
    df.loc[missing, "total_charges"] = np.nan
    return df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--drift", action="store_true", help="Generate a drifted batch")
    parser.add_argument("--out", type=Path, default=Path("data/customers.csv"))
    args = parser.parse_args()

    df = generate(args.rows, args.seed, args.drift)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df):,} rows to {args.out}  (churn rate = {df.churned.mean():.1%})")


if __name__ == "__main__":
    main()
