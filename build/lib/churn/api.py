"""REST serving layer (FastAPI).

Run locally:  make serve        ->  http://localhost:8000/docs

Production concerns, one per section below:
  * config & fail-closed security  (API keys required outside local)
  * model loaded ONCE at startup, pinned by version, integrity-checked
  * liveness (/health) vs readiness (/ready) probes for Kubernetes
  * input contract via Pydantic (bad input -> 422, never a 500)
  * versioned routes (/v1) so clients survive breaking changes
  * request IDs, JSON logs, Prometheus metrics, optional OpenTelemetry
  * prediction + feedback events with pseudonymised IDs (-> monitoring)
  * payload-size limit and security headers

There is deliberately NO endpoint to swap the model at runtime. In
Kubernetes every pod must serve the same, declared version; changing the
model = changing CHURN_MODEL_VERSION in Git -> rolling/canary deploy.
Rollback = `kubectl rollout undo` or reverting that commit.
"""
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Security
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from churn import __version__, config, registry
from churn import observability as obs
from churn.predict import ChurnPredictor
from churn.schemas import BatchRequest, BatchResponse, Customer, Feedback, Prediction

obs.setup_logging()
log = logging.getLogger("churn.api")
pred_log = logging.getLogger("churn.predictions")

MAX_BODY_BYTES = 1_000_000
STATE: dict = {"predictor": None}


# ---------------------------------------------------------------- security --
if config.ENV != "local" and not config.API_KEYS:
    # Fail closed: never run unauthenticated in a shared environment.
    raise RuntimeError("CHURN_API_KEYS must be set when CHURN_ENV != local")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _key_ok(key: str | None, valid: set[str]) -> bool:
    # compare_digest: constant-time comparison, no timing side channel
    return bool(key) and any(hmac.compare_digest(key, k) for k in valid)


def require_api_key(key: str | None = Security(api_key_header)) -> None:
    if not config.API_KEYS:  # local dev only (guarded above)
        return
    if not _key_ok(key, config.API_KEYS | config.ADMIN_API_KEYS):
        raise HTTPException(401, "Invalid or missing API key", headers={"WWW-Authenticate": "API-Key"})


def require_admin_key(key: str | None = Security(api_key_header)) -> None:
    if not config.ADMIN_API_KEYS:
        raise HTTPException(403, "Admin API disabled")
    if not _key_ok(key, config.ADMIN_API_KEYS):
        raise HTTPException(403, "Admin key required")


# ----------------------------------------------------------------- startup --
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        p = ChurnPredictor()
        STATE["predictor"] = p
        obs.MODEL_LOADED.set(1)
        obs.MODEL_INFO.labels(p.version).set(1)
        obs.log_event(log, "model_loaded", model_version=p.version, env=config.ENV)
    except Exception as exc:  # start anyway: /health answers, /ready says not-ready
        obs.MODEL_LOADED.set(0)
        log.exception("model_load_failed: %s", exc)
    yield
    obs.log_event(log, "shutdown")  # uvicorn drains in-flight requests on SIGTERM


app = FastAPI(
    title="Churn Prediction API",
    version=__version__,
    description="Production ML deployment reference",
    lifespan=lifespan,
    # Hide interactive docs in prod; the OpenAPI contract lives in the repo.
    docs_url=None if config.ENV == "prod" else "/docs",
    redoc_url=None,
)
obs.setup_tracing(app)


