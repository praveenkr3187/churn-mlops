# Security

## Threat model (what we defend against)

| Threat | Example | Controls |
|---|---|---|
| Unauthenticated use | Scraper calls `/v1/predict` | API keys (fail closed), TLS, only `/v1/*` routed |
| Abuse / DoS | One client floods the batch endpoint | Per-key rate limit (Envoy), 1,000-row batch cap, 1 MB body limit, HPA, circuit breaker |
| Malicious input | Huge numbers, unknown fields, wrong types | Pydantic contract with ranges, `extra="forbid"` → 422 |
| Model tampering | Someone swaps `model.joblib` for a pickle that runs code | Write access limited to the trainer identity; SHA-256 verified before unpickling; bucket versioning |
| Stolen credentials | Leaked AWS key in a repo | No long-lived keys exist: GitHub OIDC for CI, pod identity for workloads |
| Lateral movement | Compromised pod reaches other services | NetworkPolicy default deny, no SA token, non-root, read-only FS, no capabilities, IMDSv2 hop limit 1 (AWS) |
| Supply chain | Poisoned PyPI package or GitHub Action | Hashed lockfile, pip-audit, Trivy, SBOM, cosign signatures, Dependabot, SHA-pinned actions |
| PII leakage | Customer IDs in logs | Salted hash in logs; raw IDs never logged; batch output holds IDs + scores only |
| Secret exposure | Keys in Git or Terraform state | Cloud secret manager + External Secrets; Terraform creates the container only |
| Privilege misuse in CI | A PR deploys to prod | Cloud trust bound to GitHub *environment*; prod requires reviewers; per-namespace cluster access |
| Unfair outcomes | Model under-serves a group | Fairness gate, model card, human review of actions |

## Identity map (who can do what)

| Identity | Can | Cannot |
|---|---|---|
| `churn-api` pod | read models bucket | write anything |
| `churn-trainer` pod | read data; write model versions and the **staging** alias | move **production** |
| `churn-batch` pod | read data and models; write `scores/` | write models |
| External Secrets | read the one `churn-api` secret | other secrets |
| GitHub `staging` / `prod` env | push images, deploy to its own namespace, move the production alias | touch other namespaces or accounts |
| Humans (SSO) | cluster admin via access entries / IAM | — (use break-glass sparingly) |

## Supply-chain case study: the Trivy GitHub Action compromise (March 2026)

In March 2026, attackers hijacked version tags of `aquasecurity/trivy-action`
(and related Trivy releases) so that workflows using a tag like `@0.28.0`
silently ran malicious code that stole CI secrets (CVE-2026-33634). Teams
had done the "right" thing by adding a security scanner, and that scanner
became the attack vector.

**Lessons applied here:**
1. Tags are mutable; commit SHAs are not. In your fork, pin every `uses:` to
   a full SHA (`pinact run` does it automatically; Dependabot keeps SHAs
   updated). This repo uses tags only for readability.
2. Keep secrets out of CI where possible. Our deploy jobs use OIDC:
   there's no cloud key in GitHub to steal.
3. Least-privilege `permissions:` per job (`contents: read` by default).
4. We use `trivy-action@v0.35.0`, a version fixed after the incident.

References: [GitHub advisory GHSA-69fq-xp46-6x23](https://github.com/advisories/GHSA-69fq-xp46-6x23),
[Microsoft guidance](https://www.microsoft.com/en-us/security/blog/2026/03/24/detecting-investigating-defending-against-trivy-supply-chain-compromise/).

## Scanner exceptions (all deliberate)

| Tool | Check | Reason |
|---|---|---|
| checkov k8s | CKV_K8S_11 CPU limits | CPU limits cause throttling and latency spikes; we set requests + memory limits |
| checkov k8s | CKV_K8S_43 image digests | Tags are immutable and signed. Next step: have `release.py` write digests |
| checkov k8s | CKV_K8S_15 pull policy Always | Not needed with immutable tags |
| checkov k8s | CKV_K8S_35 secrets as env | 12-factor trade-off, mitigated by RBAC; mounting as files is stricter |
| checkov tf | CKV_AWS_18 / CKV_GCP_62 bucket access logs | Use CloudTrail S3 data events / Cloud Audit Logs at org level |
| checkov tf | CKV_AWS_144 cross-region replication | Models are reproducible from data + code |
| checkov tf | CKV2_AWS_57 secret auto-rotation | Documented manual rotation (OPERATIONS.md) |
| checkov tf | CKV_TF_1 module commit hash | Registry modules pinned by version + lock file |
| checkov tf | CKV_GCP_12/13/61/69 | Enforced by GKE Autopilot itself (false positives) |

Every exception lives next to the code (`# checkov:skip=...: reason` or
`.checkov-k8s.yaml`). A skip without a reason fails review.

## Next hardening steps (beyond this session)
- **Enforce signed images at admission:** GKE Binary Authorization policy
  requiring the cosign attestation, or Kyverno / Sigstore policy-controller
  on EKS.
- **OAuth2 client credentials** instead of static API keys when there are
  many clients.
- **WAF** (AWS WAF on an ALB / Cloud Armor on an L7 LB) if the API becomes
  internet-facing for untrusted clients.
- **Private cluster endpoints** + self-hosted CI runners inside the VPC.
- **Mount secrets as files** and move from env vars.
