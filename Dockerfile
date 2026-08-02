# syntax=docker/dockerfile:1
#
# AgentStrata runtime image (Milestone 0 — functionally empty FastAPI service).
# REQUIREMENTS.md: CNT-01 (builder/runtime base + venv + lock hashes),
# CNT-04 (exec-form ENTRYPOINT), CNT-05 (VOLUME/EXPOSE), CNT-06 (env),
# CNT-08 (single worker, no reload/debug).

# --- Builder stage -----------------------------------------------------------
# CNT-01: digest-pinned python:3.12-slim, lock hash-verified, install into a venv.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Hash-pinned install: --require-hashes fails the build on any lock tampering
# (STACK-01 / CNT-01). The universal lock carries hashes for every supported
# platform, so this works for linux/amd64 and linux/arm64 alike.
COPY requirements.txt requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --require-hashes -r requirements.lock \
    && /opt/venv/bin/pip check

# --- Runtime stage -----------------------------------------------------------
# Same pinned slim family; only the venv, app/, schemas/, and license travel.
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# CNT-05
VOLUME /etc/agent
EXPOSE 8080

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app /app/app
COPY schemas /app/schemas
COPY LICENSE /app/LICENSE

# CNT-04: exec form — PID 1 is python directly (no shell wrapper, no tini),
# so signals reach the process; Uvicorn handles shutdown.
ENTRYPOINT ["python", "-m", "app.main"]
