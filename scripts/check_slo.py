"""Fail the build if a load test breached the SLO.

    python scripts/check_slo.py reports/load_stats.csv [--p95-ms 250] [--max-error-rate 0.005]
"""
import argparse
import csv
import sys


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("stats_csv")
    p.add_argument("--endpoint", default="/v1/predict")
    p.add_argument("--p95-ms", type=float, default=250)
    p.add_argument("--max-error-rate", type=float, default=0.005)
    p.add_argument("--min-requests", type=int, default=100)
    a = p.parse_args()

    rows = {r["Name"]: r for r in csv.DictReader(open(a.stats_csv))}
    if a.endpoint not in rows:
        sys.exit(f"endpoint {a.endpoint} not in {list(rows)}")
    r = rows[a.endpoint]
    n, fails = int(r["Request Count"]), int(r["Failure Count"])
    p95, rps = float(r["95%"]), float(r["Requests/s"])
    err = fails / max(n, 1)

    print(f"{a.endpoint}: {n} requests, {rps:.1f} req/s, p95={p95:.0f}ms, errors={err:.2%}")
    problems = []
    if n < a.min_requests:
        problems.append(f"only {n} requests (< {a.min_requests}) — test too short to judge")
    if p95 > a.p95_ms:
        problems.append(f"p95 {p95:.0f}ms > SLO {a.p95_ms:.0f}ms")
    if err > a.max_error_rate:
        problems.append(f"error rate {err:.2%} > SLO {a.max_error_rate:.2%}")
    if problems:
        sys.exit("SLO BREACHED: " + "; ".join(problems))
    print("SLO met")


if __name__ == "__main__":
    main()
