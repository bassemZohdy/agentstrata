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

## Release checklist

1. `git rev-parse HEAD` — record the commit.
2. `sha256sum requirements.lock requirements-dev.lock schemas/*.json` — record hashes.
3. `uv pip install --python .venv -r requirements-dev.lock --require-hashes` then
   `pytest tests/ -q` — all green.
4. `.venv/bin/python scripts/gen-schemas.py && git diff --exit-code -- schemas/` — zero diff.
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
