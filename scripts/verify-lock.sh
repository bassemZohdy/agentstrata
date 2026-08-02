#!/usr/bin/env bash
# Verify the hash-pinned lockfiles (REQUIREMENTS.md STACK-01; CNT-01 in the
# Docker builder). Succeeds only if every dependency resolves to a version
# whose hash is present in the lock. Does not install anything and needs no
# virtual environment (resolves for Python 3.12 to match the runtime image).
#
# Used by CI (lockfile hash verification job) and locally.
set -euo pipefail
cd "$(dirname "$0")/.."

UV="${UV:-uv}"

"$UV" pip install --dry-run --require-hashes --python 3.12 -r requirements.lock
"$UV" pip install --dry-run --require-hashes --python 3.12 -r requirements-dev.lock

echo "Lockfile hash verification passed."
