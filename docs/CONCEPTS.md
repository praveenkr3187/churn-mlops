# Concepts: production ML deployment, end to end

Every concept below follows the same pattern: **what it is → why it matters
in production → where it lives in this repo**. Read top to bottom once; later,
use it as a glossary.

---

## Part A: Reproducibility and packaging

### A1. Training/serving skew
**What:** the model sees features computed one way in training and another
way in production (for example, the notebook scaled with training-set means,
while the API re-implemented scaling by hand).
**Why:** it's the most common *silent* ML bug: no errors, just worse
predictions.
**Here:** all preprocessing lives inside one scikit-learn `Pipeline` that is
saved as a single artifact (`features.py`). The API and batch jobs call
`pipeline.predict_proba`, never re-implementing preprocessing.

### A2. Data contract
**What:** one explicit definition of valid input (columns, types, ranges,
categories).
**Why:** training validation and API validation must agree. If they're
written twice, they drift.
**Here:** `config.NUMERIC_RANGES` / `config.CATEGORIES`. `data.validate()`
(batch) and `schemas.Customer` (API) are both generated from it, and a test
proves they match (`test_api_schema_and_batch_validation_share_contract`).

### A3. Twelve-factor configuration
**What:** code reads settings from environment variables; nothing is
hard-coded per environment.
**Why:** the *same image* runs on a laptop, in CI, on EKS and on GKE.
**Here:** `config.py`, fed by Kubernetes ConfigMaps and Secrets.

### A4. Deterministic dependencies (lockfile with hashes)
**What:** `pyproject.toml` has version *ranges*; `requirements.lock` pins
every transitive package to an exact version *and* SHA-256 hash.
**Why:** (1) builds are reproducible, (2) a tampered package on PyPI fails
the hash check, (3) the training and serving images get the same
scikit-learn, which matters because pickles aren't portable across versions.
**Here:** `make lock` (uv), `pip install --require-hashes` in the
`Dockerfile`, and a CI check that the lock is fresh.

### A5. Immutable, versioned artifacts
**What:** once a model version or image tag is published, it never changes.
You publish a new version instead.
**Why:** "what is running?" always has one exact answer. Rollback means
pointing back to an old version, which is guaranteed to still be identical.
**Here:** the registry refuses to overwrite a version, ECR/Artifact Registry
tags are immutable, and model versions are timestamps.

### A6. Build once, promote the same artifact
**What:** build the image once, then push those exact bytes to staging and
production.
**Why:** if you rebuild for production, you ship something you never tested.
**Here:** `release.yml` builds OCI archives once. Each environment pushes
the same archive with `crane` and signs it.

### A7. Pickle safety and portability
**What:** `joblib`/`pickle` run code when loading, and objects pickled with
scikit-learn 1.5 may break or silently misbehave on 1.8.
**Here:** `registry.load()` verifies the artifact's SHA-256 against metadata
written at training time, and refuses to load if the scikit-learn
major.minor version differs. Only the training job's identity can write to
the models bucket.

---

## Part B: Model quality and governance

### B1. Always beat a baseline
A `DummyClassifier` sets the floor. An AUC of 0.83 means nothing until you
know the floor is 0.50. Recorded as `baseline_roc_auc`.

### B2. Three-way split
**Train** fits the model, **validation** picks the decision threshold,
**test** is touched once for the final numbers. Picking the threshold on
test leaks information and makes the reported metrics optimistic.

### B3. Cost-based decision threshold
**What:** choose the probability cutoff that minimises business cost, not
0.5.
**Why:** a missed churner (lost customer value × chance an offer would have
worked, about 150) costs more than an unnecessary offer (about 50). The
optimum lands near 0.2, and recall rises from 0.62 to 0.92.
**Here:** `train.choose_threshold`, with costs in `config.py`, stored in
metadata and applied by the API.

