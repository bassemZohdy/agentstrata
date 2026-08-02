#!/usr/bin/env bash
# Regenerate the locked dependency manifests (REQUIREMENTS.md STACK-01).
#
# The lockfiles are the release-critical artifact: exact versions + hashes,
# universal (no platform markers — the image builds for linux/amd64 and
# linux/arm64, CNT-02) and resolved for Python 3.12 to match the runtime
# image (CNT-01). Run from the repository root.
#
# Review policy: a library upgrade that changes a documented API shape or
# lifecycle is a requirements-impacting change (STACK-01) — it must go
# through the same review as a REQUIREMENTS.md change, not land as an
# automatic dependency bump.
set -euo pipefail
cd "$(dirname "$0")/.."

uv pip compile requirements.txt \
    -o requirements.lock \
    --generate-hashes \
    --universal \
    --python-version 3.12

uv pip compile requirements.txt requirements-dev.txt \
    -o requirements-dev.lock \
    --generate-hashes \
    --universal \
    --python-version 3.12

echo "Lockfiles regenerated: requirements.lock, requirements-dev.lock"
