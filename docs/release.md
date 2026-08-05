# Release evidence (TRC-02)

Each released image MUST be traceable to the exact source commit,
dependency-lock hash, and test results that verified it.

## Image

| Field | Value |
| --- | --- |
| Image digest | `docker inspect --format '{{index .RepoDigests 0}}' agentbase:<tag>` |
| Source commit | `git rev-parse HEAD` |
| Lock hash | `sha256sum requirements.lock` |
| Schema artifacts | `sha256sum schemas/*.json` |
| Test results | `pytest --tb=short -q` output archive |

## Recorded evidence — P4 acceptance run (2026-08-04)

| Field | Value |
| --- | --- |
| Source commit | `32d048a` (exact final candidate — all audit fixes, CI gates green) |
| Image digest | amd64 `agentbase@sha256:f9831410d2a06b71afd9b1f5b619de4c32fd2109000cf034afe90278f3158dac`; arm64 `agentbase@sha256:d83d97b9249c0641aa25e3d9a9f82430127ce685f8b7cddae1980cbc7eca235a` (re-verify per build) |
| ACC-01 exact-candidate | `docs/acceptance-{amd64,arm64}.json` record `source_commit: 32d048a` == HEAD, staleness pass, **450/450 both archs** |
| Acceptance | **450/450 passed inside the image on linux/amd64 AND linux/arm64** (`docs/acceptance-{amd64,arm64}.{log,json}` — RAG-01..06 suites included) |
| Host suite | **450 passed**, ruff + mypy clean, schemas zero-diff, traceability 164 IDs mapped |
| Capabilities | phase `P4`; `multiAgent`/`acp`/`approval`/`rag` all true (CAP-02) |

## Recorded evidence — P3 acceptance run (2026-08-04)

| Field | Value |
| --- | --- |
| Source commit | P3 acceptance run: `4c5c5bc` + `p3-acceptance` commit (capability flip) |
| Acceptance | **417/417 passed inside the image on linux/amd64 AND linux/arm64** (`docs/acceptance-{amd64,arm64}.{log,json}` — HITL-01..05 suites included) |
| Host suite | **417 passed**, ruff + mypy clean, schemas zero-diff, traceability 164 IDs mapped |
| Capabilities | phase `P3`; `multiAgent`/`acp`/`approval` true, `rag` fail-closed (CAP-02) |

## Recorded evidence — M8 exit-check run (2026-08-04)

| Field | Value |
| --- | --- |
| amd64 image ID | `sha256:cbfbf71efab6987a062aed4f540bfee0ed89ad5d3657803ace2dc1e440425df3` (current code; re-verify per build) |
| Source commit | `c7e8329` (M8 exit-check run: `98f2a8c` + compose/CNT-10 commit `c7e8329`) |
| Lock hash | `e6f4ec176f693a1305a5b1d0c5e7bfcb09163adf65ecb13c6ed2579689af4944` (`requirements.lock`) |
| Acceptance | **339/339 passed inside the image on linux/amd64 AND linux/arm64** (`docs/acceptance-{amd64,arm64}.{log,json}`, incl. image ID / commit / lock hash / staleness check) |
| Host suite | **340 passed**, ruff + mypy clean, schemas zero-diff |
| NFR-00 | `docs/nfr-report.json` — **7/7 gates pass** incl. NFR-02 (p95 18.4 ms < 50 ms, spec-conformant measurement with the deterministic in-process mock AgentRunner per §6; end-to-end reference recorded alongside) |
| NFR-08 | zero-downtime reload PASS (live 1→2, rebuild 2→3, 0 failures, no restart) |
| Supply chain | `docs/supplychain/` — CycloneDX + SPDX SBOMs, trivy scan (23 OS HIGH/CRIT with no fix yet — release-blocking per CNT-12 until Debian ships; 0 fixable python findings), buildx SLSA v1 provenance, keyless-signing workflow, canary scan passed |
| Compose smoke | PASS (runtime healthy, MCP sample connected, redis/postgres healthy, auth 401/200) |

## Recorded evidence — P5 acceptance (P5-1 … P5-4)

P5 shipped in four milestones; P5-1/P5-2/P5-3 acceptance passed before
P5-4 (cost accounting) started:

| Field | Value |
| --- | --- |
| P5-1 Prometheus `/metrics` (OBS-05) | `be03e89` — metrics + `/metrics` endpoint shipped |
| P5-2 WebSocket API (WS-01) | `1bf4f26` — `/v1/ws` shipped |
| P5-3 Kubernetes CRD / operator (K8S-11/12) | `f11ae43` — CRD + operator shipped, phase flipped to P5 |
| P5-4 cost accounting (COST-01/02) | `d90bc26` + P5-4 finish-line commit `8259f30` |
| Host suite | **527 passed** (2026-08-06 finish line), ruff + mypy clean (app, scripts, and tests), schemas zero-diff |
| Capabilities | phase `P5`; `multiAgent`/`acp`/`approval`/`rag`/`costs` all true (CAP-02, COST-01) |

## Release checklist

1. `git rev-parse HEAD` — record the commit.
2. `sha256sum requirements.lock requirements-dev.lock schemas/*.json` — record hashes.
3. `uv pip install --python .venv -r requirements-dev.lock --require-hashes` then
   `pytest tests/ -q` — all green (the COST-01/02 suite is
   `tests/test_protocol/test_cost.py` + `tests/test_config/test_validation.py`).
4. `.venv/bin/python scripts/gen-schemas.py && git diff --exit-code -- schemas/` — zero diff
   (re-run after any config-model change, e.g. the `costs` models).
5. `.venv/bin/python scripts/gen-traceability.py` — all requirement IDs mapped.
6. `.venv/bin/python scripts/benchmark.py` — record `docs/nfr-report.json`.
7. Build: `docker build -t agentbase:<commit-short> .` — record the digest.
8. Run the §18 acceptance suite against the built image (storage proofs per
   the recorded ACC-01 deviation: memory + real file; redis/postgres via
   in-memory substitutes; real-instance + fencing proofs deferred).
9. Record all fields above in the release notes; the image ships only when
   every check passes.

## Vulnerability gate (CNT-12, default recorded)

A release is blocked when the image scan reports any CRITICAL or HIGH CVE
with no available fix; MEDIUM/LOW are tracked, non-blocking.

Scan commands (run in the release environment; the tools are not part of the
runtime image):

```bash
# SBOM (SPDX or CycloneDX)
docker sbom agentbase:<tag> --format cyclonedx-json --output sbom.cdx.json
# Vulnerability scan
trivy image --severity CRITICAL,HIGH --exit-code 1 agentbase:<tag>
```

Both architectures build via buildx QEMU (verified: linux/amd64 + linux/arm64);
the multi-arch manifest list is exported on registry push (TRC-02).
