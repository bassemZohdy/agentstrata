# Supply-chain evidence (CNT-12, CNT-13) — M8 exit check

Generated from the local release-environment run on the `linux/amd64` image.
All artifacts are reproducible with the commands below.

## Artifacts

| Artifact | File | Notes |
| --- | --- | --- |
| CycloneDX SBOM | `sbom-agentbase-amd64.cdx.json` | 3 097 components (OS, python, file) |
| SPDX SBOM | `sbom-agentbase-amd64.spdx.json` | SPDX-2.3, 184 packages |
| Vulnerability scan | (see table below) | trivy, CRITICAL/HIGH |
| Provenance | OCI layout with in-toto SLSA v1 attestation | see below |
| Keyless signing | `.github/workflows/release.yml` | runs on tag push (GitHub OIDC) |
| Canary-secret scan | `scripts/canary-scan.py` | passed |

Image digest (amd64, current code): `sha256:cbfbf71efab6987a062aed4f540bfee0ed89ad5d3657803ace2dc1e440425df3`
Source commit: `git rev-parse HEAD` at scan time; dependency lock:
`sha256sum requirements.lock`.

## Vulnerability scan (CNT-12 severity policy)

Policy (recorded in TODO.md "Decisions made"): a release is blocked when the
scan reports any CRITICAL or HIGH CVE **with no available fix**; MEDIUM/LOW are
tracked, non-blocking.

Result (re-verified 2026-08-04 against the rebuilt image): **23 OS-level
HIGH/CRITICAL findings (19 HIGH, 4 CRITICAL), ALL without an upstream fix
yet** (Debian trixie has not published updates; CVE-2026-53615 util-linux
family, CVE-2025-69720 ncurses,
CVE-2026-13221/42496/42497/48962/57432/57433/8376/9538 perl-base,
CVE-2026-41992 gzip, CVE-2026-54369 libacl1, CVE-2026-53615 bsdutils/mount).

**Status: the CNT-12 publication gate FAILS CLOSED until Debian ships
fixes** — this is an upstream-environment constraint (the base image's OS
packages), not a code-side gap; per PHASE-01 the phases remain
independently releasable on their §18 acceptance. The release workflow
re-runs the scan on every release; when fixes land, bump the base-image
digest (a reviewed change per CNT-12) and re-verify.

Fixable findings: the scan initially also reported 2 HIGH python-pkg findings
(msgpack 1.1.2, setuptools 70.3.0) — both were pip's vendored packages. The
runtime never invokes pip, so the Dockerfile now removes pip from the runtime
stage (CNT-01/CNT-12); the re-scan shows **0 fixable findings** in the Python
environment.

## Provenance

```
docker buildx build --provenance=true --platform linux/amd64 \
  --output type=oci,dest=agentbase-oci.tar .
```

The OCI layout contains an in-toto Statement v1 with a SLSA provenance v1
predicate (buildkit `slsa-definitions` buildType, subject image digest,
resolved base-image dependencies). Attestation blob:
`sha256:961fbf3d9580090a58b2ce378a5e44f07699fefc4a6e01ec6b01fa1dd379eb8c`.

## Keyless signing

Keyless signing requires an OIDC identity (GitHub Actions `id-token: write`);
it cannot run from a local machine. `.github/workflows/release.yml` performs
the signing step of record on `v*` tag push: multi-arch build+push with
`provenance: true` + `sbom: true` attestations, then `cosign sign` (keyless)
on the image digest and its attestations. Signatures live in the registry
next to the image.

## Canary-secret scan (CNT-13)

```
python scripts/canary-scan.py agentbase:amd64
```

Checks: (1) no forbidden paths (`*.pem`, `*.key`, `.env`, `id_rsa`,
credentials, kubeconfig) or canary content in any image layer; (2) image
history contains no canary/secret-value patterns; (3) a canary file excluded
by `.dockerignore` never reaches a built image (context boundary).
Result: **passed**.

## Reproduce

```bash
docker save agentbase:amd64 -o /tmp/agentbase.tar
docker run --rm -v /tmp:/scan anchore/syft:latest /scan/agentbase.tar \
  -o cyclonedx-json=/scan/sbom.cdx.json
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy:latest image --severity CRITICAL,HIGH agentbase:amd64
docker buildx build --provenance=true --platform linux/amd64 \
  --output type=oci,dest=/tmp/agentbase-oci.tar .
python scripts/canary-scan.py agentbase:amd64
```
