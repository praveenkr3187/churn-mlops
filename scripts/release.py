"""Edit an overlay's release state: which image tag + model version each track
runs, and how traffic is split. The CD pipeline calls this, commits the
result, and applies it — so Git always records what's running (GitOps).

The state lives in k8s/overlays/<env>/release/kustomization.yaml, a
machine-managed Kustomize Component. Don't hand-edit it; use:

  python scripts/release.py show           --overlay aws-prod
  python scripts/release.py set-stable     --overlay aws-staging --tag sha-abc --model v2026...
  python scripts/release.py canary-start   --overlay aws-prod --tag sha-abc --model v2026... --weight 10
  python scripts/release.py canary-weight  --overlay aws-prod --weight 50
  python scripts/release.py canary-promote --overlay aws-prod
  python scripts/release.py canary-abort   --overlay aws-prod
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

K8S = Path(__file__).resolve().parents[1] / "k8s" / "overlays"
def _str_presenter(dumper, data):
    style = "|" if "\n" in data else None  # readable multi-line patches
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class _Dumper(yaml.SafeDumper):  # own subclass: don't change YAML output globally
    pass


_Dumper.add_representer(str, _str_presenter)

HEADER = (
    "# MACHINE-MANAGED by scripts/release.py — do not edit by hand.\n"
    "# Records exactly what each track runs in this environment.\n"
)


def _path(overlay: str) -> Path:
    return K8S / overlay / "release" / "kustomization.yaml"


def load(overlay: str) -> dict:
    doc = yaml.safe_load(_path(overlay).read_text())
    images = {i["name"]: i for i in doc.get("images", [])}
    lits = {g["name"]: dict(x.split("=", 1) for x in g["literals"])
            for g in doc.get("configMapGenerator", [])}
    state = {
        "registry": images["churn-api"]["newName"],
        "stable": {"tag": images["churn-api"]["newTag"],
                   "model": lits["churn-stable-release"]["CHURN_MODEL_VERSION"]},
        "canary": None,
    }
    if "churn-canary-release" in lits:
        patch = yaml.safe_load(doc["patches"][0]["patch"])[0]["value"]
        weights = {b["name"]: b["weight"] for b in patch}
        state["canary"] = {"tag": images["churn-api-canary"]["newTag"],
                           "model": lits["churn-canary-release"]["CHURN_MODEL_VERSION"],
                           "weight": weights["churn-api-canary"]}
    return state


def render(state: dict) -> str:
    reg = state["registry"]
    doc = {
        "apiVersion": "kustomize.config.k8s.io/v1alpha1",
        "kind": "Component",
        "images": [{"name": "churn-api", "newName": reg, "newTag": state["stable"]["tag"]}],
        "configMapGenerator": [{
            "name": "churn-stable-release", "behavior": "merge",
            "literals": [f"CHURN_MODEL_VERSION={state['stable']['model']}"]}],
    }
    # Jobs use the training image (same tag as the stable API release).
    train_reg = reg.rsplit("/", 1)[0] + "/churn-train" if "/" in reg else "churn-train"
    doc["images"].append({"name": "churn-train", "newName": train_reg, "newTag": state["stable"]["tag"]})
    c = state.get("canary")
    if c is not None:
        doc["images"].append({"name": "churn-api-canary", "newName": reg, "newTag": c["tag"]})
        doc["configMapGenerator"].append({
            "name": "churn-canary-release", "behavior": "merge",
            "literals": [f"CHURN_MODEL_VERSION={c['model']}"]})
        w = int(c["weight"])
        patch = [{"op": "replace", "path": "/spec/rules/0/backendRefs", "value": [
            {"name": "churn-api-stable", "port": 80, "weight": 100 - w},
            {"name": "churn-api-canary", "port": 80, "weight": w}]}]
        doc["patches"] = [{"target": {"kind": "HTTPRoute", "name": "churn-api"},
                           "patch": yaml.dump(patch, Dumper=_Dumper, sort_keys=False)}]
    return HEADER + yaml.dump(doc, Dumper=_Dumper, sort_keys=False)


def save(overlay: str, state: dict) -> None:
    _path(overlay).write_text(render(state))


def main(argv=None) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["show", "set-stable", "canary-start", "canary-weight",
                                       "canary-promote", "canary-abort"])
    p.add_argument("--overlay", required=True)
    p.add_argument("--tag")
    p.add_argument("--model")
    p.add_argument("--weight", type=int)
    a = p.parse_args(argv)

    s = load(a.overlay)
    c = s["canary"]
    if a.command == "show":
        print(json.dumps(s, indent=2))
        return
    if a.command == "set-stable":
        s["stable"] = {"tag": a.tag or s["stable"]["tag"], "model": a.model or s["stable"]["model"]}
        if c is not None:  # idle canary mirrors stable
            s["canary"] = {**s["stable"], "weight": 0}
    elif c is None:
        sys.exit(f"{a.overlay} has no canary track")
    elif a.command == "canary-start":
        if c["weight"] > 0:
            sys.exit("a canary is already running — promote or abort it first")
        s["canary"] = {"tag": a.tag or s["stable"]["tag"], "model": a.model or s["stable"]["model"],
                       "weight": a.weight or 10}
    elif a.command == "canary-weight":
        if not 0 <= (a.weight or 0) <= 100:
            sys.exit("weight must be 0..100")
        c["weight"] = a.weight
    elif a.command == "canary-promote":
        s["stable"] = {"tag": c["tag"], "model": c["model"]}
        s["canary"] = {**s["stable"], "weight": 0}
    elif a.command == "canary-abort":
        s["canary"] = {**s["stable"], "weight": 0}
    save(a.overlay, s)
    print(json.dumps(s, indent=2))


if __name__ == "__main__":
    main()
