.PHONY: help install lock data drift-data train promote test lint security ci serve smoke load \
        drift live-eval up down kind-up kind-smoke kind-down k8s-render k8s-alerts clean

PY ?= python
OVERLAY ?= local

help:           ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

# ---------------------------------------------------------------- dev ------
install:        ## Editable install with every extra
	$(PY) -m pip install -e ".[dev,aws,gcp,tracing,mlflow]"

lock:           ## Re-pin exact, hashed dependencies (requirements.lock)
	uv pip compile pyproject.toml --extra aws --extra gcp --extra tracing --extra mlflow \
	  --python-version 3.11 --python-platform x86_64-manylinux_2_28 --generate-hashes -o requirements.lock

data:           ## Generate 10k synthetic customers -> data/customers.csv
	$(PY) scripts/generate_data.py --rows 10000 --out data/customers.csv

drift-data:     ## Generate a drifted batch (price hike, more month-to-month)
	$(PY) scripts/generate_data.py --rows 2000 --seed 99 --drift --out data/customers_drifted.csv

train:          ## Train + gates + register as 'staging'
	$(PY) -m churn.train --alias staging

promote:        ## Promote the staging model to production (locally: you are the approver)
	$(PY) -m churn.promote --from-alias staging --alias production --reason "local"

test:           ## All tests (unit, model, API, registry/S3, release tooling, k8s policy)
	$(PY) -m pytest

lint:           ## Ruff + alert-rule sync check
	ruff check src tests scripts locustfile.py
	$(PY) scripts/sync_alerts.py --check

security:       ## Bandit + pip-audit (+ checkov/hadolint if installed)
	bandit -q -r src -c pyproject.toml
	pip-audit --strict -r requirements.lock --require-hashes --disable-pip
	-checkov -d infra --framework terraform --quiet --compact
	-hadolint Dockerfile

ci:             ## Run the fast CI checks locally (lint + security + tests), same as the PR gate
	$(MAKE) lint
	bandit -q -r src -c pyproject.toml
	pip-audit --strict -r requirements.lock --require-hashes --disable-pip
	$(PY) -m pytest

# -------------------------------------------------------------- serve ------
serve:          ## Run the API locally (API key: dev-key, admin: dev-admin-key)
	mkdir -p logs
	CHURN_API_KEYS=dev-key CHURN_ADMIN_API_KEYS=dev-admin-key \
	CHURN_PREDICTION_LOG=logs/predictions.jsonl OMP_NUM_THREADS=1 \
	  uvicorn churn.api:app --reload --port 8000

smoke:          ## Smoke-test a running API (URL=..., KEY=...)
	bash scripts/smoke_test.sh $${URL:-http://localhost:8000} $${KEY:-dev-key}

load:           ## Load test at expected load + SLO check (run locust on ANOTHER machine for real numbers)
	mkdir -p reports
	locust -f locustfile.py --host $${URL:-http://localhost:8000} --headless -u 20 -r 5 -t 2m --csv reports/load
	$(PY) scripts/check_slo.py reports/load_stats.csv

drift:          ## PSI drift report on logged traffic
	$(PY) scripts/drift_report.py --events logs/predictions.jsonl

live-eval:      ## Live accuracy from predictions joined with feedback
	$(PY) scripts/live_eval.py --events logs/predictions.jsonl

# ------------------------------------------------- docker compose stack ----
up:             ## MinIO + MLflow + train + API + Prometheus + Grafana
	docker compose up -d --build --wait api mlflow prometheus grafana

down:
	docker compose down -v

# ------------------------------------------------------- local k8s ---------
kind-up:        ## kind cluster + images + full app layer + first model
	kind create cluster --name churn || true
	docker build --target serve -t churn-api:dev .
	docker build --target train -t churn-train:dev .
	kind load docker-image churn-api:dev churn-train:dev --name churn
	kubectl apply -k k8s/overlays/local
	kubectl -n churn-local wait --for=condition=complete job/churn-bootstrap --timeout=600s
	kubectl -n churn-local rollout restart deploy/churn-api
	kubectl -n churn-local rollout status deploy/churn-api --timeout=300s

kind-smoke:     ## Port-forward and smoke-test the kind deployment
	kubectl -n churn-local port-forward svc/churn-api-stable 18080:80 & \
	  PF=$$!; sleep 3; bash scripts/smoke_test.sh http://localhost:18080 dev-key; kill $$PF

kind-down:
	kind delete cluster --name churn

k8s-render:     ## Render an overlay (OVERLAY=aws-prod)
	kustomize build k8s/overlays/$(OVERLAY)

k8s-alerts:     ## Regenerate k8s PrometheusRule from monitoring/alerts.yml
	$(PY) scripts/sync_alerts.py

clean:
	rm -rf models reports logs data/*.csv .pytest_cache .ruff_cache src/*.egg-info