### B4. Calibration
**What:** when the model says 0.3, do about 30% of those customers churn?
**Why:** downstream teams use probabilities for budgeting, not only the
ranking.
**Here:** Brier-score gate plus a calibration table in the model card.

### B5. Fairness audit
**What:** compare error rates across groups defined by a sensitive
attribute.
**Why:** a model can look good on average while systematically missing
churners in one group (here, senior citizens).
**Here:** per-group recall and flag rate, and a gate on the recall gap
(`fairness_audit`).

### B6. Champion/challenger
**What:** score the current production model (champion) and the new
candidate (challenger) on the *same* test set.
**Why:** passing absolute thresholds isn't enough if the new model is worse
than what's already live.
**Here:** `evaluate_champion`, and the gate `not_worse_than_production`.

### B7. Quality gates
Automated, recorded pass/fail checks that decide whether a model may be
registered. A failure exits with code 1, which stops CI and fails the
retraining job, which in turn fires an alert. Every run is recorded, passed
or failed (`reports/run_*.json`), which gives you lightweight experiment
tracking.

### B8. Model card
A human-readable summary: intended use, data, metrics, gates, fairness,
calibration, top features and limitations. It's generated automatically
and is **what the approver reads** before a production release.

### B9. Behavioural tests
Tests of *model behaviour*, not code: a loyal two-year customer must score
lower than a brand-new customer with five tickets (directional test), and
changing `customer_id` must not change the score (invariance test). They
catch bugs that pass every metric.

### B10. Explainability (global)
Permutation importance: how much AUC drops when a feature is shuffled.
It's stored per version, so you can see when a new model starts relying on
something different.

---

## Part C: Serving

### C1. Online vs batch inference
The **API** serves one customer in milliseconds. The **batch CronJob**
scores everyone nightly and writes a file for the CRM. Both use the same
`ChurnPredictor`. Many use cases only need batch, which is cheaper and
simpler.

### C2. Liveness, readiness and startup probes
- **Liveness** (`/health`): "is the process stuck?" Failing it means the
  pod is killed and restarted. Never check dependencies here, or a storage
  blip restarts every pod at once.
- **Readiness** (`/ready`): "can I serve?" Failing it removes the pod from
  the load balancer without killing it.
- **Startup**: gives a cold start (model download) up to 2 minutes before
  liveness applies.

### C3. Graceful shutdown
On deploy or scale-down, Kubernetes sends SIGTERM. A `preStop` sleep of
5 s lets the gateway stop routing to the pod first. Uvicorn then finishes
in-flight requests (up to 20 s) before exiting. Result: zero failed
requests during deploys.

### C4. API versioning
Everything public is under `/v1/`. A breaking change becomes `/v2/`, running
side by side until clients migrate.

### C5. Validation at the edge
Pydantic rejects bad input with 422 and field-level messages
(`extra="forbid"` catches client typos). Bad input must never cause a 500.

### C6. Request IDs
Every response carries `x-request-id`, which appears in every log line and
prediction event. "Why did customer X get 0.93?" becomes a single search.

### C7. One process per container, scale with pods
One Uvicorn worker per pod gives predictable memory, correct per-pod
Prometheus metrics, and scaling handled by the HPA.

### C8. The OpenMP trap (found in this project)
scikit-learn's gradient boosting starts OpenMP threads on *every*
`predict()`. Under concurrent load, dozens of thread pools fought over
2 CPUs: p50 latency went from **19 ms idle to 340 ms under load**.
`OMP_NUM_THREADS=1` in the serving image fixed it. Lesson: libraries tuned
for fast batch training can be slow in a concurrent server. Only a load
test reveals this.

### C9. Load testing done right
- Test at **expected** load against the SLO. Find the **breaking point**
  separately.
- Use a target request rate (`constant_throughput`), not "as fast as
  possible".
- **Never run the load generator on the machine under test.** In this
  project, running locust on the same 2 CPUs inflated p95 from 17 ms to
  350 ms.

---

## Part D: Kubernetes building blocks

