"""Schema contract tests (SCH-01 – SCH-09, BASE-01)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.config.models import (
    SCHEMA_MAJOR,
    SCHEMA_VERSION,
    AgentConfig,
    field_aliases,
    iter_schema_fields,
)
from app.config.resolver import resolve
from app.config.validate import validate_resolution


def base_doc(**overrides) -> dict:
    doc = {
        "name": "agent",
        "engine": {"systemInstruction": "You are a helpful assistant."},
        "llm": {"provider": "gemini", "model": "gemini-2.5-flash"},
    }
    doc.update(overrides)
    return doc


def invalid(doc) -> bool:
    try:
        AgentConfig.model_validate(doc)
        return False
    except ValidationError:
        return True


class TestBaseConfig:
    def test_model_config_sch01(self):
        cfg = AgentConfig.model_validate(base_doc())
        assert cfg.model_config.get("extra") == "forbid"
        assert cfg.model_config.get("strict") is True
        assert cfg.model_config.get("populate_by_name") is True

    def test_defaults(self):
        cfg = AgentConfig.model_validate(base_doc())
        assert cfg.schemaVersion == SCHEMA_VERSION
        assert cfg.description == ""
        assert cfg.profile == ""
        assert cfg.engine.temperature == 0.7
        assert cfg.engine.topP == 1.0
        assert cfg.engine.maxTokens == 4096
        assert cfg.engine.maxOutputBytes == 1_048_576
        assert cfg.engine.timeoutSeconds == 300
        assert cfg.engine.maxIterations == 10
        assert cfg.engine.historyMaxMessages == 200
        assert cfg.engine.historyMaxBytes == 4_194_304
        assert cfg.engine.streaming.value == "text"
        assert cfg.engine.overrides.allowTemperature is True
        assert cfg.engine.overrides.temperatureMax == 2.0
        assert cfg.engine.overrides.maxTokensMax == 8192
        assert cfg.engine.tokenBudget.perRequest == 0
        assert cfg.engine.tokenBudget.perSession == 0
        assert cfg.llm.provider.value == "gemini"
        assert cfg.llm.baseUrl == ""
        assert cfg.llm.contextWindowTokens == 0
        assert cfg.llm.vertex.enabled is False
        assert cfg.llm.vertex.location == "us-central1"
        assert cfg.llm.extra == {}
        assert cfg.tools.mcpServers == []
        assert cfg.storage.type.value == "memory"
        assert cfg.storage.sessionTtlSeconds == 86400
        assert cfg.storage.runTtlSeconds == 604800
        assert cfg.storage.maxSessions == 10000
        assert cfg.storage.maxRunsPerSession == 1000
        assert cfg.storage.maxIdempotencyRecordsPerSession == 1000
        assert cfg.storage.lockAcquireSeconds == 0.0
        assert cfg.storage.idempotencyTtlSeconds == 86400
        assert cfg.server.host == "0.0.0.0"
        assert cfg.server.port == 8080
        assert cfg.server.protocols.openaiCompat is True
        assert cfg.server.protocols.acp is False
        assert cfg.server.corsOrigins == ["*"]
        assert cfg.server.corsAllowCredentials is False
        assert cfg.server.auth.mode.value == "none"
        assert cfg.server.auth.jwt.principalClaim == "sub"
        assert cfg.server.auth.jwt.refreshSeconds == 3600
        assert cfg.server.rateLimit.enabled is False
        assert cfg.server.maxConcurrentRequests == 100
        assert cfg.server.maxRequestLineBytes == 8192
        assert cfg.server.maxHeaderBytes == 32768
        assert cfg.server.maxHeaderCount == 100
        assert cfg.server.maxRequestBytes == 1_048_576
        assert cfg.server.maxMessageBytes == 262_144
        assert cfg.server.streamQueueEvents == 64
        assert cfg.server.slowConsumerSeconds == 10
        assert cfg.server.shutdownGraceSeconds == 25
        assert cfg.k8s.enabled is False
        assert cfg.k8s.required is False
        assert cfg.k8s.namespace == "default"
        assert cfg.k8s.resyncSeconds == 300
        assert cfg.observability.logLevel.value == "INFO"
        assert cfg.observability.logFormat.value == "json"
        assert cfg.observability.includeToolArguments is False
        assert cfg.observability.otel.enabled is False
        # SCH-09 phase stubs
        assert cfg.agents == []
        assert cfg.approval.enabled is False
        assert cfg.rag.enabled is False
        assert cfg.rag.store.options == {}

    def test_schema_version_must_be_1(self):
        assert invalid(base_doc(schemaVersion=2))

    def test_name_required_and_dns1123(self):
        assert invalid({**base_doc(), "name": "BAD_NAME"})
        assert invalid({k: v for k, v in base_doc().items() if k != "name"})

    def test_engine_required_fields(self):
        assert invalid({**base_doc(), "engine": {}})
        assert invalid({**base_doc(), "engine": {"systemInstruction": ""}})

    def test_strict_types(self):
        assert invalid(base_doc(engine={"systemInstruction": "s", "temperature": "0.7"}))
        assert invalid(base_doc(engine={"systemInstruction": "s", "maxTokens": "10"}))

    def test_enum_literals_only(self):
        assert invalid(base_doc(llm={"model": "m", "provider": "GEMINI"}))
        assert invalid(base_doc(engine={"systemInstruction": "s", "streaming": "TEXT"}))

    def test_field_ranges(self):
        # passing edge values
        AgentConfig.model_validate(
            base_doc(engine={"systemInstruction": "s", "temperature": 2.0, "topP": 0.001})
        )
        AgentConfig.model_validate(
            base_doc(
                engine={
                    "systemInstruction": "s",
                    "maxTokens": 1_000_000,
                    "maxOutputBytes": 16_777_216,
                }
            )
        )
        AgentConfig.model_validate(base_doc(server={"port": 65535, "maxHeaderCount": 200}))
        # failing edges
        assert invalid(base_doc(engine={"systemInstruction": "s", "temperature": 2.01}))
        assert invalid(base_doc(engine={"systemInstruction": "s", "topP": 0.0}))
        assert invalid(base_doc(engine={"systemInstruction": "s", "maxTokens": 0}))
        assert invalid(base_doc(server={"port": 0}))
        assert invalid(base_doc(server={"maxHeaderCount": 201}))

    def test_secret_ref_non_empty(self):
        assert invalid(base_doc(llm={"model": "m", "apiKeyEnv": ""}))

    def test_passthrough_extra_accepts_any_keys(self):
        cfg = AgentConfig.model_validate(
            base_doc(llm={"model": "m", "extra": {"temperature": 1, "api_key": "x"}})
        )
        assert cfg.llm.extra["api_key"] == "x"

    def test_unknown_field_rejected(self):
        assert invalid(base_doc(bogusField=1))
        assert invalid(base_doc(engine={"systemInstruction": "s", "bogus": 1}))


class TestSchemaMeta:
    def test_schema_major_and_version(self):
        assert SCHEMA_MAJOR == 1
        assert SCHEMA_VERSION == 1

    def test_k8s_alias_is_lowercase(self):
        assert "k8s" in field_aliases(AgentConfig)

    def test_bindable_paths(self):
        fields = iter_schema_fields(AgentConfig)
        paths = dict((p, k) for p, k, _, _ in fields)
        bindable = {p for p, _, _, b in fields if b}
        assert paths["engine.systemInstruction"] == "leaf"
        assert paths["engine.overrides"] == "model"
        assert paths["tools.mcpServers"] == "list"
        assert paths["tools.mcpServers.name"] == "leaf"
        assert paths["llm.extra"] == "passthrough"
        assert paths["rag.store.options"] == "passthrough"
        # CFG-07: whole list/model/passthrough paths bindable; list-item
        # fields and passthrough keys are not.
        assert "tools.mcpServers" in bindable
        assert "tools.mcpServers.name" not in bindable
        assert "engine.systemInstruction" in bindable
        assert "llm.extra" in bindable

    def test_schema_json_roundtrip(self):
        schema = json.loads(Path("schemas/agent.schema.json").read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == (
            f"https://agentbase.dev/schemas/agent.schema.v{SCHEMA_MAJOR}.json"
        )
        props = set(schema["properties"])
        assert {"name", "engine", "llm", "k8s", "agents", "approval", "rag"} <= props
        assert set(schema["required"]) == {"name", "engine", "llm"}


class TestBundledBase:
    def test_bundled_file_resolves_like_schema_defaults(self, bundled_dir):
        res = resolve(env={}, bundled_dir=bundled_dir, argv=[])
        result = validate_resolution(res)
        assert result.ok, result.report()
        assert result.config is not None
        cfg = result.config
        assert cfg.name == "agent"
        assert cfg.engine.systemInstruction == "You are a helpful assistant."
        assert cfg.llm.provider.value == "gemini"
        assert cfg.llm.model == "gemini-2.5-flash"
        assert cfg.llm.apiKeyEnv == "GEMINI_API_KEY"
        assert cfg.engine.temperature == 0.7  # schema default preserved
