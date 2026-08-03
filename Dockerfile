# syntax=docker/dockerfile:1
#
# AgentStrata runtime image.
# REQUIREMENTS.md: CNT-01 (builder base + venv + lock hashes), CNT-02
# (multi-arch), CNT-03 (non-root arbitrary UID), CNT-04 (exec ENTRYPOINT),
# CNT-05 (VOLUME/EXPOSE), CNT-06 (env), CNT-08 (single worker, no reload),
# CNT-10 (HEALTHCHECK), CNT-11 (read-only rootfs), CNT-13 (secrets hygiene).

# --- Builder stage -----------------------------------------------------------
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
COPY config /app/config
COPY schemas /app/schemas
COPY LICENSE /app/LICENSE

# CNT-03: non-root arbitrary-UID (OpenShift-compatible): run as UID 10001
# group 0; paths are group-writable so a platform-assigned UID still works.
RUN useradd --uid 10001 --gid 0 --no-create-home --shell /usr/sbin/nologin agentstrata \
    && mkdir -p /tmp /var/lib/agentstrata \
    && chmod -R g+w /tmp /var/lib/agentstrata /app \
    && chown -R 10001:0 /tmp /var/lib/agentstrata /app
USER 10001:0

# CNT-11: read-only rootfs — writes confined to /tmp and storage.path
# (the image is run with --read-only + tmpfs mounts for those).
VOLUME ["/tmp", "/var/lib/agentstrata"]

# CNT-10: HEALTHCHECK uses the loopback probe (bound-port marker file).
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

# CNT-04: exec form — PID 1 is python directly (no shell wrapper, no tini).
ENTRYPOINT ["python", "-m", "app.main"]
