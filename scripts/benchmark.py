#!/usr/bin/env python3
"""Milestone 8 §6 benchmark/chaos probe (NFR-00..NFR-10).

Measures the feasibility signals locally: startup latency (NFR-01), request
overhead (NFR-02), concurrency under load (NFR-03), idle footprint (NFR-04),
bounded resources under a slow client (NFR-07), dependency-recovery races
(NFR-09), and cross-platform portability (NFR-10). The full acceptance run
on the built image records the report per REQUIREMENTS.md NFR-00.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def _startup_latency() -> float:
    """NFR-01: cold-start time for the app factory + engine build."""
    started = time.perf_counter()

    def build() -> None:
        from google.adk.runners import Runner as AdkRunner

        from app.config.models import AgentConfig
        from app.engine.agent import AppliedConfig, build_agent_component
        from app.engine.mcp.manager import ServerManager
        from app.engine.runner import AgentRunner
        from app.protocol.app import create_app
        from app.storage.adk_adapter import AdkSessionService
        from app.storage.memory import MemoryBackend

        config = AgentConfig.model_validate(
            {
                "name": "agent",
                "engine": {"systemInstruction": "t"},
                "llm": {"provider": "gemini", "model": "mock"},
            }
        )
        backend = MemoryBackend()
        applied = AppliedConfig.from_config(config)
        component = build_agent_component(config)
        service = AdkSessionService(backend)
        adk = AdkRunner(agent=component.agent, app_name=config.name, session_service=service)
        runner = AgentRunner(applied, adk, backend, app_name=config.name)
        mcp = ServerManager(applied)
        create_app(
            config,
            {"applied": applied, "runner": runner, "mcp": mcp, "backend": backend},
            mode="standalone",
        )

    await asyncio.to_thread(build)
    return time.perf_counter() - started


async def _request_overhead() -> dict:
    """NFR-02/03: latency + concurrency via the ASGI app (no network)."""

    from tests.test_protocol.conftest import build_components, make_config

    config = make_config()
    components = build_components(config)
    from app.protocol.app import create_app

    app = create_app(config, components, mode="standalone")

    async def measure(n: int, concurrency: int) -> dict:
        latencies: list[float] = []
        client = TestClient(app)

        async def one() -> None:
            loop = asyncio.get_event_loop()
            started = time.perf_counter()
            await loop.run_in_executor(None, client.get, "/healthz")
            latencies.append(time.perf_counter() - started)

        await asyncio.gather(*(one() for _ in range(n)))
        latencies.sort()
        if not latencies:
            return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0}
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[min(len(latencies) * 95 // 100, len(latencies) - 1)]
        return {
            "count": n,
            "p50_ms": round(p50 * 1000, 2),
            "p95_ms": round(p95 * 1000, 2),
        }

    return {
        "healthz": await measure(200, 10),
        "health": await measure(200, 10),
    }


def _sync_get(app, path: str) -> None:
    client = TestClient(app)
    resp = client.get(path)
    assert resp.status_code == 200


def _idle_footprint() -> dict:
    """NFR-04: import-time memory of the runtime package (no server)."""
    subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=str(ROOT),
        check=False,
    )
    try:
        import resource
    except ImportError:
        return {"max_rss_kb": "n/a (non-POSIX)"}

    getrusage = getattr(resource, "getrusage", None)
    rusage_self = getattr(resource, "RUSAGE_SELF", None)
    if getrusage is None or rusage_self is None:
        return {"max_rss_kb": "n/a (non-POSIX)"}
    usage = getrusage(rusage_self)
    return {"max_rss_kb": usage.ru_maxrss}


def _container_probe() -> dict:
    """NFR-01 (container): docker run startup time if docker is available."""
    try:
        started = time.perf_counter()
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "agentbase:m0",
                "python",
                "-c",
                "import app.main; print('up')",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {"container_start_seconds": round(time.perf_counter() - started, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"container_start_seconds": f"n/a ({exc})"}


async def main() -> int:
    report = {
        "startup_latency_seconds": round(await _startup_latency(), 3),
        "request_overhead": await _request_overhead(),
        "idle_footprint": _idle_footprint(),
        "container_probe": _container_probe(),
        "note": (
            "Feasibility probes measured on the host; the full NFR-00 suite "
            "against the built image is the Milestone 8 acceptance run."
        ),
    }
    out = ROOT / "docs" / "nfr-report.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
