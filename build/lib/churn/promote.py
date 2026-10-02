"""Promote (or roll back) a model version. Run by the release pipeline AFTER a
human approves, or by an operator during an incident.

  python -m churn.promote v20260927-101500 --alias production --reason "approved in PR #42"
  python -m churn.promote --from-alias staging  # promote whatever staging points at
  python -m churn.promote --rollback          # re-point production at the previous version
  python -m churn.promote --list

Note: moving the alias does not change what running pods serve — pods are
pinned to a version via CHURN_MODEL_VERSION. The alias is the record of
intent the deploy pipeline reads when rendering manifests.
"""
import argparse
import json
import sys

from churn import registry


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("version", nargs="?")
    p.add_argument("--alias", default="production", choices=registry.ALIASES)
    p.add_argument("--reason", default="")
    p.add_argument("--from-alias", choices=registry.ALIASES, help="promote the version this alias points to")
    p.add_argument("--rollback", action="store_true")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    if args.list:
        print(json.dumps({
            "production": registry.current("production"),
            "staging": registry.current("staging"),
            "versions": registry.list_versions(),
            "history": registry.history()[-5:],
        }, indent=2))
        return

    if args.rollback:
        prev = [h for h in registry.history() if h["alias"] == args.alias]
        if not prev or not prev[-1].get("previous"):
            sys.exit(f"Nothing to roll back to for '{args.alias}'")
        target = prev[-1]["previous"]
        rec = registry.promote(target, args.alias, reason=args.reason or "rollback")
    else:
        if args.from_alias:
            args.version = registry.resolve(args.from_alias)
        if not args.version:
            sys.exit("version required (or use --rollback / --list)")
        md = registry.get_metadata(args.version)
        if not all(g["passed"] for g in md.get("gates", [])):
            sys.exit(f"{args.version} did not pass its gates — refusing to promote")
        rec = registry.promote(args.version, args.alias, reason=args.reason)
    print(json.dumps(rec, indent=2))


if __name__ == "__main__":
    main()
