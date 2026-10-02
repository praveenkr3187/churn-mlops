## What & why
<!-- One or two sentences. Link the issue/ticket. -->

## Type of change
- [ ] Code (API / training / scripts) — ships via `release` workflow
- [ ] Model / training config — ships via `model-release` workflow
- [ ] Kubernetes manifests (`k8s/`)
- [ ] Infrastructure (`infra/`) — `infra` workflow posts a `terraform plan`
- [ ] Docs only

## How I tested it
<!-- e.g. `make test`, `make up && make smoke`, `make kind-up kind-smoke` -->

## Production checklist
- [ ] CI is green (lint, security, tests, IaC, image scan, e2e + load test)
- [ ] No secrets, data or model files committed
- [ ] If the API contract changed: backwards compatible, or a new `/v2` path
- [ ] If an alert/SLO changed: edited `monitoring/alerts.yml` and ran `make k8s-alerts`
- [ ] If a dependency changed: ran `make lock`
- [ ] Rollback plan: <!-- usually "revert this PR"; say so if it isn't -->
