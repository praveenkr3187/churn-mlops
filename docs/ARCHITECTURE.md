# Architecture

This document explains **what runs where, and how data, models and code move
through the system**. Every diagram is Mermaid, so it renders on GitHub/GitLab
and in most Markdown viewers.

Contents
1. [System context](#1-system-context)
2. [Cloud deployment (AWS and GCP)](#2-cloud-deployment-aws-and-gcp)
3. [Inside the Kubernetes namespace](#3-inside-the-kubernetes-namespace)
4. [Code structure](#4-code-structure)
5. [Request flow: one prediction](#5-request-flow-one-prediction)
6. [Training and retraining flow](#6-training-and-retraining-flow)
7. [Model registry data model](#7-model-registry-data-model)
8. [CI: what runs on every pull request](#8-ci-what-runs-on-every-pull-request)
9. [Code release: build once, promote everywhere](#9-code-release-build-once-promote-everywhere)
10. [Model release: new model, same code](#10-model-release-new-model-same-code)
11. [Canary state machine](#11-canary-state-machine)
12. [Observability: logs, metrics, traces, alerts](#12-observability-logs-metrics-traces-alerts)
13. [Security layers](#13-security-layers)
14. [Feedback loop: monitoring to retraining](#14-feedback-loop-monitoring-to-retraining)
15. [Key design decisions](#15-key-design-decisions)

---

## 1. System context

Who talks to the system, and what it depends on.

```mermaid
flowchart LR
    CRM["CRM / retention app<br/>(API client)"] -- "HTTPS + X-API-Key<br/>POST /v1/predict" --> SYS
    CRM -- "POST /v1/feedback<br/>(did they churn?)" --> SYS
    DS["Data scientist"] -- "PR: code / params" --> GH["GitHub<br/>(CI/CD)"]
    APPROVER["Approver<br/>(ML lead)"] -- "approves release<br/>after reading model card" --> GH
    GH -- "OIDC, no keys" --> SYS
    SYS["Churn prediction system<br/>(Kubernetes on EKS or GKE)"]
    SYS -- "read data / models<br/>write scores" --> STORE[("Object storage<br/>S3 or GCS")]
    SYS -- "secrets" --> SM["Secret manager<br/>AWS SM / GCP SM"]
    SYS -- "metrics, logs, traces" --> OBS["Prometheus, Grafana,<br/>CloudWatch / Cloud Logging,<br/>X-Ray / Cloud Trace"]
    OBS -- "pages / tickets" --> ONCALL["On-call engineer"]
    CRMDB[("CRM database")] -. "nightly batch scores<br/>(scores/latest.csv)" .- STORE
```

## 2. Cloud deployment (AWS and GCP)

The **app layer is identical on both clouds**. Only the infrastructure layer
(Terraform) and a tiny platform layer differ.

```mermaid
flowchart TB
    subgraph Internet
        U["Clients"]
    end
    subgraph Cloud["Cloud account / project (one per environment)"]
        LB["L4 load balancer<br/>AWS NLB | GCP Network LB"]
        subgraph VPC["VPC: private subnets across 3 zones"]
            subgraph K8S["Kubernetes: EKS (managed nodes) | GKE Autopilot"]
                GW["Envoy Gateway<br/>(Gateway API)<br/>TLS, rate limit, retries"]
                subgraph NS["namespace churn-prod"]
                    API["churn-api stable<br/>3-20 pods (HPA)"]
                    CAN["churn-api canary<br/>1 pod"]
                    RT["CronJob retrain<br/>(weekly)"]
                    BS["CronJob batch-score<br/>(nightly)"]
                end
                ADD["Add-ons: cert-manager, External Secrets,<br/>kube-prometheus-stack, OTel collector"]
            end
            NAT["NAT gateway / Cloud NAT"]
        end
        S3[("Buckets: *-models, *-data<br/>S3 | GCS, KMS-encrypted, versioned")]
        REG[("Container registry<br/>ECR | Artifact Registry<br/>immutable tags")]
        SEC[("Secret manager<br/>churn-api")]
    end
    U --> LB --> GW
    GW -- "weight 90%" --> API
    GW -- "weight 10%" --> CAN
    API & CAN & RT & BS -- "pod identity" --> S3
    ADD -- "pod identity" --> SEC
    K8S -. "pull images" .-> REG
```

| Concern | AWS | GCP | Cloud-agnostic piece |
|---|---|---|---|
| Kubernetes | EKS + managed node group | GKE Autopilot | Kubernetes manifests (`k8s/`) |
| Pod → cloud identity | EKS Pod Identity | Workload Identity Federation | ServiceAccounts (no annotations needed) |
| Object storage | S3 (+ VPC endpoint) | GCS (+ Private Google Access) | `fsspec` URIs `s3://` / `gs://` |
| Images | ECR (immutable, scan on push) | Artifact Registry (immutable) | OCI images, cosign signatures |
| Secrets | Secrets Manager | Secret Manager | External Secrets Operator |
| Load balancer | NLB via AWS LB Controller | Network LB | Gateway API + Envoy Gateway |
| Traces | X-Ray | Cloud Trace | OpenTelemetry |
| Metrics | (Amazon Managed Prometheus) | (Google Managed Prometheus) | Prometheus + PodMonitor |
| CI → cloud auth | GitHub OIDC → IAM role | GitHub OIDC → WIF → SA | GitHub environments |
| IaC | `infra/aws` | `infra/gcp` | Terraform |

## 3. Inside the Kubernetes namespace

```mermaid
flowchart LR
    subgraph gateway-system
        GW["Gateway 'public'<br/>:443 TLS"]
    end
    subgraph churn-prod["namespace churn-prod (Pod Security: restricted)"]
        HR["HTTPRoute churn-api<br/>/v1/* only<br/>stable 90 / canary 10"]
        BTP["BackendTrafficPolicy<br/>50 rps per API key,<br/>retries, circuit breaker"]
        SVC1["Service churn-api-stable"]
        SVC2["Service churn-api-canary"]
        D1["Deployment churn-api<br/>track=stable"]
        D2["Deployment churn-api-canary<br/>track=canary"]
        HPA["HPA 3-20 @ 65% CPU"]
        PDB["PDB maxUnavailable 1"]
        CM1["ConfigMap churn-config<br/>(env, bucket URIs)"]
        CM2["ConfigMap churn-stable-release<br/>CHURN_MODEL_VERSION"]
        CM3["ConfigMap churn-canary-release"]
        ES["ExternalSecret -> Secret<br/>API keys, PII salt"]
        NP["NetworkPolicies<br/>default deny"]
        PM["PodMonitor + PrometheusRule"]
        CJ1["CronJob churn-retrain"]
        CJ2["CronJob churn-batch-score"]
    end
    GW --> HR --> SVC1 --> D1
    HR --> SVC2 --> D2
    BTP -.-> HR
    HPA -.-> D1
    PDB -.-> D1
    CM1 & CM2 & ES -.-> D1
    CM1 & CM3 & ES -.-> D2
```

**Kustomize layering:** `base/` (everything) + `components/canary` +
`components/prod` + `overlays/<cloud>-<env>/` (bucket URIs, hostname) +
`overlays/<cloud>-<env>/release/` (machine-managed: image tags, model versions,
traffic weights).

```mermaid
flowchart BT
    base["k8s/base<br/>Deployment, Service, HTTPRoute, HPA, PDB,<br/>NetworkPolicy, ExternalSecret, CronJobs, monitoring"]
    canary["components/canary<br/>2nd Deployment + Service"]
    prod["components/prod<br/>HA: min 3, spread, bigger pods"]
    ov["overlays/aws-prod<br/>namespace, s3:// URIs, hostname"]
    rel["overlays/aws-prod/release<br/>images, model versions, weights<br/>(written by scripts/release.py)"]
    base --> ov
    canary --> ov
    prod --> ov
    rel --> ov
```

## 4. Code structure

```mermaid
flowchart TB
    subgraph src/churn
        config["config.py<br/>settings + DATA CONTRACT<br/>(single source of truth)"]
        data["data.py<br/>load (local/s3/gs) + validate"]
        features["features.py<br/>sklearn Pipeline<br/>(preprocessing inside model)"]
        train["train.py<br/>split, threshold, gates,<br/>fairness, champion/challenger"]
        card["model_card.py"]
        registry["registry.py<br/>versions, aliases, history,<br/>sha256 + lib-version checks"]
        promote["promote.py<br/>promote / rollback CLI"]
        predict["predict.py<br/>ChurnPredictor + batch CLI"]
        schemas["schemas.py<br/>Pydantic API contract"]
        obs["observability.py<br/>JSON logs, Prometheus, OTel,<br/>pseudonymisation"]
        api["api.py<br/>FastAPI service"]
    end
    config --> data & schemas & train & registry & api
    data --> train & predict
    features --> train
    card --> train
    train --> registry
    promote --> registry
    registry --> predict --> api
    schemas --> api
    obs --> api
```

Scripts (`scripts/`) are the operational tools: `generate_data`,
`bootstrap_storage` (local only), `release` (canary state), `deploy.sh`,
`smoke_test.sh`, `canary_analysis`, `check_slo`, `drift_report`,
`live_eval`, `sync_alerts`.

## 5. Request flow: one prediction

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (CRM)
    participant LB as Load balancer
    participant GW as Envoy Gateway
    participant P as churn-api pod
    participant M as In-memory model
    participant L as stdout (JSON logs)
    participant PR as Prometheus

    C->>LB: POST /v1/predict (TLS, X-API-Key)
    LB->>GW: TCP passthrough
    GW->>GW: terminate TLS, rate-limit per key,<br/>pick stable/canary by weight
    GW->>P: HTTP (5s timeout, retry on 503)
    P->>P: middleware: size limit, request-id
    P->>P: auth: constant-time key compare (401)
    P->>P: Pydantic: validate against data contract (422)
    P->>M: predict_proba (preprocessing + GBM, ~8ms)
    M-->>P: probability
    P->>P: threshold (cost-based) -> will_churn, risk band
    P->>L: prediction event (hashed customer ID, features, score, version)
    P->>PR: counters + histograms (latency, score, inputs)
    P-->>C: 200 {probability, will_churn, risk_band, model_version}<br/>+ x-request-id
```

Startup is different: the pod downloads a **pinned** model version from
object storage, verifies its SHA-256 and scikit-learn version, and only then
reports `/ready`. Kubernetes sends no traffic until then.

## 6. Training and retraining flow

```mermaid
flowchart TD
    A([Weekly CronJob or 'make train']) --> B[Load data<br/>s3:// or gs://]
    B --> C{Validate<br/>schema, ranges,<br/>categories, nulls}
    C -- invalid --> X1([Fail loudly: job fails, alert])
    C -- valid --> D[Split 60/20/20<br/>train / validation / test]
    D --> E[Fit Pipeline on train]
    E --> F[Choose threshold on VALIDATION<br/>minimise FN*150 + FP*50]
    F --> G[Evaluate on TEST<br/>AUC, PR-AUC, recall, Brier, cost]
    G --> H[Fairness audit<br/>recall gap by senior_citizen]
    H --> I[Champion/challenger<br/>score production model on same test set]
    I --> J{All gates pass?}
    J -- no --> X2([Record run report, exit 1,<br/>ChurnRetrainJobFailed alert])
    J -- yes --> K[Register immutable version<br/>model.joblib + metadata + model card + sha256]
    K --> L[Alias 'staging']
    L --> M([Human runs model-release workflow])
```

Gates (all configurable via env vars): ROC-AUC ≥ 0.75, recall ≥ 0.60,
Brier ≤ 0.20, fairness recall gap ≤ 0.15, not worse than production by
more than 0.01 AUC.

## 7. Model registry data model

```mermaid
flowchart LR
    subgraph bucket["s3://acme-churn-prod-models/registry  (or gs://...)"]
        subgraph versions["versions/ (immutable)"]
            v1["v20261004-203012/<br/>model.joblib<br/>metadata.json<br/>model_card.md"]
            v2["v20261011-203009/<br/>..."]
        end
        subgraph aliases["aliases/ (movable pointers)"]
            prod["production.json<br/>{version: v20261004..., previous, by, reason}"]
            stg["staging.json<br/>{version: v20261011...}"]
        end
        hist["history.jsonl<br/>audit log of every promotion/rollback"]
    end
    prod --> v1
    stg --> v2
```

`metadata.json` holds: metrics, gates, threshold and costs, fairness,
calibration, feature importance, data URI + fingerprint + row counts,
training reference distributions (for drift), library versions, artifact
SHA-256. **Who can write what** is enforced by IAM (infra/*/iam.tf): the
trainer can add versions and move `staging` only; only the CI deployer,
after approval, can move `production`.

## 8. CI: what runs on every pull request

```mermaid
flowchart LR
    PR([Pull request]) --> Q & T & I & IM & E
    Q["quality<br/>ruff, bandit,<br/>pip-audit (CVEs),<br/>lockfile fresh,<br/>alerts in sync"]
    T["test<br/>unit, model behaviour,<br/>API contract, S3 registry,<br/>release tooling,<br/>k8s policy tests"]
    I["iac<br/>terraform fmt+validate,<br/>kustomize build +<br/>kubeconform, checkov,<br/>hadolint"]
    IM["images<br/>build serve+train,<br/>Trivy scan"]
    E["e2e<br/>docker compose stack,<br/>smoke test,<br/>load test vs SLO"]
    T --> E
    Q & T & I & IM & E --> M{All green?}
    M -- yes --> MERGE([Merge allowed])
    M -- no --> BLOCK([Blocked])
```

## 9. Code release: build once, promote everywhere

```mermaid
sequenceDiagram
    autonumber
    participant Dev as main branch
    participant B as build job
    participant S as deploy-staging
    participant H as Human approver
    participant P as deploy-prod
    participant K as Prod cluster
    participant G as Git (release state)

    Dev->>B: push
    B->>B: build serve+train as OCI archives<br/>Trivy scan, SBOM
    B->>S: artifact (exact bytes)
    S->>S: OIDC login, push to staging registry,<br/>cosign sign + SBOM attestation
    S->>S: release.py set-stable --tag, deploy.sh<br/>(apply, rollout, smoke)
    S->>G: commit overlay release state
    S->>H: waiting for 'prod' environment approval
    H->>P: approve
    P->>P: push same bytes to prod registry, sign
    P->>K: canary 10% -> analysis (10 min)
    P->>K: canary 50% -> analysis (10 min)
    P->>K: promote: stable = new, canary weight 0
    P->>G: commit release state
    Note over P,K: any failure -> canary-abort -> weight 0 -> commit
```

## 10. Model release: new model, same code

```mermaid
sequenceDiagram
    autonumber
    participant CJ as Retrain CronJob
    participant R as Registry
    participant A as Approver
    participant W as model-release workflow
    participant K as Prod cluster

    CJ->>R: register v-new (all gates passed), alias staging
    A->>W: run workflow (version=staging)
    W->>R: resolve version, re-check gates
    W->>A: model card in run summary
    A->>W: approve 'prod' environment
    W->>K: canary pod with CHURN_MODEL_VERSION=v-new, 10%
    W->>K: analysis: errors, latency, mean score shift
    W->>K: 50% -> analysis -> promote
    W->>R: promote v-new to production (audit record)
```

Code and model have **independent release trains**: a model change never
needs a new image, and a code change keeps the current model.

## 11. Canary state machine

```mermaid
stateDiagram-v2
    [*] --> Idle: canary = stable, weight 0
    Idle --> Canary10: canary-start (new tag or model)
    Canary10 --> Canary50: analysis PROMOTE
    Canary10 --> Idle: analysis ROLLBACK -> canary-abort
    Canary10 --> Canary10: INCONCLUSIVE (wait, max 3x)
    Canary50 --> Promoted: analysis PROMOTE
    Canary50 --> Idle: analysis ROLLBACK -> canary-abort
    Promoted --> Idle: stable = canary, weight 0
```

Analysis (`scripts/canary_analysis.py`) compares canary vs stable over the
window: error ratio (+0.5 pp max), p95 latency (×1.2 + 20 ms max), mean
predicted score (±0.15 max) and requires ≥ 200 requests.

## 12. Observability: logs, metrics, traces, alerts

```mermaid
flowchart LR
    subgraph pod["churn-api pod"]
        APP["FastAPI app"]
    end
    APP -- "JSON lines on stdout<br/>(request + prediction + feedback events)" --> LOGS["Fluent Bit / GKE logging agent"]
    LOGS --> CL["CloudWatch Logs | Cloud Logging"]
    CL -- "export / sink" --> ARCH[("S3 / GCS<br/>event archive")]
    ARCH --> DRIFT["drift_report.py (PSI)<br/>live_eval.py (live recall)"]
    APP -- "/metrics (scraped every 15s)" --> PROM["Prometheus"]
    PROM --> GRAF["Grafana dashboard<br/>service + model panels"]
    PROM --> RULES["Alert rules<br/>SLO burn rate, latency,<br/>score shift, retrain failed"]
    RULES --> AM["Alertmanager"]
    AM -- "severity=page" --> PAGER["PagerDuty / Opsgenie"]
    AM -- "severity=ticket" --> CHAT["Slack / Teams / Jira"]
    APP -- "OTLP spans" --> OTEL["OTel Collector"]
    OTEL --> TR["X-Ray | Cloud Trace"]
    PROM -- "canary vs stable queries" --> CA["canary_analysis.py"]
```

Three kinds of signal, because a service can be **up but wrong**:

| Signal | Question it answers | Where |
|---|---|---|
| Service health | Is it up, fast, error-free? | `/metrics` → SLO burn-rate alerts |
| Model behaviour | Are inputs or scores shifting? | score/input histograms, PSI report |
| Model quality | Is it still *right*? | feedback join → live recall |

## 13. Security layers

```mermaid
flowchart TB
    subgraph L1["Supply chain"]
        a1["hashed lockfile"] --> a2["pip-audit, Trivy, SBOM"] --> a3["cosign signatures"] --> a4["immutable tags"]
    end
    subgraph L2["Identity"]
        b1["GitHub OIDC (no CI keys)"] --> b2["pod identity (no pod keys)"] --> b3["least-privilege IAM per workload"]
    end
    subgraph L3["Network"]
        c1["TLS at gateway"] --> c2["only /v1/* routed"] --> c3["rate limit per key"] --> c4["NetworkPolicy default deny"]
    end
    subgraph L4["Workload"]
        d1["non-root, read-only FS,<br/>no capabilities, seccomp"] --> d2["Pod Security 'restricted'"] --> d3["no SA token mounted"]
    end
    subgraph L5["Application"]
        e1["API keys (constant-time)"] --> e2["strict input schema"] --> e3["payload limit"] --> e4["model checksum on load"]
    end
    subgraph L6["Data"]
        f1["KMS encryption"] --> f2["no public buckets, TLS only"] --> f3["pseudonymised IDs in logs"] --> f4["secrets never in Git or TF state"]
    end
    L1 --> L2 --> L3 --> L4 --> L5 --> L6
```

## 14. Feedback loop: monitoring to retraining

```mermaid
flowchart LR
    SERVE["Serve predictions"] --> LOG["Prediction events"]
    CRM["CRM: actual churn<br/>(weeks later)"] --> FB["/v1/feedback"]
    LOG & FB --> JOIN["live_eval.py<br/>join on customer_hash"]
    LOG --> PSI["drift_report.py<br/>PSI vs training_stats"]
    JOIN -- "recall < gate" --> TRIG{Retrain?}
    PSI -- "PSI > 0.25" --> TRIG
    SCHED["Weekly schedule"] --> TRIG
    TRIG --> RETRAIN["Retrain CronJob<br/>(gates, champion/challenger)"]
    RETRAIN --> REL["Model release<br/>(approval + canary)"]
    REL --> SERVE
```

## 15. Key design decisions

| Decision | Why | Alternative (when to choose it) |
|---|---|---|
| Preprocessing inside the saved Pipeline | Removes training/serving skew | Feature store (many models sharing features) |
| Registry on object storage | Zero extra infrastructure; same on AWS/GCP/local | MLflow / SageMaker / Vertex AI registry (many teams, UI, lineage) |
| Model pulled at startup, pinned version | Model and code release independently | Bake model into image (tiny model, strict immutability) |
| One Uvicorn worker per pod, `OMP_NUM_THREADS=1` | Predictable memory, correct metrics, no thread fights; scale with pods | Gunicorn multi-worker on VMs |
| Kustomize over Helm | Plain YAML, easy to read and diff | Helm (distributing to many external users) |
| Gateway API + Envoy Gateway | ingress-nginx retired (Mar 2026); weighted canary built into the standard | Istio / Argo Rollouts (bigger platform needs) |
| Push-based deploy from CI, state committed to Git | Easy to follow end to end | Argo CD / Flux GitOps (pull-based, drift correction) |
| API keys | Simple for server-to-server | OAuth2 client credentials / mTLS (many clients, fine-grained scopes) |
| Human approval for prod | Model decisions affect customers | Fully automatic once trust is earned (strong canary + monitoring) |
