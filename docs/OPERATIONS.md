# Operations runbook

For the on-call engineer. Every alert in `monitoring/alerts.yml` links to a
section here. Commands assume `NS=churn-prod`, `OVERLAY=aws-prod` (or
`gcp-prod`), and a kubeconfig for the right cluster.

## Service level objectives

| SLI | SLO | Error budget (30 days) | Alert |
|---|---|---|---|
| Availability: non-5xx share of `/v1/*` | 99.5% | 0.5% ≈ 3.6 h of full outage | ChurnErrorBudgetFastBurn (page), SlowBurn (ticket) |
| Latency: p95 of `/v1/predict` | < 250 ms | — | ChurnHighLatency (page) |
| Freshness: model retrained | weekly | 1 missed run | ChurnRetrainJobFailed (ticket) |
| Quality: live recall | ≥ 0.60 | — | `live_eval.py` (weekly job / manual) |

When the budget is exhausted: freeze feature releases and spend the time on
reliability work.

## First five minutes (any alert)

```bash
kubectl -n $NS get pods -o wide                     # running? restarting? which nodes?
kubectl -n $NS get events --sort-by=.lastTimestamp | tail -20
kubectl -n $NS logs deploy/churn-api --tail=100     # JSON logs; look at "level": "ERROR"
python scripts/release.py show --overlay $OVERLAY   # what SHOULD be running
kubectl -n $NS get deploy -o wide                   # what IS running (image tags)
```
Grafana: dashboard **"Churn API — service & model"**. Did it start with a
deploy? Check the "Serving model versions" panel and recent release commits
(`git log -- k8s/overlays/$OVERLAY/release`).

---

## High error rate
*ChurnErrorBudgetFastBurn / ChurnErrorBudgetSlowBurn*

1. **Did a release just happen?** If a canary is live, abort it:
   ```bash
   python scripts/release.py canary-abort --overlay $OVERLAY && scripts/deploy.sh $OVERLAY --no-smoke
   ```
   If a full promotion just happened, roll back:
   ```bash
   python scripts/release.py set-stable --overlay $OVERLAY --tag <previous-tag> --model <previous-version>
   scripts/deploy.sh $OVERLAY
   ```
   (Previous values are in `git log -p k8s/overlays/$OVERLAY/release/`.)
2. **Which status codes?** `sum by (status) (rate(churn_http_requests_total[5m]))`.
   `503` means the model isn't loaded (see [Model not loaded](#model-not-loaded)).
   `500` means look for `"exc"` in the logs.
3. **Dependency down?** Object storage only matters at startup; running pods
   keep serving. Check the cloud status page if new pods fail to start.
4. Commit the rollback state (`git add k8s/overlays && git commit`), then
   write the incident note (template below).

## High latency
*ChurnHighLatency*

1. **Saturated?** Compare pod CPU to requests; check whether the HPA is at
   `maxReplicas` (`kubectl -n $NS get hpa`). If so, raise `maxReplicas` in
   `components/prod` and deploy.
2. **Pods throttled or being evicted?** Check `kubectl top pods` and events
   for `OOMKilled`.
3. **Regression from a new version?** Compare latency by `track` or
   `model_version`, and roll back if it started with a release.
4. **Batch abuse?** Check whether `/v1/predict/batch` traffic spiked from
   one key. Tighten the per-key rate limit in `base/traffic-policy.yaml`.
