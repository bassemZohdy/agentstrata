"""Healthcheck probe (REQUIREMENTS.md CNT-10; fixed module path per DEL-01).

The Docker HEALTHCHECK invokes ``python -m app.healthcheck``; the probe
reads a bound-port marker file written by the runtime at boot and exits 0
only when the service is up. The marker is the loopback-filesystem signal
the container checks (no network dependency inside the probe).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# CNT-10: bound-port file lives under /tmp (read-only rootfs: /tmp is the
# writable scratch; the runtime touches this file once the listener binds).
MARKER = Path(os.environ.get("AGENT_HEALTH_MARKER", "/tmp/agentstrata.ready"))


def main() -> int:
    try:
        if not MARKER.is_file():
            return 1
        return 0
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
