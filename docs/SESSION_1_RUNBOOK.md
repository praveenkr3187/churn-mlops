# Session 1: Taking an ML model to production, end to end

**Goal:** by the end, the mentee can explain every box in
[ARCHITECTURE.md](ARCHITECTURE.md) and has personally run the path from raw
data to a gated, versioned, containerised model serving traffic on
Kubernetes, with a canary release and a rollback.

**Format.** The full scope doesn't fit in one hour, so the session has two
parts:
- **Part 1, live walkthrough (90 min):** you drive, she asks. Covers every
  concept, with live demos on the laptop.
- **Part 2, labs (self-paced, ~6-8 h over the week):** she drives. Eight
  labs, each ending in a checkpoint she shows you.
- **Checkpoint call (30 min)** before Session 2: she demos Labs 5-7 and
  walks the architecture diagram back to you.

Reading order for her: `README.md` → `docs/CONCEPTS.md` (Parts A-C before
the session) → the rest during the labs.

---

## Before the session

**Mentee (30 min):** Python 3.11, Git, Docker Desktop, `kind`, `kubectl`,
`kustomize`. Run `make install && make data && make test`. Read
CONCEPTS.md Parts A-C.

**You:** `make clean && make data`; have `make up` already built once (image
builds are slow); open Grafana; have ARCHITECTURE.md rendered on screen.
Three terminals: **A** server, **B** commands, **C** `tail -f logs/predictions.jsonl`.

---

## Part 1: live walkthrough (90 min)

### 0. The hook (5 min)
Ask: *"Your notebook model has 0.82 AUC. What must be true before a
retention team can rely on it at 2 a.m. on a Sunday?"* Let her list, then
map her answers onto the **System context** diagram. Most people miss:
validation, versioning, rollback, identity, monitoring model *quality*, and
who approves.
> The model is ~10% of a production ML system.

### 1. Data contract and validation (8 min): CONCEPTS A2
- `config.py`: one contract; `data.py` and `schemas.py` are both generated
  from it.
- **Break it live:** set one row to `contract_type=lifetime`, then run
  `make train`. It fails immediately with a clear message.

### 2. Pipeline, splits, threshold (10 min): A1, B2, B3
- `features.py`: preprocessing inside the artifact, which kills training/serving skew.
- `train.py`: 60/20/20 split; threshold chosen by *cost* on validation.
- Ask: *"Why is our threshold 0.19 and not 0.5?"* (a missed churner costs
  3× an unnecessary offer).

### 3. Gates, fairness, champion/challenger, model card (12 min): B4-B8
- `make train`, then read the PASS/FAIL lines together.
- Open `models/versions/<v>/model_card.md`. This is exactly what an approver
  reads.
- `make promote`, then `make train` again, and point out the new
  `not_worse_than_production` gate.
- Quality-gate demo: `CHURN_MIN_ROC_AUC=0.99 python -m churn.train; echo $?` gives exit 1.

### 4. Registry (7 min): A5, A7
- Tour `models/`: `versions/`, `aliases/`, `history.jsonl`.
- Tamper demo: append bytes to a `model.joblib`, then run `make serve`; it
  refuses to load (checksum). Explain why pickles are dangerous.
- `python -m churn.promote --rollback`.

### 5. The API (12 min): C2-C7, F
- `make serve`, then `/docs`. Walk `api.py` top to bottom, one production
  concern per block.
- Terminal C: prediction events with **hashed** customer IDs.
- `curl localhost:8000/metrics | grep churn_`: route templates, not raw
  paths (cardinality).
- Ask: *"Why is there no endpoint to swap the model at runtime?"*

### 6. The OpenMP story (5 min): C8, C9
Tell it as a war story: the load test failed the SLO, profiling showed
8 ms inference but 340 ms p50 under load, and the cause was OpenMP thread
pools fighting. The fix was one environment variable. Second lesson: locust
on the same machine inflated p95 from 17 ms to 350 ms.
> "You'd never find this by reading code. Only by measuring."

### 7. Containers and the local stack (8 min): A4, A6
- `Dockerfile`: hashed lockfile, two targets, non-root, no model inside.
- `make up`, then Grafana: send traffic and watch the panels move.

### 8. Kubernetes (10 min): Part D
- Show the **Inside the namespace** and **Kustomize layering** diagrams.
- `make k8s-render OVERLAY=aws-prod | less`: point at probes, securityContext,
  HPA, PDB, NetworkPolicy, weighted HTTPRoute.
