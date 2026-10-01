"""Automated canary analysis: compare the canary track with stable using
Prometheus, and decide PROMOTE or ROLLBACK. The release pipeline runs this
between each traffic step (10% -> 50% -> 100%).

    python scripts/canary_analysis.py --prometheus http://localhost:9090 \
        --namespace churn-prod --window 10m

Exit 0 = healthy (promote), 1 = unhealthy (abort), 2 = not enough data yet.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request


def query(prom: str, q: str) -> float | None:
    url = f"{prom}/api/v1/query?" + urllib.parse.urlencode({"query": q})
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310 (internal URL)
        data = json.load(r)["data"]["result"]
    if not data:
        return None
    v = float(data[0]["value"][1])
    return None if v != v else v  # NaN -> None


def metrics(prom: str, ns: str, track: str, window: str) -> dict:
    sel = f'namespace="{ns}",track="{track}",route=~"/v1/.*"'
    return {
        "requests": query(prom, f"sum(increase(churn_http_requests_total{{{sel}}}[{window}]))") or 0.0,
        "error_ratio": query(
            prom,
            f'sum(rate(churn_http_requests_total{{{sel},status=~"5.."}}[{window}]))'
            f" / sum(rate(churn_http_requests_total{{{sel}}}[{window}]))") or 0.0,
        "p95_s": query(
            prom,
            f"histogram_quantile(0.95, sum by (le) (rate(churn_http_request_duration_seconds_bucket"
            f'{{namespace="{ns}",track="{track}",route="/v1/predict"}}[{window}])))'),
        "mean_score": query(
            prom,
            f'sum(rate(churn_prediction_score_sum{{namespace="{ns}",track="{track}"}}[{window}]))'
            f' / sum(rate(churn_prediction_score_count{{namespace="{ns}",track="{track}"}}[{window}]))'),
    }


def judge(stable: dict, canary: dict, min_requests: int = 200) -> tuple[int, list[str]]:
    """Pure decision logic (unit-tested in tests/test_release.py)."""
    if canary["requests"] < min_requests:
        return 2, [f"canary has {canary['requests']:.0f} requests (< {min_requests}); wait longer"]
    reasons = []
    if canary["error_ratio"] > stable["error_ratio"] + 0.005:
        reasons.append(f"error ratio {canary['error_ratio']:.2%} vs stable {stable['error_ratio']:.2%}")
    if canary["p95_s"] is not None and stable["p95_s"] is not None:
        if canary["p95_s"] > stable["p95_s"] * 1.2 + 0.02:
            reasons.append(f"p95 {canary['p95_s'] * 1000:.0f}ms vs stable {stable['p95_s'] * 1000:.0f}ms")
    # Model-level guardrail: a new model may shift scores, but not wildly.
    if canary["mean_score"] is not None and stable["mean_score"] is not None:
        if abs(canary["mean_score"] - stable["mean_score"]) > 0.15:
            reasons.append(f"mean score {canary['mean_score']:.3f} vs stable {stable['mean_score']:.3f}")
    return (1, reasons) if reasons else (0, ["canary healthy"])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--prometheus", default="http://localhost:9090")
    p.add_argument("--namespace", required=True)
    p.add_argument("--window", default="10m")
    p.add_argument("--min-requests", type=int, default=200)
    a = p.parse_args()

    s = metrics(a.prometheus, a.namespace, "stable", a.window)
    c = metrics(a.prometheus, a.namespace, "canary", a.window)
    print(json.dumps({"stable": s, "canary": c}, indent=2))
    code, reasons = judge(s, c, a.min_requests)
    print({0: "PROMOTE", 1: "ROLLBACK", 2: "INCONCLUSIVE"}[code], "-", "; ".join(reasons))
    sys.exit(code)


if __name__ == "__main__":
    main()
