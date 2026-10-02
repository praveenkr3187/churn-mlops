"""Logging, metrics and tracing — all vendor-neutral.

* Logs    -> JSON on stdout. The platform ships them: Fluent Bit -> CloudWatch
             (AWS) or Cloud Logging (GCP, automatic on GKE). Never log to local
             files in a container: they die with the pod.
* Metrics -> Prometheus format on /metrics. Scraped by Prometheus (self-hosted,
             Amazon Managed Prometheus, or Google Managed Prometheus).
* Traces  -> OpenTelemetry, enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is
             set. The OTel Collector forwards to X-Ray, Cloud Trace, Jaeger, ...
"""
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram

from churn import config

# ------------------------------------------------------------------ logging --


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level or os.getenv("LOG_LEVEL", "INFO"))


def log_event(logger: logging.Logger, msg: str, **fields) -> None:
    logger.info(msg, extra={"extra_fields": fields})


def pseudonymise(value: str | None) -> str | None:
    """Salted hash: stable (so we can join with ground truth later) but not
    reversible without the salt, which lives in a secret. GDPR/DPDP-friendly."""
    if value is None:
        return None
    return hashlib.sha256(f"{config.PII_SALT}:{value}".encode()).hexdigest()[:16]


# ------------------------------------------------------------------ metrics --
REQUESTS = Counter(
    "churn_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
LATENCY = Histogram(
    "churn_http_request_duration_seconds",
    "Request latency",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
PREDICTIONS = Counter(
    "churn_predictions_total", "Predictions served", ["model_version", "risk_band"]
)
SCORE = Histogram(
    "churn_prediction_score",
    "Distribution of predicted churn probability (watch for shifts)",
    ["model_version"],
    buckets=tuple(i / 10 for i in range(1, 11)),
)
FEATURE_VALUE = Histogram(
    "churn_feature_value",
    "Distribution of key numeric inputs (input drift)",
    ["feature"],
    buckets=(0, 1, 2, 5, 10, 25, 50, 75, 100, 150, 250, 1000, 5000),
)
MODEL_INFO = Gauge("churn_model_info", "Currently loaded model", ["model_version"])
MODEL_LOADED = Gauge("churn_model_loaded", "1 if a model is loaded")
FEEDBACK = Counter("churn_feedback_total", "Ground-truth labels received", ["churned"])


# ------------------------------------------------------------------ tracing --
def setup_tracing(app) -> bool:
    """Instrument FastAPI with OpenTelemetry if an OTLP endpoint is configured."""
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # tracing extras not installed
        logging.getLogger(__name__).warning("OTEL endpoint set but opentelemetry not installed")
        return False

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": os.getenv("OTEL_SERVICE_NAME", "churn-api"),
             "deployment.environment": config.ENV}
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready,metrics")
    return True