- `diff <(make -s k8s-render OVERLAY=aws-prod) <(make -s k8s-render OVERLAY=gcp-prod)`:
  only URIs, registry and names differ. **The app layer is cloud-agnostic.**

### 9. Releasing (8 min): Part E
- **Code release** and **Canary state machine** diagrams.
- Live: `python scripts/release.py canary-start --overlay aws-prod --model vX --weight 10`,
  then `git diff`. That's the entire release, recorded in Git.
- `pytest tests/test_release.py -k judgement -v`: the automated
  promote/rollback decision.

### 10. Cloud, security, operations (5 min): Parts F-H
- `infra/aws/iam.tf`: the identity map (trainer can't promote to production).
- `monitoring/alerts.yml`: burn-rate alerts, each linking to OPERATIONS.md.
- The Trivy incident (SECURITY.md): pin CI actions to SHAs.

---

## Part 2: labs (she drives)

Each lab ends in a **checkpoint**: something concrete she shows you.

| # | Lab | Time | Checkpoint |
|---|---|---|---|
| 1 | **Local loop.** `make data train promote test serve smoke`. Break the data 3 different ways and read each error. | 45 min | Explains each failure message |
| 2 | **Governance.** Change `CHURN_COST_FN` to 300, retrain, and compare threshold/recall/cost in the two model cards. Make the fairness gate fail by lowering `CHURN_MAX_RECALL_GAP`. | 45 min | Two model cards side by side, with her explanation |
| 3 | **Add a feature end to end** (`num_dependents`): generator → `config.py` → tests. Count how many files change and why so few do. | 60 min | PR-style diff with all tests green |
| 4 | **Compose stack.** `make up`; find her predictions in MinIO and Prometheus; build one new Grafana panel. | 45 min | Screenshot of her panel |
| 5 | **Kubernetes (kind).** `make kind-up kind-smoke`. Delete a pod and watch it heal. Break the image tag and read the events. Run a manual retrain job. | 60 min | Recovers from the broken tag using `kubectl rollout undo` |
| 6 | **Load and capacity.** Run locust from a *second* machine (or throttle it with `taskset`). Find the req/s per pod at p95 < 250 ms; remove `OMP_NUM_THREADS=1` and measure again; compute HPA min/max for 200 req/s peak. | 60 min | Her capacity table and HPA numbers |
| 7 | **Release drill.** On kind (or paper): canary-start a new model at 10%, simulate bad metrics in `test_canary_judgement`, abort, then promote a good one. Write the incident note for the aborted release. | 45 min | Incident note using the template |
| 8 | **Drift and feedback.** `make drift-data`; `drift_report.py --csv`; send feedback for 100 customers via the API; run `live_eval.py`. Decide: retrain or not? | 45 min | A one-paragraph retrain decision with evidence |
| ★ | **Stretch: real cloud.** Follow DEPLOY.md Level 3 on a free-tier/credit account for staging only; tear it down the same day. | 3 h | Smoke test passing over HTTPS |

---

## Likely questions (short answers)

- **Why not SageMaker/Vertex endpoints?** They're great, but they hide the
  moving parts. Learn the parts once on Kubernetes, then the managed
  services are easy to evaluate. The registry and pipeline ideas map 1:1.
- **Why Kustomize, not Helm?** It's plain YAML you can read and diff. Helm
  shines when you distribute charts to others.
- **Why pull the model at startup instead of baking it into the image?**
  So models and code release independently (weekly retrains don't need
  image builds). Baking is fine for tiny, rarely changing models.
- **Why human approval if we have gates?** Gates catch known failure modes.
  A human catches the business ones ("this model flags 80% of seniors").
  Automate approval once canaries and monitoring have earned trust.
- **Where would an LLM fit?** Same skeleton: validate input, a versioned
  prompt+model config instead of `model.joblib`, the same probes, logging,
  canary and gates (evals instead of AUC). That's Session 3.
- **Is this over-engineered for one model?** Some of it, for a hobby
  project, yes. Every piece here exists because its absence caused a real
  incident somewhere. The skill is knowing which pieces a given system
  needs.

## Bridges to the next sessions
- **Session 2 (time series):** why is our random `train_test_split` wrong
  for temporal data? Look at the retrain job: what should "test" mean when
  you retrain weekly?
- **Session 3 (system design):** add a feature store, a queue for async
  scoring, and an LLM explanation service. Which boxes in ARCHITECTURE.md
  change?
- **Session 4 (monitoring and retraining):** automate
  `drift_report.py` + `live_eval.py` as CronJobs that open a retrain
  ticket.