| Object | Purpose | File |
|---|---|---|
| Deployment | Desired pods; rolling update with `maxUnavailable: 0` | `base/deployment.yaml` |
| Service | Stable in-cluster address for a set of pods | `base/service.yaml` |
| HTTPRoute (Gateway API) | Public routing, weights, timeouts | `base/httproute.yaml` |
| BackendTrafficPolicy | Rate limits, retries, circuit breaking (Envoy) | `base/traffic-policy.yaml` |
| HorizontalPodAutoscaler | Add or remove pods on CPU | `base/hpa.yaml` |
| PodDisruptionBudget | Node drains evict at most 1 pod at a time | `base/pdb.yaml` |
| topologySpreadConstraints | Pods spread across zones, surviving a zone outage | `base/deployment.yaml` |
| requests / limits | Scheduling guarantees; memory limit without CPU limit (avoids throttling) | `base/deployment.yaml` |
| NetworkPolicy | Default deny; allow only gateway, Prometheus, DNS, HTTPS | `base/networkpolicy.yaml` |
| Pod Security Admission | Namespace rejects root or privileged pods | `overlays/*/namespace.yaml` |
| ConfigMap (hashed name) | Config change produces a new name and an automatic rollout | `base/kustomization.yaml` |
| ExternalSecret | Secret copied from the cloud secret manager | `base/externalsecret.yaml` |
| CronJob | Scheduled retraining and batch scoring | `base/cronjob-*.yaml` |
| PodMonitor / PrometheusRule | Scrape config and alerts as code | `base/servicemonitor.yaml`, `prometheusrule.yaml` |

**Kustomize** composes plain YAML: base, then components, then overlay.
No templating language to learn, and `kustomize build` shows exactly what
will be applied.

---

## Part E: Releasing safely

### E1. Environments
**local** (laptop) → **CI** (ephemeral) → **staging** (production-like, own
account or project) → **prod**. Separate cloud accounts per environment mean
a staging mistake can't touch production.

### E2. Deployment strategies
| Strategy | How | Used here |
|---|---|---|
| Rolling update | Replace pods gradually | Staging, and the final promote step |
| Blue/green | Full second copy, switch all traffic at once | No (costly; canary is finer-grained) |
| **Canary** | Send a small % to the new version, compare, widen | Prod: 10% → 50% → 100% |
| Shadow | Copy traffic to the new version, discard its answers | Not implemented here |

### E3. Automated canary analysis
The pipeline, not a human staring at dashboards, compares canary and stable
on error rate, latency **and model behaviour** (mean score shift), then
promotes or rolls back (`scripts/canary_analysis.py`, unit-tested).

### E4. Rollback
Three levels, fastest first:
1. Canary abort: weight to 0 (seconds, automatic).
2. `kubectl rollout undo` or `release.py set-stable` to the previous tag or
   model (minutes).
3. `git revert` of the release commit (auditable).

It's always possible because artifacts are immutable (A5).

### E5. Release state in Git (GitOps-lite)
`overlays/*/release/kustomization.yaml` records the exact image tag, model
version and weights for each track. The pipeline commits every change, so
**Git history is deployment history**. The next step up is Argo CD or Flux
pulling from Git.

### E6. Separate release trains for code and models
New model with the same code goes through `model-release.yml`. New code
with the same model goes through `release.yml`. Each is smaller and safer
than releasing both together.

### E7. Human in the loop
GitHub *environments* with required reviewers. For models, the approver
reads the model card in the run summary first.

---

## Part F: Security

