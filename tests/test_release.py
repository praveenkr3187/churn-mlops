"""Tests for the deployment tooling: release state machine, canary judgement,
alert-rule sync, drift maths."""
import shutil

import canary_analysis
import drift_report
import numpy as np
import pytest
import release
import sync_alerts


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    """A throwaway copy of the aws-prod overlay's release state."""
    src = release.K8S / "aws-prod" / "release"
    dst = tmp_path / "aws-prod" / "release"
    shutil.copytree(src, dst)
    monkeypatch.setattr(release, "K8S", tmp_path)
    release.main(["set-stable", "--overlay", "aws-prod", "--tag", "sha-old", "--model", "v1"])
    return "aws-prod"


def test_canary_lifecycle_promote(overlay):
    release.main(["canary-start", "--overlay", overlay, "--tag", "sha-new", "--model", "v2", "--weight", "10"])
    s = release.load(overlay)
    assert s["canary"] == {"tag": "sha-new", "model": "v2", "weight": 10}
    assert s["stable"]["model"] == "v1"

    release.main(["canary-weight", "--overlay", overlay, "--weight", "50"])
    assert release.load(overlay)["canary"]["weight"] == 50

    release.main(["canary-promote", "--overlay", overlay])
    s = release.load(overlay)
    assert s["stable"] == {"tag": "sha-new", "model": "v2"}
    assert s["canary"]["weight"] == 0


def test_canary_abort_restores_stable(overlay):
    release.main(["canary-start", "--overlay", overlay, "--model", "v2"])
    release.main(["canary-abort", "--overlay", overlay])
    s = release.load(overlay)
    assert s["stable"]["model"] == "v1"
    assert s["canary"] == {"tag": "sha-old", "model": "v1", "weight": 0}


def test_cannot_start_two_canaries(overlay):
    release.main(["canary-start", "--overlay", overlay, "--model", "v2"])
    with pytest.raises(SystemExit):
        release.main(["canary-start", "--overlay", overlay, "--model", "v3"])


def test_release_file_is_valid_kustomize_component(overlay):
    import yaml

    release.main(["canary-start", "--overlay", overlay, "--model", "v2", "--weight", "25"])
    doc = yaml.safe_load(release._path(overlay).read_text())
    patch = yaml.safe_load(doc["patches"][0]["patch"])
    weights = {b["name"]: b["weight"] for b in patch[0]["value"]}
    assert weights == {"churn-api-stable": 75, "churn-api-canary": 25}
    assert {i["name"] for i in doc["images"]} == {"churn-api", "churn-api-canary", "churn-train"}


H = {"requests": 1000, "error_ratio": 0.001, "p95_s": 0.05, "mean_score": 0.40}


@pytest.mark.parametrize(
    "canary, code",
    [
        (H, 0),
        ({**H, "requests": 10}, 2),
        ({**H, "error_ratio": 0.02}, 1),
        ({**H, "p95_s": 0.2}, 1),
        ({**H, "mean_score": 0.7}, 1),
        ({**H, "p95_s": None, "mean_score": None}, 0),
    ],
)
def test_canary_judgement(canary, code):
    assert canary_analysis.judge(H, canary)[0] == code


def test_alert_rules_in_sync():
    assert (sync_alerts.DST.read_text() == sync_alerts.render()), "run `make k8s-alerts`"


def test_psi_detects_shift():
    same = np.array([0.25, 0.25, 0.25, 0.25])
    assert drift_report.psi(same, same) == pytest.approx(0)
    assert drift_report.psi(same, np.array([0.7, 0.1, 0.1, 0.1])) > 0.25


def test_drift_report_flags_drifted_batch(trained):
    from churn import registry
    from generate_data import generate

    ref = registry.get_metadata(trained["version"])["training_stats"]
    normal = drift_report.report(generate(2000, seed=1), ref)
    drifted = drift_report.report(generate(2000, seed=1, drift=True), ref)
    assert max(normal.values()) < 0.10
    assert max(drifted.values()) > 0.25
