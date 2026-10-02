"""Load test. Answers: what throughput can one pod sustain within the latency SLO,
and how many pods does peak traffic need (-> HPA min/max)?

    # SLO check at expected per-pod load (20 req/s):
    locust -f locustfile.py --host http://localhost:8000 --headless \
        -u 20 -r 5 -t 2m --csv reports/load
    python scripts/check_slo.py reports/load_stats.csv     # fails if SLO breached
"""
import os
import random

from locust import HttpUser, constant_throughput, task

API_KEY = os.getenv("LOAD_API_KEY", "dev-key")
CONTRACTS = ["month-to-month", "one_year", "two_year"]
PAYMENTS = ["electronic_check", "credit_card", "bank_transfer", "mailed_check"]
INTERNET = ["fiber", "dsl", "none"]


def customer(i: int) -> dict:
    tenure = random.randint(1, 72)
    monthly = round(random.uniform(20, 120), 2)
    return {
        "customer_id": f"load-{i}",
        "tenure_months": tenure,
        "monthly_charges": monthly,
        "total_charges": round(tenure * monthly, 2),
        "num_support_tickets": random.randint(0, 6),
        "contract_type": random.choice(CONTRACTS),
        "payment_method": random.choice(PAYMENTS),
        "internet_service": random.choice(INTERNET),
        "senior_citizen": random.randint(0, 1),
    }


class ApiClient(HttpUser):
    # Each simulated client sends N requests/second, so `-u 20` = 20 req/s.
    # Test the SLO at EXPECTED load; find the breaking point separately.
    wait_time = constant_throughput(float(os.getenv("LOAD_RPS_PER_USER", "1")))

    def on_start(self):
        self.client.headers.update({"X-API-Key": API_KEY})

    @task(10)
    def predict(self):
        self.client.post("/v1/predict", json=customer(random.randint(0, 10**6)), name="/v1/predict")

    @task(1)
    def batch(self):
        self.client.post("/v1/predict/batch",
                         json={"customers": [customer(i) for i in range(50)]}, name="/v1/predict/batch")
