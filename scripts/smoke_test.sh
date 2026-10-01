#!/usr/bin/env bash
# End-to-end smoke test against a running API (local, compose, kind, or cloud).
#   bash scripts/smoke_test.sh [base_url] [api_key]
# Exits non-zero on the first failure — the deploy pipeline relies on this.
set -euo pipefail
URL="${1:-http://localhost:8000}"
KEY="${2:-${SMOKE_API_KEY:-dev-key}}"
H=(-H "Content-Type: application/json" -H "X-API-Key: $KEY")

check() {  # check <name> <expected_status> <curl args...>
  local name=$1 want=$2; shift 2
  local got
  got=$(curl -s -o /tmp/smoke_body -w '%{http_code}' --max-time 10 "$@")
  if [[ "$got" != "$want" ]]; then
    echo "FAIL  $name: HTTP $got (want $want)"; cat /tmp/smoke_body; echo; exit 1
  fi
  echo "ok    $name ($got)"
}

# Probes are only reachable in-cluster; skip them through a public gateway.
if [[ "${SKIP_PROBES:-0}" != "1" ]]; then
  check "liveness"  200 "$URL/health"
  check "readiness" 200 "$URL/ready"
fi

check "model info"        200 "${H[@]}" "$URL/v1/model-info"
check "no key -> 401"     401 -H "Content-Type: application/json" -X POST "$URL/v1/predict" -d '{}'
check "bad input -> 422"  422 "${H[@]}" -X POST "$URL/v1/predict" -d '{"tenure_months": -5, "contract_type": "lifetime"}'

risky='{"customer_id":"smoke-risky","tenure_months":2,"monthly_charges":99.0,"total_charges":198.0,
  "num_support_tickets":5,"contract_type":"month-to-month","payment_method":"electronic_check",
  "internet_service":"fiber","senior_citizen":1}'
loyal='{"customer_id":"smoke-loyal","tenure_months":60,"monthly_charges":55.0,"total_charges":3300.0,
  "num_support_tickets":0,"contract_type":"two_year","payment_method":"credit_card",
  "internet_service":"dsl","senior_citizen":0}'

check "predict risky" 200 "${H[@]}" -X POST "$URL/v1/predict" -d "$risky"
p_risky=$(python3 -c "import json;print(json.load(open('/tmp/smoke_body'))['churn_probability'])")
check "predict loyal" 200 "${H[@]}" -X POST "$URL/v1/predict" -d "$loyal"
p_loyal=$(python3 -c "import json;print(json.load(open('/tmp/smoke_body'))['churn_probability'])")

# Behavioural check on the LIVE model, not just "it returns 200".
python3 -c "import sys; r,l=$p_risky,$p_loyal; print(f'ok    risky={r} > loyal={l}') if r>l else sys.exit(f'FAIL  model ranks loyal ({l}) above risky ({r})')"

check "batch" 200 "${H[@]}" -X POST "$URL/v1/predict/batch" -d "{\"customers\": [$risky, $loyal]}"
check "feedback" 202 "${H[@]}" -X POST "$URL/v1/feedback" -d '{"customer_id":"smoke-risky","churned":true}'

echo "SMOKE TEST PASSED against $URL"
