#!/usr/bin/env bash
# Install the cluster add-ons the app layer depends on. Run once per cluster,
# after `terraform apply` and after your kubeconfig points at the cluster.
#
#   CLOUD=aws CLUSTER=churn-prod ./infra/bootstrap/install-addons.sh
#   CLOUD=gcp CLUSTER=churn-prod ./infra/bootstrap/install-addons.sh
#
# Versions are pinned so every cluster is identical. Bump them deliberately
# (Renovate can open PRs for chart versions) and test in staging first.
set -euo pipefail

CLOUD="${CLOUD:?set CLOUD=aws|gcp}"
CLUSTER="${CLUSTER:?set CLUSTER=<cluster name>}"

ENVOY_GATEWAY_VERSION="v1.5.0"          # bundles the Gateway API CRDs
CERT_MANAGER_VERSION="v1.18.2"
EXTERNAL_SECRETS_VERSION="0.19.2"       # serves the external-secrets.io/v1 API
KUBE_PROM_STACK_VERSION="77.5.0"
OTEL_COLLECTOR_VERSION="0.131.0"
AWS_LBC_VERSION="1.13.4"

here="$(cd "$(dirname "$0")" && pwd)"

helm repo add jetstack https://charts.jetstack.io --force-update
helm repo add external-secrets https://charts.external-secrets.io --force-update
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts --force-update

echo "==> Envoy Gateway (Gateway API implementation)"
helm upgrade --install eg oci://docker.io/envoyproxy/gateway-helm \
  --version "$ENVOY_GATEWAY_VERSION" -n envoy-gateway-system --create-namespace --wait

echo "==> cert-manager (TLS certificates, with Gateway API support)"
helm upgrade --install cert-manager jetstack/cert-manager \
  --version "$CERT_MANAGER_VERSION" -n cert-manager --create-namespace --wait \
  --set crds.enabled=true --set config.enableGatewayAPI=true \
  --set config.apiVersion=controller.config.cert-manager.io/v1alpha1 \
  --set config.kind=ControllerConfiguration

echo "==> External Secrets Operator (cloud secret manager -> k8s Secret)"
helm upgrade --install external-secrets external-secrets/external-secrets \
  --version "$EXTERNAL_SECRETS_VERSION" -n external-secrets --create-namespace --wait \
  --set serviceAccount.name=external-secrets

echo "==> kube-prometheus-stack (Prometheus, Alertmanager, Grafana, kube-state-metrics)"
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --version "$KUBE_PROM_STACK_VERSION" -n monitoring --create-namespace --wait \
  -f "$here/prometheus-values.yaml"
kubectl -n monitoring create configmap churn-dashboard \
  --from-file="$here/../../monitoring/grafana/dashboards/churn-api.json" \
  --dry-run=client -o yaml | kubectl label --local -f - grafana_dashboard=1 -o yaml | kubectl apply -f -

echo "==> OpenTelemetry Collector (traces -> X-Ray / Cloud Trace)"
helm upgrade --install otel-collector open-telemetry/opentelemetry-collector \
  --version "$OTEL_COLLECTOR_VERSION" -n observability --create-namespace --wait \
  -f "$here/otel-values-$CLOUD.yaml"

if [[ "$CLOUD" == "aws" ]]; then
  echo "==> AWS Load Balancer Controller (provisions the NLB for the Gateway)"
  helm repo add eks https://aws.github.io/eks-charts --force-update
  helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
    --version "$AWS_LBC_VERSION" -n kube-system --wait \
    --set clusterName="$CLUSTER" --set serviceAccount.name=aws-load-balancer-controller
  # metrics-server (for the HPA) and the Pod Identity agent are EKS add-ons in Terraform.
fi
# GKE Autopilot ships metrics-server, Dataplane V2 (NetworkPolicy) and Workload Identity.

echo "==> Platform layer (Gateway, TLS issuer, secret store)"
kubectl apply -k "$here/../../k8s/platform/$CLOUD"

echo "Done. Next: create the app secret in your cloud secret manager (docs/DEPLOY.md, step 4)."
