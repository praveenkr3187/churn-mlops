# syntax=docker/dockerfile:1.7
# One Dockerfile, two images (build targets):
#   docker build --target serve -t churn-api .      # the API
#   docker build --target train -t churn-train .    # the training / retraining job
#
# Neither image contains a model or data. The API downloads a pinned,
# checksum-verified model version from the registry (S3 / GCS / MinIO) at
# startup, so model releases don't need code releases, and vice versa.
#
# In CI, pin the base image by digest (python:3.11-slim@sha256:...) and let
# Renovate/Dependabot bump it, so builds are reproducible.

# checkov:skip=CKV_DOCKER_7:Base image is pinned via the PYTHON_IMAGE build arg (checkov cannot resolve ARGs)
ARG PYTHON_IMAGE=python:3.11.13-slim-bookworm

# ---------- deps: install exact, hash-verified dependencies ----------------
FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.lock /tmp/requirements.lock
RUN pip install --require-hashes --no-deps -r /tmp/requirements.lock

COPY pyproject.toml README.md /src/
COPY src /src/src
RUN pip install --no-deps /src

# ---------- runtime base: non-root, no build tools --------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHURN_HOME=/app \
    CHURN_REPORT_DIR=/tmp/reports
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
USER 10001:10001

# ---------- training job -----------------------------------------------------
FROM runtime AS train
COPY --chown=10001:10001 scripts /app/scripts
# Registry + data URIs come from env (see k8s/base/cronjob-retrain.yaml).
ENTRYPOINT ["python", "-m", "churn.train"]
CMD ["--alias", "staging"]

# ---------- serving ----------------------------------------------------------
FROM runtime AS serve
# scikit-learn's gradient boosting uses OpenMP threads per predict() call. With
# many concurrent single-row requests those thread pools fight over the CPU
# and p50 latency went from 19ms to 340ms under load. One thread per request
# fixed it (found with the load test — see docs/OPERATIONS.md#capacity).
ENV OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1
EXPOSE 8000
# Kubernetes uses its own probes; this HEALTHCHECK is for docker/compose.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).status==200 else 1)"]
# ONE worker per container: scale with more pods (HPA), not more processes.
# Keeps memory predictable and Prometheus metrics correct per pod.
CMD ["uvicorn", "churn.api:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--timeout-graceful-shutdown", "20", "--no-server-header", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
