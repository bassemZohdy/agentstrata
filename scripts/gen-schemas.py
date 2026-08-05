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

SCHEMA_ID = f"https://agentbase.dev/schemas/agent.schema.v{SCHEMA_MAJOR}.json"


def build_agent_schema() -> dict:
    schema = AgentConfig.model_json_schema(by_alias=True)
    schema["$id"] = SCHEMA_ID
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Agentbase Agent Definition"
    return schema


def build_overlay_schema() -> dict:
    """K8S-03/DEL-02: optional-fields variant of the full Agent Definition
    schema so operators can validate a ConfigMap's agent.yaml overlay."""
    schema = build_agent_schema()
    _make_optional(schema, set())
    schema["$id"] = f"https://agentbase.dev/schemas/agent-overlay.schema.v{SCHEMA_MAJOR}.json"
    schema["title"] = "Agentbase ConfigMap Overlay (all fields optional)"
    return schema


def _make_optional(node: dict, seen: set[str]) -> None:
    ref = node.get("$ref")
    if ref:
        if ref in seen:
            return
        seen.add(ref)
        target = node.get("$defs", {}).get(ref.rsplit("/", 1)[-1])
        if isinstance(target, dict):
            _make_optional(target, seen)
            return
    if "properties" in node:
        node.pop("required", None)
        for child in node["properties"].values():
            if isinstance(child, dict):
                _make_optional(child, seen)
    for key in ("allOf", "anyOf", "oneOf"):
        for child in node.get(key, []) or []:
            if isinstance(child, dict):
                _make_optional(child, seen)
    defs = node.get("$defs")
    if isinstance(defs, dict):
        for def_node in defs.values():
            if isinstance(def_node, dict):
                _make_optional(def_node, seen)


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


def build_crd() -> dict:
    """K8S-01: the AgentConfig CRD with the FULL agent schema embedded as
    the validation schema (spec == the Agent Definition document). The CRD
    is regenerated with the schema, so a config-schema change shows up as a
    gen-schemas zero-diff failure."""
    spec_schema = build_agent_schema()
    # CRD structural schemas cannot carry the $id/$schema document headers.
    spec_schema.pop("$id", None)
    spec_schema.pop("$schema", None)
    spec_schema.pop("title", None)
    return {
        "apiVersion": "apiextensions.k8s.io/v1",
        "kind": "CustomResourceDefinition",
        "metadata": {
            "name": "agentconfigs.agentstrata.io",
            "labels": {
                "app.kubernetes.io/managed-by": "agentstrata-operator",
            },
        },
        "spec": {
            "group": "agentstrata.io",
            "scope": "Namespaced",
            "names": {
                "plural": "agentconfigs",
                "singular": "agentconfig",
                "kind": "AgentConfig",
                "shortNames": ["agc"],
            },
            "versions": [
                {
                    "name": "v1",
                    "served": True,
                    "storage": True,
                    "subresources": {"status": {}},
                    "additionalPrinterColumns": [
                        {
                            "name": "Ready",
                            "type": "string",
                            "jsonPath": '.status.conditions[?(@.type=="Ready")].status',
                        },
                        {
                            "name": "ConfigMap",
                            "type": "string",
                            "jsonPath": ".status.appliedConfigMap",
                        },
                    ],
                    "schema": {
                        "openAPIV3Schema": {
                            "type": "object",
                            "properties": {
                                "spec": spec_schema,
                                "status": {
                                    "type": "object",
                                    "x-kubernetes-preserve-unknown-fields": True,
                                },
                            },
                        }
                    },
                }
            ],
        },
    }


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
    overlay = ROOT / "schemas" / "agent-overlay.schema.json"
    overlay.write_text(
        json.dumps(build_overlay_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    crd_dir = ROOT / "k8s_operator" / "crd"
    crd_dir.mkdir(parents=True, exist_ok=True)
    crd = crd_dir / "agentconfigs.agentstrata.io.yaml"
    import yaml as _yaml

    crd.write_text(
        _yaml.safe_dump(build_crd(), sort_keys=False, default_flow_style=False),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {agent}")
    print(f"wrote {openapi}")
    print(f"wrote {overlay}")
    print(f"wrote {crd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