| Concept | What it means here |
|---|---|
| Least privilege | API reads models; trainer writes versions and the `staging` alias only; only approved CI moves `production` (`infra/*/iam.tf`) |
| Workload identity | Pods get cloud credentials from EKS Pod Identity / GKE Workload Identity; **no keys anywhere** |
| OIDC for CI | GitHub proves "repo X, environment prod" to AWS/GCP and gets short-lived credentials |
| Secrets management | Cloud secret manager → External Secrets → pod env. Never in Git, and never in Terraform state (TF creates the container only) |
| Fail closed | The API refuses to start outside `local` without API keys |
| Constant-time comparison | `hmac.compare_digest` prevents timing attacks on keys |
| Defence in depth | TLS, rate limits, route allow-list, NetworkPolicy, non-root read-only pods, schema validation, checksums |
| Pseudonymisation | Logs carry `sha256(salt:customer_id)`: joinable for evaluation, not reversible without the salt |
| Supply chain | Hashed lockfile, pip-audit, Trivy, SBOM, cosign signatures, Dependabot, **pin CI actions to commit SHAs** (see the Trivy incident in SECURITY.md) |
| Policy as code | checkov, hadolint and k8s policy tests fail the build. Every exception is written down with a reason |

---

## Part G: Observability and reliability

### G1. Logs, metrics, traces
- **Logs** (JSON, stdout): *what happened* to one request.
- **Metrics** (Prometheus): *how much and how fast*, cheap to aggregate.
- **Traces** (OpenTelemetry): *where the time went* across services.

### G2. SLI, SLO, error budget
- **SLI:** a measurement (share of non-5xx `/v1` requests).
- **SLO:** the target (99.5% over 30 days; p95 latency under 250 ms).
- **Error budget:** the allowed failure (0.5%). Spend it on releases and
  experiments. When it runs out, freeze risky changes.

### G3. Burn-rate alerts
Alert on *how fast* the budget is burning, not on every error spike. A fast
burn (14.4× over 1 h and 5 m) pages someone. A slow burn (6× over 6 h and
30 m) opens a ticket. That means fewer false pages and real problems caught
early.

### G4. Metric cardinality
Label by route *template* (`/v1/predict`), never by raw path or customer.
Unbounded labels can take down Prometheus.

### G5. Drift
- **Data drift:** inputs change (a price hike moves `monthly_charges`).
  Detected with PSI in `drift_report.py`; above 0.25 means a major shift.
- **Prediction drift:** the score distribution moves. Detected by the
  `ChurnScoreDistributionShift` alert.
- **Concept drift:** the input → churn relationship itself changes. Only
  ground truth reveals it, via `live_eval.py` on `/v1/feedback` data.

### G6. Runbooks
Every alert links to a section of `OPERATIONS.md` that tells the on-call
engineer exactly what to check and do.

### G7. Capacity planning
Load test → requests per pod at SLO → HPA min/max. Keep headroom (HPA
targets 65% CPU) because new pods take time to start.

---

## Part H: Infrastructure as code

- **Terraform** declares networks, clusters, buckets, registries, IAM and
  secrets. Changes are PRs with a `plan` to review, then an `apply` after
  approval (`infra.yml`).
- **Remote state** with locking (S3 native lockfile / GCS), one state per
  environment.
- **Community modules** (VPC, EKS) encode AWS best practice. Pin their
  versions.
- **Scanning** (checkov) catches public buckets, missing encryption and
  over-broad IAM before anything exists.
- **Bootstrap problem:** the state bucket and the Terraform CI role must
  exist before Terraform can run. Create them once by hand; see DEPLOY.md.

---

## Part I: What production adds beyond this repo

This repo is complete for one team and one model. At larger scale you'd
add:
- **Feature store** (Feast, SageMaker/Vertex Feature Store), when many
  models share features or need point-in-time correctness.
- **Pipeline orchestrator** (Airflow, Kubeflow, Vertex/SageMaker
  Pipelines), for multi-step data prep and training DAGs.
- **Managed registry and experiment tracking** (MLflow, W&B).
- **GitOps controller** (Argo CD) and progressive delivery (Argo Rollouts).
- **Data quality framework** (Great Expectations, Soda) on upstream tables.
- **Service mesh** (Istio/Linkerd) for mTLS between many services.
