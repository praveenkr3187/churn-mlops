"""Policy tests on the RENDERED Kubernetes manifests: the rules we care about
are enforced by CI, not by code review memory. Skipped if kustomize is absent."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ["local", "aws-staging", "aws-prod", "gcp-staging", "gcp-prod"]
KUSTOMIZE = shutil.which("kustomize")
pytestmark = pytest.mark.skipif(not KUSTOMIZE, reason="kustomize not installed")


def render(overlay: str) -> list[dict]:
    out = subprocess.run([KUSTOMIZE, "build", str(ROOT / "k8s" / "overlays" / overlay)],
                         check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def pod_specs(docs):
    for d in docs:
        if d["kind"] in ("Deployment", "Job"):
            yield d, d["spec"]["template"]["spec"]
        elif d["kind"] == "CronJob":
            yield d, d["spec"]["jobTemplate"]["spec"]["template"]["spec"]


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_pods_are_hardened(overlay):
    for d, spec in pod_specs(render(overlay)):
        name = f"{overlay}/{d['kind']}/{d['metadata']['name']}"
        assert spec["securityContext"]["runAsNonRoot"] is True, name
        assert spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault", name
        for c in spec["containers"]:
            sc = c["securityContext"]
            assert sc["allowPrivilegeEscalation"] is False, name
            assert sc["capabilities"]["drop"] == ["ALL"], name
            assert "memory" in c["resources"]["limits"], f"{name}: memory limit"
            assert "cpu" in c["resources"]["requests"], f"{name}: cpu request"
            assert not c["image"].endswith(":latest"), f"{name}: never deploy :latest"


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_api_has_probes_and_readonly_fs(overlay):
    for d, spec in pod_specs(render(overlay)):
        if d["kind"] == "Deployment" and d["metadata"]["name"].startswith("churn-api"):
            c = spec["containers"][0]
            assert {"livenessProbe", "readinessProbe", "startupProbe"} <= set(c), d["metadata"]["name"]
            assert c["securityContext"]["readOnlyRootFilesystem"] is True
            assert spec["automountServiceAccountToken"] is False


@pytest.mark.parametrize("overlay", ["aws-prod", "gcp-prod"])
def test_prod_is_highly_available(overlay):
    docs = render(overlay)
    hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler")
    assert hpa["spec"]["minReplicas"] >= 3
    assert any(d["kind"] == "PodDisruptionBudget" for d in docs)
    dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "churn-api")
    assert dep["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    tsc = dep["spec"]["template"]["spec"]["topologySpreadConstraints"][0]
    assert tsc["whenUnsatisfiable"] == "DoNotSchedule"


@pytest.mark.parametrize("overlay", ["aws-staging", "aws-prod", "gcp-staging", "gcp-prod"])
def test_cloud_overlays_point_at_their_cloud(overlay):
    docs = render(overlay)
    cfg = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].startswith("churn-config"))
    scheme = "s3://" if overlay.startswith("aws") else "gs://"
    assert cfg["data"]["CHURN_MODEL_REGISTRY"].startswith(scheme)
    env = overlay.split("-")[1]
    assert cfg["data"]["CHURN_ENV"] == env
    assert all(d["metadata"].get("namespace") in (None, f"churn-{env}") for d in docs)
    # Secrets come from the cloud secret manager, never from Git.
    assert not any(d["kind"] == "Secret" for d in docs)
    assert any(d["kind"] == "ExternalSecret" for d in docs)


@pytest.mark.parametrize("overlay", OVERLAYS)
def test_namespace_enforces_restricted_pod_security(overlay):
    ns = next(d for d in render(overlay) if d["kind"] == "Namespace")
    assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"


def test_canary_deployment_mirrors_stable():
    base = yaml.safe_load_all((ROOT / "k8s/base/deployment.yaml").read_text())
    canary = yaml.safe_load_all((ROOT / "k8s/components/canary/deployment.yaml").read_text())
    b, c = next(base), next(canary)
    bs, cs = b["spec"]["template"]["spec"], c["spec"]["template"]["spec"]
    for key in ("securityContext", "serviceAccountName", "terminationGracePeriodSeconds", "volumes"):
        assert bs[key] == cs[key], key
    for key in ("ports", "startupProbe", "readinessProbe", "livenessProbe", "securityContext", "lifecycle"):
        assert bs["containers"][0][key] == cs["containers"][0][key], key
