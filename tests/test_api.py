import json

import pytest

from churn import config


def test_probes_need_no_auth(client, trained):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["model_version"] == trained["version"]


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_predict_requires_valid_key(client, customer, headers):
    assert client.post("/v1/predict", json=customer, headers=headers).status_code == 401


def test_predict(client, customer, auth, trained):
    r = client.post("/v1/predict", json=customer, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert 0 <= body["churn_probability"] <= 1
    assert body["model_version"] == trained["version"]
    assert body["will_churn"] == (body["churn_probability"] >= trained["decision_threshold"])
    assert "x-request-id" in r.headers


def test_request_id_propagates(client, customer, auth):
    r = client.post("/v1/predict", json=customer, headers={**auth, "x-request-id": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


@pytest.mark.parametrize(
    "patch",
    [{"contract_type": "lifetime"}, {"tenure_months": -1}, {"monthly_charges": 0},
     {"unexpected_field": 1}, {"senior_citizen": 3}],
)
def test_invalid_input_returns_422(client, customer, auth, patch):
    assert client.post("/v1/predict", json={**customer, **patch}, headers=auth).status_code == 422


def test_payload_too_large(client, auth):
    r = client.post("/v1/predict/batch", content=b"x" * 1_000_001,
                    headers={**auth, "content-type": "application/json"})
    assert r.status_code == 413


def test_batch(client, customer, auth):
    r = client.post("/v1/predict/batch", json={"customers": [customer] * 5}, headers=auth)
    assert r.status_code == 200 and len(r.json()["predictions"]) == 5


def test_batch_limit(client, customer, auth):
    r = client.post("/v1/predict/batch", json={"customers": [customer] * 1001}, headers=auth)
    assert r.status_code == 422


def test_prediction_events_are_pseudonymised(client, customer, auth):
    client.post("/v1/predict", json=customer, headers=auth)
    last = json.loads(open(config.PREDICTION_LOG).read().strip().splitlines()[-1])
    assert last["event"] == "prediction"
    assert "T1" not in json.dumps(last)  # raw ID never logged
    assert len(last["customer_hash"]) == 16
    assert last["features"]["tenure_months"] == customer["tenure_months"]


def test_feedback(client, auth):
    r = client.post("/v1/feedback", json={"customer_id": "T1", "churned": True}, headers=auth)
    assert r.status_code == 202


def test_prometheus_metrics(client, customer, auth):
    client.post("/v1/predict", json=customer, headers=auth)
    text = client.get("/metrics").text
    assert "churn_predictions_total" in text
    assert 'route="/v1/predict"' in text
    assert "churn_model_loaded 1.0" in text


def test_admin_requires_admin_key(client, auth):
    assert client.get("/v1/admin/registry", headers=auth).status_code == 403
    r = client.get("/v1/admin/registry", headers={"X-API-Key": "admin-key"})
    assert r.status_code == 200 and r.json()["production"] == r.json()["serving"]


def test_no_runtime_model_swap_endpoint(client):
    assert client.post("/admin/reload").status_code in (404, 405)