5. Check that `OMP_NUM_THREADS=1` is set in the pod
   (`kubectl exec ... -- env`). See [Capacity](#capacity).

## API down
*ChurnApiDown*

1. `kubectl -n $NS get pods`: `CrashLoopBackOff` means read
   `kubectl logs --previous`; `ImagePullBackOff` means a bad tag or registry
   permission; `Pending` means no capacity (check node group / Autopilot
   quota).
2. If Prometheus itself is down, the alert is a false positive. Check
   `kubectl -n monitoring get pods`.
3. Check the external path too: `SKIP_PROBES=1 bash scripts/smoke_test.sh https://churn.example.com $KEY`.

## Model not loaded
*ChurnModelNotLoaded*: pods run but `/ready` returns 503.

```bash
kubectl -n $NS logs deploy/churn-api | grep model_load_failed
```
| Log message | Cause | Fix |
|---|---|---|
| `No 'production' model` | Alias missing (new environment) | Train and promote a model |
| `Checksum mismatch` | Artifact changed after training | **Security incident**: don't bypass. Check bucket audit logs, restore from bucket versioning |
| `trained with scikit-learn X but runtime has Y` | Image and model built with different libs | Deploy an image matching the model, or retrain with the current image |
| `AccessDenied` / `403` | Pod identity or IAM broken | Check the pod identity association / WIF binding in Terraform |

## Canary
*ChurnMixedModelVersions*: two versions have served traffic for more than
2 h, usually because a release job died mid-way.

```bash
python scripts/release.py show --overlay $OVERLAY
# decide: finish it...
python scripts/release.py canary-promote --overlay $OVERLAY && scripts/deploy.sh $OVERLAY
# ...or abandon it
python scripts/release.py canary-abort --overlay $OVERLAY && scripts/deploy.sh $OVERLAY --no-smoke
git add k8s/overlays && git commit -m "ops: resolve stuck canary" && git push
```

## Drift
*ChurnScoreDistributionShift / ChurnHighRiskShareSpike*

The service is healthy, but inputs or outputs changed.
1. Export recent prediction events (CloudWatch Logs Insights / Cloud
   Logging query `jsonPayload.event="prediction"`) to a JSONL file.
2. `python scripts/drift_report.py --events events.jsonl --version production`
3. Interpret:
   - One feature with high PSI after a known business change (price rise,
     new plan) means real drift. Retrain on recent data and release
     through `model-release`.
   - Categorical PSI with a *new category* means an upstream schema change.
     The API rejects unknown categories (422), so check the client error
     rate too.
   - High PSI everywhere at once usually means an upstream bug (units
     changed, nulls). Fix the data, don't retrain on it.
4. When labels arrive: `python scripts/live_eval.py --events events.jsonl`.

## No traffic
*ChurnNoPredictions*: nobody is calling the API.
Check whether the CRM integration is down (their side), DNS or the
certificate (`kubectl -n gateway-system get gateway,certificate`), or
whether the API keys were rotated without updating the client.

## Retraining failed
*ChurnRetrainJobFailed*

```bash
kubectl -n $NS get jobs -l app.kubernetes.io/name=churn-retrain
kubectl -n $NS logs job/<job-name>
```
- `[FAIL] ...` gate lines mean the job **worked as designed**: the new
  model isn't good enough, so production keeps the old one. Investigate
  the data (drift? label delay? volume?). No urgency unless it repeats.
- `DataValidationError` means an upstream data problem. Contact the data
  owner.
- `OOMKilled` means the data outgrew the job. Raise memory in
  `cronjob-retrain.yaml`.
- Re-run manually: `kubectl -n $NS create job --from=cronjob/churn-retrain retrain-manual-$(date +%s)`

## Rotate API keys
Zero-downtime rotation (both old and new keys are valid for a while):
1. Add the new key: `api_keys: "old,new"` in the cloud secret
   (`aws secretsmanager put-secret-value` / `gcloud secrets versions add`).
2. External Secrets syncs within 1 h (or annotate the ExternalSecret to force
   it), then `kubectl -n $NS rollout restart deploy/churn-api deploy/churn-api-canary`.
3. Give clients the new key and confirm the old one stops appearing
   (per-key rate-limit stats in Envoy).
4. Remove the old key and restart again.
Rotate the **PII salt** only with a plan: it breaks joins between old and
new prediction events.

## Capacity
Measured during development on a 2-vCPU sandbox (indicative only; measure
on your real node type):

| Measurement | Result |
|---|---|
| Model inference, 1 row | ~8 ms |
| Full request server-side, idle | ~19 ms |
| 20 req/s from a separate lightweight client | p50 12 ms, p95 17 ms |
| **Before** `OMP_NUM_THREADS=1`, under concurrent load | p50 340 ms (thread-pool contention) |
| locust on the **same** machine as the API | p95 350 ms (load generator starved the API: don't do this) |

Sizing rule: find the sustained req/s per pod with p95 < 250 ms at 65% CPU
(`make load` from a separate machine). Then `minReplicas` = peak ÷ that
rate, rounded up, and at least 3 in prod; `maxReplicas` = 3 × peak ÷ rate.

## Deploy / rollback cheat sheet

| Action | Command |
|---|---|
| What's deployed | `python scripts/release.py show --overlay $OVERLAY` |
| Deploy current state | `scripts/deploy.sh $OVERLAY` |
| Roll back code | `release.py set-stable --tag <old>` → `deploy.sh` |
| Roll back model | `release.py set-stable --model <old>` → `deploy.sh`; `python -m churn.promote --rollback` |
| Emergency (no Git) | `kubectl -n $NS rollout undo deploy/churn-api` (then fix Git state!) |
| Pause retraining | `kubectl -n $NS patch cronjob churn-retrain -p '{"spec":{"suspend":true}}'` |
| Manual batch score | `kubectl -n $NS create job --from=cronjob/churn-batch-score score-manual-$(date +%s)` |

## Incident note template

```
Title:            <symptom> in churn-api <env>
Detected:         <time IST> by <alert / person>
Impact:           <requests failed %, customers affected, duration>
Timeline:         <time> event ... (deploys, alerts, actions)
Root cause:       <what actually broke and why>
Resolution:       <what fixed it>
Error budget:     <minutes consumed>
Action items:     <owner, due date>  (a test, alert or guard that would have caught it earlier)
```
Blameless: describe systems and decisions, not people.
