# Teaching guide: production ML deployment with this repo

A complete curriculum for mentoring one engineer from "I can train a model in a
notebook" to "I can ship, release, operate and roll back a model in production,
and explain every decision". It uses this repository as the single worked
example from start to finish.

- **Who it's for:** you (the mentor). The mentee reads the linked docs; you use
  this file to plan sessions, run demos, set labs and check understanding.
- **Relation to other docs:** [SESSION_1_RUNBOOK.md](SESSION_1_RUNBOOK.md) is a
  compressed 90-minute tour of everything. This guide is the full course:
  the same material spread over 11 modules, with much deeper CI/CD coverage.
- **Duration:** about 5 weeks at 2 sessions/week (60-90 min each) plus
  4-6 h/week of labs. Modules 6-7 (CI/CD) are the longest on purpose.

---

## How to teach with this repo

These rules matter more than the content order.

1. **Why before how.** Open each module with the failure it prevents ("a model
   was deployed that nobody could reproduce"). Then show the code that
   prevents it. [CONCEPTS.md](CONCEPTS.md) is written in exactly this
   what / why / where format; lean on it.
2. **Break it first.** Every module has a "break it" demo. People remember a
   red pipeline they caused far better than a green one they watched.
3. **She drives in labs.** In labs you don't touch the keyboard. When she's
   stuck, ask a question ("what does the error say? which file produces it?")
   rather than giving the answer.
4. **Every lab ends in a checkpoint** she shows you: a diff, a screenshot, a
   written paragraph, a green run. No checkpoint, not done.
5. **Explain it back.** End each session by having her explain one diagram
   from [ARCHITECTURE.md](ARCHITECTURE.md) to you without notes.
6. **Real Git from day 1.** She works in her own GitHub repo (see Module 0),
   uses branches and pull requests for every lab, and CI reviews her work
   before you do. That habit is itself half of production engineering.

---

## Course map

| # | Module | Core question | Sessions | Lab time |
|---|---|---|---|---|
| 0 | Orientation and setup | What is "production", and what's in this repo? | 1 | 1 h |
| 1 | Production ML code | How do we make a model *trustworthy* before it ships? | 1 | 2 h |
| 2 | Model registry and governance | Which model is live, who approved it, how do we undo it? | 1 | 1 h |
| 3 | The serving API | How does a model answer requests safely? | 1 | 2 h |
| 4 | Containers and supply chain | How do we ship the exact same thing everywhere? | 1 | 1.5 h |
| 5 | Kubernetes | How does it run, scale and heal? | 2 | 3 h |
| 6 | **Continuous Integration** | How do we stop bad changes *before* merge? | 2 | 4 h |
| 7 | **Continuous Delivery / Deployment** | How do good changes reach prod safely, and bad ones get rolled back? | 2 | 4 h |
| 8 | Infrastructure as code | How do we create the cloud itself, reviewably? | 1 | 2 h (+3 h stretch) |
| 9 | Observability and operations | How do we know it's working, and what do we do at 2 a.m.? | 1 | 2 h |
| 10 | Security, end to end | Who can do what, and how do we prove what we shipped? | 1 | 1 h |
| ★ | Capstone | Ship a feature through the whole system | — | 4-6 h |

The order follows the path of a change: code → model → API → image → cluster
→ pipeline → cloud → operations.

---

## Module 0: Orientation and setup

**Goal:** she can state what separates a notebook model from a production
system, has the repo running, and has her own GitHub copy with CI running.

**Read before:** [README.md](../README.md), [CONCEPTS.md](CONCEPTS.md) intro.

**Session (60 min)**
1. The hook: *"Your model has 0.82 AUC. What must be true before a retention
   team can rely on it at 2 a.m. on a Sunday?"* Write her list on a whiteboard.
   Then map it onto the system-context diagram in ARCHITECTURE.md. Typical
   gaps: validation, versioning, rollback, identity, *model-quality*
   monitoring, approvals.
2. Repo tour with `README.md` → "Repository map". Show that the ML is ~10% of
   the code.
3. **Show the hidden folder.** `.github/` holds all CI/CD. In macOS Finder it's
   hidden: press `Cmd + Shift + .`, or use `ls -la`. Many beginners think a
   repo "has no CI" for exactly this reason.

**Setup lab (she does)**
```bash
# tools: Python 3.11, Git, Docker Desktop, kind, kubectl, kustomize, gh
python -m venv .venv && source .venv/bin/activate
make install data train promote test

# her own GitHub repo (public = free Actions minutes + free environments)
git init && git add . && git commit -m "Initial import"
gh repo create churn-mlops --public --source . --push
pip install pre-commit && pre-commit install
```
Then open the **Actions** tab: `ci` and `cd-kind` start automatically.
`release`, `model-release` and `infra` will fail or skip until a cloud exists;
that's expected, and part of Module 7's discussion.

**Checkpoint:** `make test` green locally; a link to her repo with a green `ci` run.

---

## Module 1: Production ML code

**Goal:** she can explain how data contracts, pipelines, cost-based thresholds,
quality/fairness gates and model cards make a model trustworthy.

**Read:** CONCEPTS.md Part A (A1-A3) and Part B.
**Files:** `src/churn/config.py`, `data.py`, `features.py`, `train.py`, `model_card.py`.

**Explain**
- **Data contract** (`config.py`): one definition of columns, types and allowed
  values; validation *and* the API schema derive from it, so they can't drift.
- **Pipeline inside the artifact** (`features.py`): preprocessing ships with
  the model, which removes training/serving skew.
- **60/20/20 split, threshold by cost:** why the threshold is ~0.19, not 0.5
  (a missed churner costs 3× an unnecessary offer).
- **Gates:** minimum ROC AUC, fairness (recall gap across groups), and
  "not worse than production". A failed gate exits 1, which is what stops a
  pipeline.
- **Model card:** the document a human approver reads.

**Break it (demo)**
- Set one row's `contract_type` to `lifetime` in `data/customers.csv`, then run `make train`. It fails fast with a clear message.
- `CHURN_MIN_ROC_AUC=0.99 python -m churn.train; echo $?` prints `1`.

**Lab**
1. Break the data three different ways (missing column, negative tenure,
   wrong type). Record each error message.
2. Set `CHURN_COST_FN=300`, retrain, and compare threshold, recall and cost
   across the two model cards.
3. Make the fairness gate fail by lowering `CHURN_MAX_RECALL_GAP`.

**Checkpoint:** two model cards side by side with her written explanation of the differences.

**Ask her:** Why is exit code 1 so important? What would happen if validation
lived only in the notebook?

---

## Module 2: Model registry and governance

**Goal:** she can explain versions vs aliases, promotion, rollback and why
model files are integrity-checked.

**Read:** CONCEPTS.md A5, A7. **Files:** `src/churn/registry.py`, `promote.py`.

**Explain**
- Immutable **versions** + movable **aliases** (`staging`, `production`).
  Deploying a model means moving a pointer, and rollback means moving it back.
- `history.jsonl`: the audit trail (who, when, why).
- Checksums + library-version checks: a pickle is executable code, so a
  tampered or incompatible file must refuse to load.
- The trainer may write `staging`; only an approved pipeline moves
  `production` (enforced by IAM in `infra/*/iam.tf`, revisited in Modules 8 and 10).

**Break it:** append bytes to a `models/versions/<v>/model.joblib`, then run
`make serve`. It refuses to start. Then `python -m churn.promote --rollback`.

**Lab:** train three versions, promote two, roll back once, and reconstruct
the full story from `history.jsonl` alone.

**Checkpoint:** she narrates the history file as a timeline.

---

## Module 3: The serving API

**Goal:** she can walk through `api.py` and name the production concern each block handles.

**Read:** CONCEPTS.md Part C. **Files:** `src/churn/api.py`, `schemas.py`, `observability.py`.

**Explain (one concern per block)**
API keys and admin keys · strict request schema (422 on bad input) · `/v1`
versioning · liveness vs readiness probes · structured JSON logs ·
Prometheus metrics with *route templates* (cardinality) · pseudonymised
(hashed) customer IDs in prediction events · `/v1/feedback` for ground truth ·
no runtime "swap model" endpoint (changes go through the pipeline).

**Demo:** `make serve`; open `/docs`; `make smoke`; `tail -f logs/predictions.jsonl`;
`curl localhost:8000/metrics | grep churn_`.

**War story (5 min):** OpenMP threads. Inference took 8 ms, but p50 under
load was 340 ms because thread pools fought each other. The fix was
`OMP_NUM_THREADS=1`, and it was found only by load testing. Point to the
comment in the `Dockerfile`.

**Lab**
1. Read `scripts/smoke_test.sh`. Why does it check that "risky > loyal"
   instead of just checking for HTTP 200?
2. Add a field to `/v1/model-info` (e.g. training row count) with a test.
3. `make load`, then remove `OMP_NUM_THREADS=1` and run it again. Compare p95.

**Checkpoint:** PR with the new field + test, CI green.

---

## Module 4: Containers and supply chain

**Goal:** she understands why we build one image once and promote it, and how
it's kept reproducible and safe.

**Read:** CONCEPTS.md A4, A6. **Files:** `Dockerfile`, `.dockerignore`, `requirements.lock`, `docker-compose.yml`.

**Explain**
- Multi-stage, two targets (`serve`, `train`), non-root user, no model or data
  inside the image (models are released separately from code).
- **Hashed lockfile:** `pip install --require-hashes`, so a compromised package
  mirror can't swap a dependency. CI fails if the lock is stale.
- `.dockerignore`: smaller images, and no secrets or data leak into layers.
- The compose stack mimics the cloud: MinIO = S3/GCS, plus Prometheus + Grafana.

**Demo:** `make up`; send traffic; watch Grafana panels move; open the MinIO console.

**Lab:** find her predictions in MinIO and in Prometheus; add one Grafana panel.
Then change a dependency in `pyproject.toml` *without* running `make lock` and
push. Watch CI catch it (this previews Module 6).

**Checkpoint:** a screenshot of her panel, plus the red CI run and its fix.

---

## Module 5: Kubernetes

**Goal:** she can read every rendered manifest, deploy locally on kind, and
recover from a broken rollout by hand.

**Read:** CONCEPTS.md Part D. **Files:** `k8s/base/*`, `k8s/components/*`, `k8s/overlays/*`.

**Session 1: objects**
- Deployment (rolling update, `maxUnavailable: 0`, probes, securityContext),
  Service, HPA, PDB, NetworkPolicy, CronJobs (retrain, batch-score),
  ExternalSecret, HTTPRoute (weighted canary).
- `make k8s-render OVERLAY=aws-prod | less`, and walk through it top to bottom.

**Session 2: layering**
- Kustomize: base → components (canary, prod) → overlays (local, aws/gcp ×
  staging/prod) → `release/` (machine-managed state).
- `diff <(make -s k8s-render OVERLAY=aws-prod) <(make -s k8s-render OVERLAY=gcp-prod)`
  shows that only URIs, registry and names differ. The app layer is cloud-agnostic.
- `tests/test_k8s.py`: policy is **tested**, not remembered (non-root, limits,
  probes, no `:latest`, prod HA).

**Lab**
1. `make kind-up kind-smoke`.
2. Delete a pod and watch it heal.
3. Break the image tag in `k8s/overlays/local/release/kustomization.yaml`,
   then apply. Read the events. Note that the old pods keep serving (why?).
   Recover with `kubectl rollout undo`.
4. Trigger the retrain CronJob manually:
   `kubectl -n churn-local create job --from=cronjob/churn-retrain manual-1`.

**Checkpoint:** she recovers from the broken tag live, explaining each command.

**Ask her:** Why does changing the model version (a ConfigMap) trigger a
rolling update? (Hint: generated ConfigMaps have content-hash names.)

---

## Module 6: Continuous Integration (CI)

**Goal:** she can read, run, extend and debug the CI pipeline, and explain why
each check exists.

**Read:** `.github/workflows/ci.yml` (in full), `.pre-commit-config.yaml`,
`.github/pull_request_template.md`, `.github/CODEOWNERS`, `.github/dependabot.yml`.

### Session 1: CI concepts and a walk through `ci.yml`

Start with vocabulary, using `ci.yml` as the example for each term:

| Concept | Where to point in `ci.yml` |
|---|---|
| Trigger (`on:`) | `pull_request` + `push` to `main` |
| Job, step, runner | 5 jobs, each on a fresh `ubuntu-latest` VM |
| Parallel vs sequential | jobs run in parallel; `e2e` has `needs: [test]` |
| Least-privilege token | `permissions: contents: read` |
| Cancel stale runs | `concurrency: cancel-in-progress: true` |
| Artifacts | `upload-artifact` of JUnit test reports |
| Failure diagnostics | `if: failure()` → dump compose logs |

Then walk the five jobs and the **failure each one prevents**:

| Job | Checks | Prevents |
|---|---|---|
| `quality` | ruff, bandit, pip-audit, lockfile freshness, alert-rule sync | style/bug patterns, insecure code, known CVEs, "works on my machine" deps, alerts drifting from k8s |
| `test` | 76 tests: data, model behaviour, API contract, registry (S3 via moto), release tooling, k8s policy | regressions, bad models, broken API contracts, insecure manifests |
| `iac` | terraform fmt/validate, kustomize render + kubeconform, Checkov, Hadolint | invalid infra, invalid YAML reaching the cluster, misconfigurations |
| `images` | build both images, Trivy scan | shipping fixable HIGH/CRITICAL CVEs |
| `e2e` | full compose stack, smoke test, 60 s load test vs SLO | "unit tests pass but the system doesn't work", latency regressions |

Key ideas to land:
- **The pyramid:** fast, cheap checks first (lint in seconds) → slow, expensive
  ones last (e2e in minutes).
- **Shift left:** the same checks run on the laptop via `pre-commit` and
  `make ci`, so CI is confirmation, not discovery.
- **CI is a gate only if it's enforced:** branch protection with required
  status checks + CODEOWNERS review. Otherwise it's advice.
- **Dependabot:** small, frequent, CI-verified upgrades beat a scary yearly one.

### Session 2: break it, then extend it

**Break it (she does it, one PR per break; watch which job goes red):**

| Break | Expected red job |
|---|---|
| Add `import os` unused in `api.py` | `quality` (ruff) |
| Add `eval(input())` somewhere in `src/` | `quality` (bandit) |
| Add a dependency in `pyproject.toml` without `make lock` | `quality` (lockfile) |
| Edit `monitoring/alerts.yml` without `make k8s-alerts` | `quality` (sync) |
| Remove `readinessProbe` from `k8s/base/deployment.yaml` | `test` (k8s policy) |
| Change a label in the smoke test's "risky" customer so it looks loyal | `e2e` |
| Set `image: churn-api:latest` in an overlay | `test` (policy: never `:latest`) |

**Lab**
1. Turn on branch protection for `main`: require the 5 `ci` checks + 1
   review, and code-owner reviews. Try to merge a red PR.
2. **Extend CI:** add a step or job for one of the following, and justify it in the PR:
   - `actionlint` (lints the workflows themselves), or
   - `pytest --cov=churn --cov-fail-under=80` (coverage floor), or
   - a `model-quality` job that trains on synthetic data and uploads the model
     card as an artifact.
3. **Pin actions to SHAs:** run `pinact run` (or do it by hand for one file)
   and read the Trivy incident in [SECURITY.md](SECURITY.md) to explain why.
4. Read one Dependabot PR (or simulate one) and decide: merge or not, and why?

**Checkpoint:** a merged PR that adds a CI check, a screenshot of branch
protection blocking a red PR, and a short table of "break → job that caught it".

**Ask her:** Why does `e2e` wait for `test`, but `images` doesn't wait for
anything? What would you move into pre-commit, and what would you never move
there?

---

## Module 7: Continuous Delivery and Deployment (CD)

**Goal:** she can explain and operate all four delivery pipelines, run a
real deploy + rollback in CI for free, and design a release strategy.

**Read:** CONCEPTS.md Part E; ARCHITECTURE.md "Code release", "Canary state
machine", "Model release" diagrams; all of `.github/workflows/` and
`.github/actions/`; `scripts/release.py`, `scripts/deploy.sh`,
`scripts/canary_analysis.py`.

### The four delivery pipelines (plus one rehearsal)

| Workflow | Trigger | What it ships | Needs cloud? |
|---|---|---|---|
| `cd-kind.yml` | PR / push touching app or k8s, manual | nothing real: **rehearses** deploy, release, bad-release rollback on kind | **No** (free) |
| `release.yml` | push to `main` | new **code** (image) → staging → approval → prod canary | Yes |
| `model-release.yml` | manual (`gh workflow run model-release -f version=staging`) | new **model**, same image → approval of model card → canary | Yes |
| `infra.yml` | PR / push touching `infra/` | **Terraform**: plan on PR, apply on merge (prod gated) | Yes |
| retrain CronJob (in k8s) | weekly | a *candidate* model under `staging` alias; never auto-promoted | Yes |

### Session 1: concepts, and the free rehearsal

Terms to define, with where each appears:

- **Delivery vs deployment:** we do continuous *delivery* to prod (a human
  approves), and continuous *deployment* to staging.
- **Build once, promote the same bytes:** `release.yml` `build` job makes OCI
  archives once; each environment pushes those *identical* bytes
  (`actions/push-images`). A rebuild per environment would mean "prod runs
  something that was never tested".
- **Environments:** `staging`, `prod` (required reviewers), `prod-review`,
  `infra-*`. Each environment holds its own variables and secrets, and is the approval gate.
- **OIDC, no stored cloud keys:** `actions/cloud-auth` exchanges a GitHub
  token for short-lived AWS/GCP credentials. The cloud trusts "repo X,
  environment prod", and nothing else. (`permissions: id-token: write`.)
- **GitOps-lite:** `scripts/release.py` edits `k8s/overlays/<env>/release/`,
  `deploy.sh` applies it, and `actions/commit-release` commits it back. So
  **Git history = deployment history**, and `git revert` = rollback.
- **Same script everywhere:** humans and pipelines both run `scripts/deploy.sh`,
  so there are no "works only in CI" surprises.
- **`concurrency: release`, `cancel-in-progress: false`:** never run two
  releases at once, and never kill one halfway.

**Live demo: `cd-kind.yml`.** Trigger it from the Actions tab ("Run
workflow") and read the run summary together:
1. *Drill 1, first deploy:* applies the local overlay, bootstraps a model,
   smoke-tests.
2. *Drill 2, good release:* a new image tag through `release.py set-stable` →
   the `git diff` of the release file → rolling update → smoke test.
3. *Drill 3, bad release:* tag `does-not-exist` → rollout fails within 90 s
   (old pods keep serving because `maxUnavailable: 0`) → release state is restored
   → re-deployed → smoke test passes.

Then reproduce drill 3 by hand on her kind cluster:
```bash
python scripts/release.py show --overlay local
python scripts/release.py set-stable --overlay local --tag does-not-exist
ROLLOUT_TIMEOUT=60s SMOKE_API_KEY=dev-key scripts/deploy.sh local     # fails
git checkout k8s/overlays/local/release/                             # "git revert"
SMOKE_API_KEY=dev-key scripts/deploy.sh local                        # healthy
```

### Session 2: progressive delivery (canary) and model releases

- Walk `release.yml` `deploy-production` step by step: canary 10% →
  `canary-gate` (wait, then `canary_analysis.py` compares canary vs stable
  error rate and latency in Prometheus: pass / fail / inconclusive) →
  50% → gate → promote → **`if: failure()` → `canary-abort`**.
- Traffic weights are just a patch on the `HTTPRoute` in `release/`. Show
  `python scripts/release.py canary-start --overlay aws-prod --tag x --weight 10`
  then `git diff`. That's the entire release, as data. (`git checkout` afterwards.)
- `pytest tests/test_release.py -v`: the state machine and the promote/rollback
  judgement are unit-tested.
- **Model release vs code release:** same canary machinery, but the approver
  reads the **model card** (put into the run summary by the `review` job) and
  the final step moves the registry's `production` alias, with the run ID as
  the audit reason.
- **Infra pipeline:** plan on PR (reviewers read the plan), apply on merge,
  staging before prod (`max-parallel: 1`), separate, more-privileged identity.

**Lab**
1. **Trace a release on paper:** draw the sequence diagram of `release.yml`
   from `git push` to 100% traffic, marking where a human is involved, where
   credentials are obtained, and where Git is written. Then answer: *what
   exactly happens if the canary fails at 50%?* (Which steps run, what
   traffic gets, what gets committed?)
2. **Canary drill on paper/kind:** start a canary for a new model at 10%, make
   `test_canary_judgement` produce "fail" by adjusting its inputs, abort, then
   promote a good one. Write the incident note (template in
   [OPERATIONS.md](OPERATIONS.md)).
3. **Approval gates for free:** in her public repo create an environment
   `prod` with herself as required reviewer. Add a tiny workflow
   `approval-demo.yml` with a job using `environment: prod` that prints
   `release.py show --overlay aws-prod`. Trigger it and approve it. Now she has
   *felt* the gate that guards production.
4. **Extend `cd-kind.yml`:** add a "Drill 4": run the retrain CronJob as a Job,
   promote its model with `churn.promote`, restart the API, and smoke-test it.
   That's a model release rehearsal.
5. **Design question (written, 1 page):** your company forbids `kubectl` from
   CI. How would you change the CD design? (Expected answer: Argo CD/Flux
   pull from Git; CI only commits to `release/`; Argo Rollouts for canary.)

**Checkpoint:** her release sequence diagram, a green `cd-kind` run including
her Drill 4, the incident note, and the design page.

**Ask her:** Why is the model released separately from the code? Why does the
retrain job only ever write `staging`? What would make you comfortable
removing the human approval?

---

## Module 8: Infrastructure as code (Terraform)

**Goal:** she can read the Terraform for one cloud, explain the identity
model, and knows the bootstrap order.

**Read:** CONCEPTS.md Part H; [DEPLOY.md](DEPLOY.md) Level 3. **Files:** `infra/aws/*` or `infra/gcp/*`, `infra/bootstrap/*`.

**Explain:** network → cluster → registry → buckets (+ KMS) → identities (per
workload, least privilege) → GitHub OIDC trust. State in a remote bucket per
environment (`envs/*.backend.hcl`); per-environment values (`envs/*.tfvars`).
The **bootstrap chicken-and-egg problem:** the state bucket and the CI role are
created once by hand, and everything else goes through `infra.yml`.

**Lab:** `terraform -chdir=infra/aws init -backend=false && terraform -chdir=infra/aws validate`.
Then, using only `iam.tf`, build a table: identity → can read / can write / trusted by.
**Stretch (3 h, real cost, tear down the same day):** DEPLOY.md Level 3 for
staging only; get the smoke test passing over HTTPS; `terraform destroy`.

**Checkpoint:** the identity table. Stretch: smoke test output + destroy output.

---

## Module 9: Observability and operations

**Goal:** she can define SLOs, read burn-rate alerts, detect drift, judge live
accuracy, and follow a runbook.

**Read:** [OPERATIONS.md](OPERATIONS.md), CONCEPTS.md Part G. **Files:** `monitoring/alerts.yml`, Grafana dashboard, `scripts/drift_report.py`, `live_eval.py`, `check_slo.py`.

**Explain:** SLIs/SLOs and error budgets; multi-window burn-rate alerts;
*model-quality* signals (drift via PSI, live recall from feedback) vs
*system* signals (latency, errors); every alert links to a runbook entry;
`alerts.yml` is the single source (CI checks the k8s copy is in sync).

**Lab**
1. `make drift-data`; `python scripts/drift_report.py --csv data/customers_drifted.csv`.
2. Send feedback for 100 customers via the API; run `make live-eval`.
3. Decide whether to retrain, and write one paragraph with evidence.
4. Game day: you break something silently (e.g. scale the API to 0 in kind, or
   stop MinIO in compose). She must detect, diagnose and fix it using only
   dashboards, logs and the runbook, then write the incident note.

**Checkpoint:** the retrain decision and the game-day incident note.

---

## Module 10: Security, end to end

**Goal:** she can walk through the threat model and name the control for each threat.

**Read:** [SECURITY.md](SECURITY.md), CONCEPTS.md Part F.

**Cover in one pass:** API keys and admin separation · pseudonymised IDs ·
secrets from the cloud secret manager (ExternalSecret), never Git · workload
identity per pod · OIDC for CI · non-root, read-only, dropped capabilities
(tested) · NetworkPolicy · hashed lockfile, pip-audit, Trivy, SBOM, cosign
signatures · Dependabot · SHA-pinned actions · gitleaks in pre-commit.

**Lab:** for each of these attacks, name the control and the file that
implements it: stolen API key; malicious PyPI release of a dependency; a
compromised GitHub Action; a developer pushing an AWS key; a tampered model file;
a pod compromised through an app bug.

**Checkpoint:** the six-row table.

---

## Capstone: ship a feature through the whole system

Add a new input feature `num_dependents` end to end:
1. Generator → `config.py` contract → tests (Module 1). Notice how few files change.
2. PR with the template filled in; CI green (Module 6).
3. `cd-kind` green; a new model trained, card reviewed, promoted (Modules 2, 7).
4. Update the smoke test and dashboard if needed (Modules 3, 9).
5. A 10-minute demo to you, walking the ARCHITECTURE.md diagrams: "here's
   what happened to my change at each box".

---

## Assessment rubric

Score each area 1-4 at the end. "3" is the bar for working independently.

| Area | 1 (aware) | 2 (guided) | 3 (independent) | 4 (can teach it) |
|---|---|---|---|---|
| ML for production | knows gates exist | runs training, reads card | changes gates/threshold with justification | designs gates for a new model |
| Serving | calls the API | adds an endpoint with help | adds endpoint + tests + metrics alone | explains every block of `api.py` and its trade-offs |
| Containers | builds image | explains multi-stage | fixes a CVE / lockfile issue | designs the build for a new service |
| Kubernetes | applies an overlay | reads rendered manifests | recovers a broken rollout alone | designs overlays for a new env |
| **CI** | reads a run | fixes a red check | adds a new check with justification | designs a CI pipeline for a new repo |
| **CD** | knows the workflows exist | traces a release on paper | runs deploy + rollback; extends cd-kind | designs release strategy (canary/GitOps/approvals) |
| IaC | runs validate | reads one stack | explains identity model | adapts to a new cloud account |
| Operations | knows dashboards exist | reads alerts | handles a game-day incident | writes runbooks and SLOs |
| Security | lists controls | maps threats to controls | adds a control (e.g. new scanner) | reviews someone else's design |

---

## Question bank (use any time)

- What's the difference between CI, continuous delivery and continuous deployment? Which do we do for prod here, and why?
- Why build the image once? What breaks if each environment builds its own?
- Where do cloud credentials come from in the pipeline? What is stored in GitHub, and what isn't?
- A release is at 50% canary and error rate spikes. Walk me through what the pipeline does, step by step.
- How do you roll back (a) code, (b) a model, (c) infrastructure? Which is fastest?
- Why is `release/kustomization.yaml` machine-managed? What would go wrong if people hand-edited it?
- Why are models not inside the Docker image?
- A test passes locally but fails in CI. Name five possible reasons.
- What would you remove from this repo for a hobby project? For a bank?
- How would the same skeleton serve an LLM instead of a gradient-boosting model?

## Common misconceptions to watch for

- "CI passed, so it works in prod." CI proves the change is *safe to try*;
  canary + monitoring prove it *works*.
- "Rollback = redeploy the old code." For models, it's moving an alias; for
  config, it's `git revert` of the release file; both are faster than a rebuild.
- "More checks = better CI." Each check must prevent a named failure and be
  fast enough that people don't bypass it.
- "Secrets in GitHub secrets are fine for cloud access." Prefer OIDC:
  nothing long-lived to steal.
- "Staging is optional." It's where the *identical* artifact meets real
  infrastructure before users do.

## Appendix A: CI/CD file map

```
.pre-commit-config.yaml        laptop: fast checks before every commit
Makefile  (make ci)            laptop: the PR gate, locally
.github/
  workflows/
    ci.yml                     every PR/push: quality, test, iac, images, e2e
    cd-kind.yml                free CD rehearsal on kind: deploy, release, rollback
    release.yml                code: build once → staging → approval → canary → prod
    model-release.yml          model: card review → approval → canary → move alias
    infra.yml                  terraform: plan on PR, apply on merge
  actions/
    cloud-auth/                OIDC login to AWS or GCP + registry + kubeconfig
    push-images/               push identical archives, cosign sign, SBOM attest
    k8s-tools/                 kustomize + python for release scripts
    canary-gate/               wait, then automated canary analysis
    commit-release/            write release state back to Git
  dependabot.yml               weekly dependency PRs (actions, pip, docker, terraform)
  pull_request_template.md     production checklist on every PR
  CODEOWNERS                   who must review which paths
scripts/release.py             edit release state (tags, models, canary weights)
scripts/deploy.sh              apply + wait + smoke (same for humans and CI)
scripts/canary_analysis.py     canary vs stable judgement from Prometheus
scripts/smoke_test.sh          behavioural smoke test against a live API
```

## Appendix B: suggested weekly schedule

| Week | Sessions | Labs due |
|---|---|---|
| 1 | M0, M1 | setup, M1 |
| 2 | M2, M3 | M2, M3 |
| 3 | M4, M5 (×2) | M4, M5 |
| 4 | M6 (×2), M7 session 1 | M6, cd-kind by hand |
| 5 | M7 session 2, M8, M9, M10 | M7, M8, M9, M10 |
| 6 | capstone demo | capstone + rubric review |
