#!/usr/bin/env python3
"""NFR-09 helper: stdio MCP server wrapper for the dependency-recovery probe.

Simulates a required stdio MCP server that is DOWN while ``/marker/blocked``
exists (the reconciler's connect fails, readiness 503), then becomes reachable
once the marker is removed (execs the real spike server). The marker lives on
a small rw bind mount so the harness can toggle it from the host.
"""

import os
import sys

if os.path.exists("/marker/blocked"):
    sys.exit(1)
os.execv(sys.executable, [sys.executable, "/scripts/spike_mcp_server.py"])
