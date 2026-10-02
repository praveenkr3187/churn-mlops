"""Registry tests, including the S3 backend against a local S3-compatible
server (moto) — no AWS account needed. MinIO in docker-compose works the same
way; GCS works through gcsfs."""
import boto3
import pytest
from moto.server import ThreadedMotoServer

from churn import registry
from churn.features import build_pipeline


@pytest.fixture
def tiny_model():
    from churn import config
    from churn.data import load_data

    df = load_data().head(300)
    return build_pipeline({"max_iter": 5}).fit(df[config.FEATURES], df[config.TARGET])


def _md(version):
    import sklearn

    return {"version": version, "decision_threshold": 0.5, "gates": [], "env": {"sklearn": sklearn.__version__}}


def test_versions_are_immutable(tmp_path, tiny_model):
    registry.save(tiny_model, _md("v1"), "v1", registry=str(tmp_path))
    with pytest.raises(registry.RegistryError, match="immutable"):
        registry.save(tiny_model, _md("v1"), "v1", registry=str(tmp_path))


def test_promote_and_rollback_history(tmp_path, tiny_model):
    reg = str(tmp_path)
    for v in ("v1", "v2"):
        registry.save(tiny_model, _md(v), v, registry=reg)
    registry.promote("v1", "production", registry=reg)
    registry.promote("v2", "production", registry=reg)
    assert registry.resolve("production", reg) == "v2"
    last = registry.history(reg)[-1]
    assert last["previous"] == "v1"
    registry.promote(last["previous"], "production", reason="rollback", registry=reg)
    assert registry.resolve("production", reg) == "v1"


def test_tampered_artifact_is_refused(tmp_path, tiny_model):
    reg = str(tmp_path)
    registry.save(tiny_model, _md("v1"), "v1", registry=reg)
    with open(tmp_path / "versions" / "v1" / "model.joblib", "ab") as f:
        f.write(b"malicious")
    with pytest.raises(registry.RegistryError, match="Checksum"):
        registry.load("v1", reg)


def test_library_version_mismatch_is_refused(tmp_path, tiny_model):
    reg = str(tmp_path)
    registry.save(tiny_model, {**_md("v1"), "env": {"sklearn": "0.24.2"}}, "v1", registry=reg)
    with pytest.raises(registry.RegistryError, match="not portable"):
        registry.load("v1", reg)


@pytest.fixture
def s3_server(monkeypatch):
    server = ThreadedMotoServer(port=5055, verbose=False)
    server.start()
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:5055")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    import s3fs

    s3fs.S3FileSystem.clear_instance_cache()
    yield
    server.stop()


def test_s3_backend(tiny_model, s3_server):
    boto3.client("s3").create_bucket(Bucket="churn-models")
    reg = "s3://churn-models/registry"
    registry.save(tiny_model, _md("v1"), "v1", model_card="# card", registry=reg)
    registry.promote("v1", "production", registry=reg)
    model, md = registry.load("production", reg)
    assert md["version"] == "v1"
    assert registry.list_versions(reg) == ["v1"]
    assert registry.history(reg)[-1]["version"] == "v1"
