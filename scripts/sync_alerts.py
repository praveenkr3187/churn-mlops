"""Render monitoring/alerts.yml (single source) into a Kubernetes PrometheusRule.

    python scripts/sync_alerts.py          # write k8s/base/prometheusrule.yaml
    python scripts/sync_alerts.py --check  # CI: fail if out of date
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "monitoring" / "alerts.yml"
DST = ROOT / "k8s" / "base" / "prometheusrule.yaml"

class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(
    str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None))

HEADER = "# GENERATED from monitoring/alerts.yml by scripts/sync_alerts.py — do not edit.\n"


def render() -> str:
    groups = yaml.safe_load(SRC.read_text())["groups"]
    doc = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PrometheusRule",
        "metadata": {"name": "churn-api", "labels": {"release": "kube-prometheus-stack"}},
        "spec": {"groups": groups},
    }
    return HEADER + yaml.dump(doc, Dumper=_Dumper, sort_keys=False, width=120)


if __name__ == "__main__":
    out = render()
    if "--check" in sys.argv:
        if not DST.exists() or DST.read_text() != out:
            sys.exit("prometheusrule.yaml is out of date — run `make k8s-alerts`")
        print("alerts in sync")
    else:
        DST.write_text(out)
        print(f"wrote {DST.relative_to(ROOT)}")
