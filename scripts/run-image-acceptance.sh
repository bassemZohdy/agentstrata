#!/usr/bin/env bash
# Image-based §18 ACC-01 acceptance run (M8 exit check).
#
# Builds the runtime image for one platform, layers the locked dev tooling on
# top (Dockerfile.test — the repo's .dockerignore excludes the dev lock from
# the normal context, so the test build stages its own context), then runs the
# full pytest suite INSIDE that image.
#
# The suite MUST exercise the shipped runtime contents, not the host checkout:
# the repo is mounted read-only at /repo WITHOUT app/ (app/ is not mounted at
# all), the working dir is /app (the image's own app copy), and
# pythonpath=/app for both the in-process imports and the OBS-06 subprocess
# test. Only tests/, config/, scripts/, schemas/, and pyproject.toml are
# bind-mounted (these are the only paths the suite reads from the repo).
#
# Usage:
#   bash scripts/run-image-acceptance.sh amd64 [--no-build]
#   bash scripts/run-image-acceptance.sh arm64 [--no-build]
#
# Evidence: docs/acceptance-<platform>.log (full pytest output) and
# docs/acceptance-<platform>.json (image ID, commit, lock hash, result).
set -euo pipefail

PLATFORM="${1:?usage: run-image-acceptance.sh <amd64|arm64> [--no-build]}"
SKIP_BUILD="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="agentbase:${PLATFORM}"
TEST_TAG="agentbase-test:${PLATFORM}"
LOG="${ROOT}/docs/acceptance-${PLATFORM}.log"
EVIDENCE="${ROOT}/docs/acceptance-${PLATFORM}.json"

if [[ "${SKIP_BUILD}" != "--no-build" ]]; then
	echo "==> Building runtime image ${TAG} (linux/${PLATFORM})"
	docker buildx build --platform "linux/${PLATFORM}" -t "${TAG}" --load "${ROOT}"
	echo "==> Building test image ${TEST_TAG}"
	STAGE="$(mktemp -d)"
	trap 'rm -rf "${STAGE}"' EXIT
	cp "${ROOT}/Dockerfile.test" "${STAGE}/Dockerfile"
	cp "${ROOT}/requirements-dev.lock" "${STAGE}/requirements-dev.lock"
	docker buildx build --platform "linux/${PLATFORM}" \
		--build-arg "BASE_IMAGE=${TAG}" -t "${TEST_TAG}" --load "${STAGE}"
else
	echo "==> Skipping image builds (--no-build)"
fi

echo "==> Running acceptance suite in ${TEST_TAG} (linux/${PLATFORM})"
mkdir -p "$(dirname "${LOG}")"

# Staleness gate: the suite must exercise the code at HEAD, never a stale
# image (--no-build is a footgun otherwise). Compare a sentinel app file
# between the image and the working tree; a mismatch invalidates the run.
SENTINEL="app/protocol/app.py"
LOCAL_HASH="$(sha256sum "${ROOT}/${SENTINEL}" | cut -d' ' -f1)"
IMAGE_HASH="$(MSYS_NO_PATHCONV=1 docker run --rm --platform "linux/${PLATFORM}" \
	--entrypoint sha256sum "${TEST_TAG}" "/app/${SENTINEL}" 2>/dev/null | cut -d' ' -f1)"
if [[ "${LOCAL_HASH}" != "${IMAGE_HASH}" ]]; then
	echo "==> ERROR: ${TEST_TAG} is stale (${SENTINEL} differs from the working tree); rebuild without --no-build" >&2
	exit 3
fi
STALENESS="pass"
# MSYS_NO_PATHCONV: git-bash on Windows would otherwise rewrite -w/-v/-e values
# into C:/Program Files/Git/... paths. The buildx invocations above are left
# unconverted-free because they work through the normal MSYS translation.
set +e
MSYS_NO_PATHCONV=1 docker run --rm --platform "linux/${PLATFORM}" \
	-v "$(cygpath -m "${ROOT}")/tests:/repo/tests:ro" \
	-v "$(cygpath -m "${ROOT}")/config:/repo/config:ro" \
	-v "$(cygpath -m "${ROOT}")/scripts:/repo/scripts:ro" \
	-v "$(cygpath -m "${ROOT}")/schemas:/repo/schemas:ro" \
	-v "$(cygpath -m "${ROOT}")/pyproject.toml:/repo/pyproject.toml:ro" \
	-w /app -e PYTHONPATH=/app \
	-e AGENT_TEST_MCP_CONNECT_SECONDS=120 \
	"${TEST_TAG}" /repo/tests -q -o addopts="" -p no:cacheprovider \
	-o pythonpath=/app >"${LOG}" 2>&1
STATUS=$?
set -e

SUMMARY="$(grep -oE "[0-9]+ passed|no tests ran|errors?" "${LOG}" | tail -1 || true)"
# A green docker exit with no pytest summary means the suite never ran.
if [[ "${STATUS}" -eq 0 ]] && ! grep -qE "[0-9]+ passed" "${LOG}"; then
	echo "==> ERROR: exit 0 but no pytest 'N passed' summary in log" >&2
	STATUS=2
fi

IMAGE_ID="$(docker image inspect --format '{{.Id}}' "${TEST_TAG}" 2>/dev/null || echo unknown)"
COMMIT="$(git -C "${ROOT}" rev-parse HEAD 2>/dev/null || echo unknown)"
LOCK_HASH="$(sha256sum "${ROOT}/requirements.lock" | cut -d' ' -f1)"
cat >"${EVIDENCE}" <<EOF
{
  "platform": "linux/${PLATFORM}",
  "test_image": "${TEST_TAG}",
  "image_id": "${IMAGE_ID}",
  "source_commit": "${COMMIT}",
  "lock_hash": "${LOCK_HASH}",
  "status": "$([ "${STATUS}" -eq 0 ] && echo pass || echo fail)",
  "staleness_check": "${STALENESS}",
  "pytest_summary": "${SUMMARY}"
}
EOF

echo "==> Exit status: ${STATUS} (summary: ${SUMMARY})"
echo "==> Evidence: docs/acceptance-${PLATFORM}.json"
tail -n 5 "${LOG}"
exit "${STATUS}"