@app.middleware("http")
async def middleware(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Payload too large"}, status_code=413)

    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
    finally:
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        # Use the route TEMPLATE, not the raw path, to keep metric cardinality bounded.
        route_name = getattr(route, "path", "unmatched")
        obs.REQUESTS.labels(request.method, route_name, str(status)).inc()
        obs.LATENCY.labels(route_name).observe(elapsed)
        if route_name not in ("/health", "/ready", "/metrics"):
            obs.log_event(
                log, "request", request_id=request_id, method=request.method,
                route=route_name, status=status, latency_ms=round(elapsed * 1000, 2),
            )
    response.headers["x-request-id"] = request_id
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["cache-control"] = "no-store"
    return response


def predictor() -> ChurnPredictor:
    if STATE["predictor"] is None:
        raise HTTPException(503, "Model not loaded")
    return STATE["predictor"]


def _emit(request: Request, customers: list[Customer], preds: list[dict]) -> None:
    """One event per prediction. Raw customer_id never leaves the process."""
    sink = open(config.PREDICTION_LOG, "a") if config.PREDICTION_LOG else None  # noqa: SIM115
    try:
        for c, p in zip(customers, preds, strict=True):
            features = c.model_dump(exclude={"customer_id"})
            event = {
                "event": "prediction",
                "request_id": request.state.request_id,
                "customer_hash": obs.pseudonymise(c.customer_id),
                "features": features,
                "churn_probability": p["churn_probability"],
                "will_churn": bool(p["will_churn"]),
                "model_version": p["model_version"],
            }
            obs.log_event(pred_log, "prediction", **event)
            if sink:
                sink.write(json.dumps(event, default=str) + "\n")
            obs.PREDICTIONS.labels(p["model_version"], p["risk_band"]).inc()
            obs.SCORE.labels(p["model_version"]).observe(p["churn_probability"])
            for f in ("tenure_months", "monthly_charges", "num_support_tickets"):
                obs.FEATURE_VALUE.labels(f).observe(features[f])
    finally:
        if sink:
            sink.close()


# ------------------------------------------------------------------ probes --
@app.get("/health", tags=["ops"], include_in_schema=False)
def health():
    """Liveness: the process is up. Never checks dependencies (avoid restart storms)."""
    return {"status": "ok"}


@app.get("/ready", tags=["ops"], include_in_schema=False)
def ready():
    """Readiness: can serve predictions. Failing = removed from load balancer, not killed."""
    if STATE["predictor"] is None:
        raise HTTPException(503, "Model not loaded")
    return {"status": "ready", "model_version": STATE["predictor"].version}


@app.get("/metrics", include_in_schema=False)
def metrics():
    # Not routed by the Ingress; only reachable in-cluster by Prometheus.
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# -------------------------------------------------------------------- v1 API --
v1 = [Depends(require_api_key)]


@app.get("/v1/model-info", tags=["model"], dependencies=v1)
def model_info():
    md = predictor().metadata
    keys = ("version", "trained_at", "model_type", "metrics", "decision_threshold", "data", "env")
    return {k: md.get(k) for k in keys}


@app.post("/v1/predict", response_model=Prediction, tags=["model"], dependencies=v1)
def predict(customer: Customer, request: Request):
    pred = predictor().predict_records([customer.model_dump()])[0]
    _emit(request, [customer], [pred])
    return pred


@app.post("/v1/predict/batch", response_model=BatchResponse, tags=["model"], dependencies=v1)
def predict_batch(req: BatchRequest, request: Request):
    p = predictor()
    preds = p.predict_records([c.model_dump() for c in req.customers])
    _emit(request, req.customers, preds)
    return {"predictions": preds, "model_version": p.version}


@app.post("/v1/feedback", status_code=202, tags=["model"], dependencies=v1)
def feedback(fb: Feedback, request: Request):
    """Ground truth, joined later with prediction events to measure live accuracy."""
    event = {"event": "feedback", "request_id": request.state.request_id,
             "customer_hash": obs.pseudonymise(fb.customer_id), "churned": fb.churned}
    obs.log_event(pred_log, "feedback", **event)
    if config.PREDICTION_LOG:
        with open(config.PREDICTION_LOG, "a") as f:
            f.write(json.dumps(event) + "\n")
    obs.FEEDBACK.labels(str(fb.churned).lower()).inc()
    return {"accepted": True}


@app.get("/v1/admin/registry", tags=["admin"], dependencies=[Depends(require_admin_key)])
def admin_registry():
    """Read-only view of the registry (for operators)."""
    return {
        "serving": predictor().version,
        "production": registry.current("production"),
        "staging": registry.current("staging"),
        "versions": registry.list_versions(),
        "history": registry.history()[-10:],
    }
