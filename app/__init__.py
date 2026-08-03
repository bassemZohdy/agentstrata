"""Agentbase runtime package.

Milestone 0 bootstrap: package skeleton only — no application functionality yet.

Concern areas (free-form internal layout per REQUIREMENTS.md DEL-01, each kept
independently testable; the only fixed paths are ``app.main`` and
``app.healthcheck``):

- ``app.config``   — configuration engine (Milestone 1)
- ``app.storage``  — session/run/idempotency backends (Milestone 2)
- ``app.engine``   — agent execution (Milestone 3)
- ``app.protocol`` — HTTP/API surface (Milestone 5)
- ``app.security`` — auth, redaction, egress (Milestone 5)
- ``app.watcher``  — Kubernetes ConfigMap watcher and reload (Milestone 6)
"""

__version__ = "0.1.0"
