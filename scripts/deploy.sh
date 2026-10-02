#!/usr/bin/env bash
# Apply an overlay and wait until it is healthy. Used by the pipelines and by
# humans (same command either way = no "works in CI only" surprises).
#
#   scripts/deploy.sh aws-staging            # apply + wait + in-cluster smoke test
#   scripts/deploy.sh aws-prod --no-smoke
#   ROLLOUT_TIMEOUT=90s scripts/deploy.sh local   # fail faster (CI drills)
set -euo pipefail
OVERLAY="${1:?overlay}"; shift || true
SMOKE=1; [[ "${1:-}" == "--no-smoke" ]] && SMOKE=0

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS=$(kustomize build "$ROOT/k8s/overlays/$OVERLAY" | awk '/^kind: Namespace/{f=1} f&&/^  name:/{print $2; exit}')

echo "==> Validating render of $OVERLAY"
kustomize build "$ROOT/k8s/overlays/$OVERLAY" > /tmp/rendered.yaml
kubectl apply --server-side --dry-run=server -f /tmp/rendered.yaml >/dev/null

echo "==> Applying to namespace $NS"
kubectl apply --server-side --force-conflicts -f /tmp/rendered.yaml

echo "==> Waiting for rollouts"
for d in $(kubectl -n "$NS" get deploy -l app.kubernetes.io/name=churn-api -o name); do
  if ! kubectl -n "$NS" rollout status "$d" --timeout="${ROLLOUT_TIMEOUT:-300s}"; then
    echo "Rollout of $d failed — recent events:"
    kubectl -n "$NS" get events --sort-by=.lastTimestamp | tail -20
    exit 1
  fi
done

if [[ $SMOKE == 1 ]]; then
  echo "==> In-cluster smoke test (via port-forward to the stable service)"
  kubectl -n "$NS" port-forward svc/churn-api-stable 18080:80 >/dev/null 2>&1 &
  PF=$!; trap 'kill $PF 2>/dev/null || true' EXIT
  for _ in $(seq 1 20); do curl -fs localhost:18080/health >/dev/null && break; sleep 1; done
  bash "$ROOT/scripts/smoke_test.sh" http://localhost:18080 "${SMOKE_API_KEY:?set SMOKE_API_KEY}"
fi
echo "==> $OVERLAY deployed"
