# Deploying: laptop → kind → AWS or GCP

Three levels. Do them in order; each builds on the last.

| Level | Needs | Time | What you learn |
|---|---|---|---|
| 1. Local Python | Python 3.11 | 5 min | Train, gate, register, serve, test |
| 2. Local stack | Docker | 10 min | Containers, object storage, Prometheus/Grafana, **real Kubernetes (kind)** |
| 3. Cloud | AWS or GCP account, GitHub repo | 2-3 h first time | IaC, identity, CI/CD, canary, operations |

---

## Level 1: local Python

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
make install
make data train promote          # data -> gated model (staging) -> production
make test                        # 79 tests (k8s policy tests need kustomize on PATH)
make serve                       # http://localhost:8000/docs  (X-API-Key: dev-key)
make smoke                       # in a 2nd terminal
make drift-data && python scripts/drift_report.py --csv data/customers_drifted.csv
```

## Level 2a: docker compose (production-like on one machine)

Needs a Docker engine. On macOS without Docker Desktop, Colima provides one:
`brew install colima docker-compose docker-buildx`, then
`colima start --cpu 4 --memory 8 --disk 40` (later just `colima start` / `colima stop`).

```bash
make up          # MinIO (S3), seed data, MLflow, train+promote, API, Prometheus, Grafana
make smoke URL=http://localhost:8000 KEY=dev-key
make load        # better: run locust from ANOTHER machine
```
Consoles: API docs <http://localhost:8000/docs> · MinIO <http://localhost:9001>
(minioadmin/minioadmin) · MLflow <http://localhost:5001> (every training run,
including ones that failed their gates) · Prometheus <http://localhost:9090>
(Status → Rules shows the alerts) · Grafana <http://localhost:3000>
(admin/admin → ML → Churn API).

MinIO's own images are no longer published, so the stack uses `pgsty/minio`, a
maintained community build of the same server and console.

## Level 2b: Kubernetes on your laptop (kind)

Needs `kind`, `kubectl`, `kustomize`, Docker.
```bash
make kind-up        # cluster, build+load images, apply k8s/overlays/local, bootstrap model
make kind-smoke
kubectl -n churn-local get all,networkpolicy,cronjob
kubectl -n churn-local create job --from=cronjob/churn-retrain retrain-now   # try a retrain
make kind-down
```
This runs the **same base manifests** as production: NetworkPolicies, Pod
Security `restricted`, probes, HPA and CronJobs. It drops only the Gateway,
External Secrets and Prometheus Operator objects.

---

## Level 3: cloud

Pick **one** cloud. Placeholders to replace everywhere: `acme` (bucket
prefix), `acme/churn-mlops` (GitHub repo), `123456789012` / `acme-churn-*`
(account / project), `example.com` (domain), region (`ap-south-1` /
`asia-south1`).

### Step 0: tools
`terraform >= 1.9`, `kubectl`, `kustomize`, `helm`, `aws` CLI **or**
`gcloud`, `gh` (GitHub CLI). One cloud account/project per environment
(staging, prod) is strongly recommended.

### Step 1: bootstrap (once, by hand: the chicken-and-egg part)
Terraform needs somewhere to keep state, and CI needs a role to run
Terraform.

AWS (in each account):
```bash
aws s3api create-bucket --bucket acme-terraform-state-prod --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1
aws s3api put-bucket-versioning --bucket acme-terraform-state-prod --versioning-configuration Status=Enabled
# + an IAM role for infra CI (TF_ROLE_ARN) trusted by GitHub OIDC for environment infra-prod(-plan)
```
GCP (in each project):
```bash
gcloud storage buckets create gs://acme-terraform-state-prod --location=asia-south1 --uniform-bucket-level-access
gcloud storage buckets update gs://acme-terraform-state-prod --versioning
# + a service account for infra CI (TF_SERVICE_ACCOUNT) with WIF binding
```

### Step 2: infrastructure (Terraform)
Edit `infra/<cloud>/envs/<env>.tfvars`, then:
```bash
cd infra/aws            # or infra/gcp
terraform init -backend-config=envs/staging.backend.hcl
terraform plan  -var-file=envs/staging.tfvars
terraform apply -var-file=envs/staging.tfvars
terraform output        # keep these: registry, buckets, deployer role/SA, kubeconfig command
```
EKS takes ~15 min, GKE Autopilot ~10 min. Repeat for `prod`.

### Step 3: cluster add-ons + platform layer
```bash
$(terraform output -raw kubeconfig_command)
# edit k8s/platform/aws/secretstore.yaml (region) or gcp/secretstore.yaml (projectID),
#      k8s/platform/gcp/envoyproxy.yaml (loadBalancerIP = terraform output gateway_ip),
#      k8s/platform/common/gateway.yaml (hostname, ACME email)
CLOUD=aws CLUSTER=churn-staging ./infra/bootstrap/install-addons.sh
```
Installs Envoy Gateway, cert-manager, External Secrets, kube-prometheus-stack
(with the churn dashboard), the OTel Collector and (AWS) the Load Balancer
Controller, then applies the Gateway, TLS issuer and secret store.

### Step 4: application secret
Generate strong values; never commit them.
```bash
API_KEY=$(openssl rand -hex 24); ADMIN_KEY=$(openssl rand -hex 24); SALT=$(openssl rand -hex 32)
SECRET=$(printf '{"api_keys":"%s","admin_api_keys":"%s","pii_salt":"%s"}' $API_KEY $ADMIN_KEY $SALT)

