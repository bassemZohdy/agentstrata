#!/usr/bin/env python3
"""Generate committed schema artifacts (REQUIREMENTS.md SCH-02, API-18,
DEL-02). Never hand-edit generated files; CI regenerates and zero-diffs:

    .venv/bin/python scripts/gen-schemas.py && git diff --exit-code -- schemas/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config.models import SCHEMA_MAJOR, AgentConfig  # noqa: E402

SCHEMA_ID = f"https://agentstrata.dev/schemas/agent.schema.v{SCHEMA_MAJOR}.json"


def build_agent_schema() -> dict:
    schema = AgentConfig.model_json_schema(by_alias=True)
    schema["$id"] = SCHEMA_ID
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "AgentStrata Agent Definition"
    return schema


def build_openapi() -> dict:
    """API-18: generate openapi.json from the running app's OpenAPI schema."""
    from google.adk.runners import Runner as AdkRunner

    from app.config.resolver import resolve
    from app.config.validate import validate_resolution
    from app.engine.agent import AppliedConfig, build_agent_component
    from app.engine.mcp.manager import ServerManager
    from app.engine.runner import AgentRunner
    from app.protocol.app import create_app
    from app.storage.adk_adapter import AdkSessionService
    from app.storage.memory import MemoryBackend

    res = resolve(env={}, bundled_dir=str(ROOT / "config"), argv=[])
    result = validate_resolution(res)
    if not result.ok:
        raise SystemExit("config resolution failed while generating OpenAPI")
    assert result.config is not None
    config = result.config

    backend = MemoryBackend()
    applied = AppliedConfig.from_config(config)
    component = build_agent_component(config)
    service = AdkSessionService(backend)
    adk_runner = AdkRunner(agent=component.agent, app_name=config.name, session_service=service)
    runner = AgentRunner(applied, adk_runner, backend, app_name=config.name)
    mcp = ServerManager(applied)
    components = {
        "applied": applied,
        "agent": component,
        "runner": runner,
        "mcp": mcp,
        "backend": backend,
        "session_service": service,
    }
    app = create_app(config, components, mode="standalone")
    return app.openapi()


def main() -> int:
    agent = ROOT / "schemas" / "agent.schema.json"
    agent.write_text(
        json.dumps(build_agent_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    openapi = ROOT / "schemas" / "openapi.json"
    openapi.write_text(
        json.dumps(build_openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {agent}")
    print(f"wrote {openapi}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
