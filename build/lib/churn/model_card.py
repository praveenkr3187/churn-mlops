"""Model card: a human-readable summary for reviewers and auditors.
Generated at training time and stored next to the artifact."""


def _table(rows: list[list], header: list[str]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render_model_card(md: dict) -> str:
    m = md["metrics"]
    gates = _table([[g["name"], g["value"], g["limit"], "✅" if g["passed"] else "❌"]
                    for g in md["gates"]], ["Gate", "Value", "Limit", "Result"])
    fair = []
    for attr, f in md["fairness"].items():
        for g, v in f["groups"].items():
            fair.append([attr, g, v["n"], v["recall"], v["positive_rate"]])
    fairness = _table(fair, ["Attribute", "Group", "n", "Recall", "Flag rate"])
    imp = _table([[k, v] for k, v in list(md["feature_importance"].items())[:8]],
                 ["Feature", "Permutation importance (ΔAUC)"])
    cal = _table([[c["bin"], c["predicted"], c["actual"], c["n"]] for c in md["calibration"]],
                 ["Score bin", "Mean predicted", "Actual churn rate", "n"])
    champ = md.get("champion")
    champ_line = (
        f"Compared with production `{champ['version']}` (AUC {champ['roc_auc']}) on the same test set."
        if champ else "No production model existed; first release."
    )
    perf = " | ".join(str(x) for x in (
        m["roc_auc"], m["pr_auc"], m["precision"], m["recall"], m["f1"], m["brier"],
        m["expected_cost_per_customer"], md["baseline_roc_auc"]))

    return f"""# Model card: churn classifier `{md['version']}`

## Intended use
Rank existing telecom customers by 90-day churn risk so the retention team can
prioritise outreach. **Not** for pricing, credit or any decision that denies
service. A human reviews every retention action.

## Model
- Type: {md['model_type']} inside a scikit-learn Pipeline (preprocessing included)
- Trained: {md['trained_at']} (run `{md['run_id']}`)
- Data: `{md['data']['uri']}`, {md['data']['rows']:,} rows, fingerprint `{md['data']['fingerprint']}`,
  churn rate {md['data']['positive_rate']:.1%}; split {md['data']['split']}
- Libraries: {md['env']}

## Decision threshold
**{md['decision_threshold']}**, chosen on the validation set to minimise expected cost
(missed churner = {md['costs']['false_negative']}, unnecessary offer = {md['costs']['false_positive']}).

## Performance (held-out test set)
| ROC-AUC | PR-AUC | Precision | Recall | F1 | Brier | Cost/customer | Baseline AUC |
|---|---|---|---|---|---|---|---|
| {perf} |

{champ_line}

## Release gates
{gates}

## Fairness audit
{fairness}

## Calibration
{cal}

## What drives predictions
{imp}

## Limitations and risks
- Trained on historical behaviour; pricing changes or new products cause drift.
  Monitored via input/score distributions (PSI) and live recall from feedback.
- `senior_citizen` is age-linked; it is audited above and must not be used to
  deny service. Remove it if the audit gap grows.
- Probabilities are calibrated for this population only.
"""