aws secretsmanager put-secret-value --secret-id churn-api --secret-string "$SECRET"         # AWS
printf '%s' "$SECRET" | gcloud secrets versions add churn-api --data-file=-                  # GCP
```
Store `API_KEY` in your password manager. It's also the `SMOKE_API_KEY`
GitHub secret, and you'll hand it to the API client.

### Step 5: point the overlays at your infrastructure
In `k8s/overlays/<cloud>-<env>/kustomization.yaml`: bucket URIs (`terraform
output models_bucket / data_bucket`) and hostname.
Set your registry once: in `k8s/overlays/<cloud>-<env>/release/kustomization.yaml`,
replace the `newName` values with your ECR / Artifact Registry URL
(`terraform output`). From then on, only `scripts/release.py` edits that file.

### Step 6: DNS
Create an A/ALIAS record `churn(-staging).example.com` → the Gateway's
load balancer (`kubectl -n gateway-system get svc`). cert-manager then issues
the TLS certificate automatically (a few minutes).

### Step 7: GitHub environments
```bash
gh api -X PUT repos/acme/churn-mlops/environments/staging
gh api -X PUT repos/acme/churn-mlops/environments/prod        # add required reviewers in the UI
gh api -X PUT repos/acme/churn-mlops/environments/prod-review
```
Variables per environment (from `terraform output`):

| Variable | AWS example | GCP example |
|---|---|---|
| `CLOUD` | `aws` | `gcp` |
| `OVERLAY` | `aws-prod` | `gcp-prod` |
| `IMAGE_REGISTRY` | `123456789012.dkr.ecr.ap-south-1.amazonaws.com` | `asia-south1-docker.pkg.dev/acme-churn-prod/churn` |
| `MODEL_REGISTRY_URI` | `s3://acme-churn-prod-models/registry` | `gs://acme-churn-prod-models/registry` |
| `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN`, `EKS_CLUSTER` | from outputs | — |
| `GCP_PROJECT`, `GCP_REGION`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SERVICE_ACCOUNT`, `GKE_CLUSTER` | — | from outputs |

Secret: `SMOKE_API_KEY`. Branch protection on `main`: require the `ci`
checks.

### Step 8: first model (one-time bootstrap)
The API won't become ready until a `production` model exists.
```bash
aws s3 cp data/customers.csv s3://acme-churn-staging-data/customers.csv    # or gcloud storage cp ... gs://
kubectl -n churn-staging create job --from=cronjob/churn-retrain first-train
kubectl -n churn-staging logs -f job/first-train          # watch the gates
CHURN_MODEL_REGISTRY=s3://acme-churn-staging-models/registry \
  python -m churn.promote --from-alias staging --alias production --reason "bootstrap"
```

### Step 9: first release
Push to `main`. The `release` workflow builds, scans, signs and deploys to
staging, then waits for approval, then runs the prod canary. Watch it in the
Actions tab, and watch the canary on the Grafana dashboard.

### Step 10: verify
```bash
SKIP_PROBES=1 bash scripts/smoke_test.sh https://churn-staging.example.com $API_KEY
kubectl -n churn-staging get pods,hpa,httproute
```

---

## Cost notes (rough, per environment, on-demand, 2026 list prices vary)

| Item | AWS | GCP |
|---|---|---|
| Control plane | EKS ~$73/month | Autopilot cluster fee ~$73/month (first zonal/Autopilot cluster may be free-tier credited) |
| Compute | 3× m7i.large ~ $250/month (Spot in staging: much less) | Pay per pod request (~$60-150/month for this app + add-ons) |
| NAT | ~$35/month per gateway + data | Cloud NAT ~$35/month + data |
| Load balancer | NLB ~$20/month | ~$20/month |
| Storage, registry, secrets | a few $ | a few $ |

Tear down staging when not in use: `terraform destroy -var-file=envs/staging.tfvars`.
