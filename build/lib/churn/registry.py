"""Model registry on plain object storage — works on a laptop, S3, GCS or MinIO.

    <registry>/
      versions/v20260927-101500/
        model.joblib      the full pipeline (preprocessing + model)
        metadata.json     metrics, params, data fingerprint, sha256, lib versions
        model_card.md     human-readable summary for reviewers
      aliases/
        production.json   {"version": ..., "promoted_at": ..., "promoted_by": ...}
        staging.json
      history.jsonl       audit trail of every promotion / rollback

Same concepts as MLflow / SageMaker / Vertex AI Model Registry:
immutable versions + movable aliases + an audit log. Because it's just
files, you can swap it for MLflow later without touching api.py.

Security: joblib/pickle executes code on load. We only load artifacts whose
SHA-256 matches the metadata written at training time, and the bucket is
write-restricted to the training job's identity (see infra/).
"""
import getpass
import hashlib
import io
import json
import os
from datetime import UTC, datetime

import fsspec
import joblib
import sklearn

from churn import config

ALIASES = ("production", "staging")


class RegistryError(RuntimeError):
    pass


def _fs_and_root(registry: str | None):
    uri = str(registry or config.MODEL_REGISTRY)
    fs, root = fsspec.core.url_to_fs(uri)
    return fs, root.rstrip("/")


def _read_json(fs, path):
    with fs.open(path, "r") as f:
        return json.load(f)


def _write_json(fs, path, obj):
    with fs.open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)


def new_version() -> str:
    return datetime.now(UTC).strftime("v%Y%m%d-%H%M%S")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(model, metadata: dict, version: str, model_card: str = "", registry: str | None = None) -> str:
    fs, root = _fs_and_root(registry)
    path = f"{root}/versions/{version}"
    if fs.exists(f"{path}/model.joblib"):
        raise RegistryError(f"{version} already exists — versions are immutable")
    fs.makedirs(path, exist_ok=True)

    buf = io.BytesIO()
    joblib.dump(model, buf)
    blob = buf.getvalue()
    metadata = {**metadata, "artifact_sha256": sha256(blob), "artifact_bytes": len(blob)}

    with fs.open(f"{path}/model.joblib", "wb") as f:
        f.write(blob)
    _write_json(fs, f"{path}/metadata.json", metadata)
    if model_card:
        with fs.open(f"{path}/model_card.md", "w") as f:
            f.write(model_card)
    return path


def list_versions(registry: str | None = None) -> list[str]:
    fs, root = _fs_and_root(registry)
    if not fs.exists(f"{root}/versions"):
        return []
    names = [p.rstrip("/").split("/")[-1] for p in fs.ls(f"{root}/versions", detail=False)]
    return sorted(n for n in names if fs.exists(f"{root}/versions/{n}/model.joblib"))


def get_metadata(version: str, registry: str | None = None) -> dict:
    fs, root = _fs_and_root(registry)
    return _read_json(fs, f"{root}/versions/{resolve(version, registry)}/metadata.json")


def resolve(version: str = "production", registry: str | None = None) -> str:
    """Turn an alias ('production', 'staging') into a concrete version."""
    if version not in ALIASES:
        return version
    fs, root = _fs_and_root(registry)
    alias_path = f"{root}/aliases/{version}.json"
    if not fs.exists(alias_path):
        raise FileNotFoundError(
            f"No '{version}' model in {root}. Train one and promote it "
            f"(`python -m churn.promote <version> --alias {version}`)."
        )
    return _read_json(fs, alias_path)["version"]


def current(alias: str = "production", registry: str | None = None) -> str | None:
    try:
        return resolve(alias, registry)
    except FileNotFoundError:
        return None


def promote(version: str, alias: str = "production", reason: str = "", registry: str | None = None) -> dict:
    """Point an alias at a version. Rolling back = promoting an older version."""
    if alias not in ALIASES:
        raise RegistryError(f"alias must be one of {ALIASES}")
    fs, root = _fs_and_root(registry)
    if not fs.exists(f"{root}/versions/{version}/model.joblib"):
        raise FileNotFoundError(f"Version {version} not found in {root}")
    record = {
        "alias": alias,
        "version": version,
        "previous": current(alias, registry),
        "promoted_at": datetime.now(UTC).isoformat(),
        "promoted_by": os.getenv("GITHUB_ACTOR") or getpass.getuser(),
        "reason": reason,
    }
    fs.makedirs(f"{root}/aliases", exist_ok=True)
    _write_json(fs, f"{root}/aliases/{alias}.json", record)
    # Object stores can't append, so read-modify-write the audit log.
    hist = f"{root}/history.jsonl"
    old = fs.cat_file(hist).decode() if fs.exists(hist) else ""
    with fs.open(hist, "w") as f:
        f.write(old + json.dumps(record) + "\n")
    return record


def history(registry: str | None = None) -> list[dict]:
    fs, root = _fs_and_root(registry)
    hist = f"{root}/history.jsonl"
    if not fs.exists(hist):
        return []
    return [json.loads(line) for line in fs.cat_file(hist).decode().splitlines() if line]


def load(version: str = "production", registry: str | None = None, strict_env: bool = True):
    """Load a model, verifying integrity and library compatibility first."""
    fs, root = _fs_and_root(registry)
    version = resolve(version, registry)
    path = f"{root}/versions/{version}"
    metadata = _read_json(fs, f"{path}/metadata.json")
    blob = fs.cat_file(f"{path}/model.joblib")

    if sha256(blob) != metadata.get("artifact_sha256"):
        raise RegistryError(f"Checksum mismatch for {version} — refusing to load (tampered?)")

    trained_with = metadata.get("env", {}).get("sklearn", "")
    if strict_env and trained_with.split(".")[:2] != sklearn.__version__.split(".")[:2]:
        raise RegistryError(
            f"{version} was trained with scikit-learn {trained_with} but runtime has "
            f"{sklearn.__version__}. Pickles are not portable across versions — retrain "
            "or pin the same version (see requirements.lock)."
        )
    # Safe to unpickle: SHA-256 verified above against metadata from the training job.
    model = joblib.load(io.BytesIO(blob))  # nosec B301
    return model, metadata
