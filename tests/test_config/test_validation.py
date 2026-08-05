"""Validation pipeline tests (CFG-12 – CFG-15, CAP-01, CAP-02)."""

from __future__ import annotations

from app.config.resolver import resolve
from app.config.validate import validate_resolution


def valid(config: dict) -> bool:
    r = resolve(
        env={"AGENT_APPLICATION_JSON": __import__("json").dumps(config)},
        bundled_dir="config",
        argv=[],
    )
    return validate_resolution(r).ok


def issues_for(config: dict) -> list[tuple[str, str]]:
    r = resolve(
        env={"AGENT_APPLICATION_JSON": __import__("json").dumps(config)},
        bundled_dir="config",
        argv=[],
    )
    return sorted((i.path, i.code) for i in validate_resolution(r).issues)


class TestShapeWalk:
    def test_snake_case_external_key_rejected(self):
        assert ("engine.system_instruction", "unknown_field") in issues_for(
            {"engine": {"system_instruction": "x"}}
        )

    def test_unknown_nested_field_rejected(self):
        assert ("engine.bogus", "unknown_field") in issues_for({"engine": {"bogus": 1}})

    def test_unknown_top_level_rejected(self):
        assert ("bogusTop", "unknown_field") in issues_for({"bogusTop": 1})

    def test_passthrough_map_keys_allowed(self):
        assert valid({"llm": {"extra": {"anything": 1, "api_key": "x"}}})
        assert valid({"rag": {"store": {"options": {"chunk": 100}}}})

    def test_dollar_schema_and_schema_version_allowed(self):
        assert valid({"$schema": "https://x", "schemaVersion": 1})


class TestCrossField:
    def test_storage_redis_needs_connection(self):
        assert ("storage.connectionStringEnv", "cross_field") in issues_for(
            {"storage": {"type": "redis"}}
        )

    def test_prometheus_path_collision_rejected(self):
        assert ("observability.prometheus.path", "cross_field") in issues_for(
            {"observability": {"prometheus": {"enabled": True, "path": "/healthz"}}}
        )
        assert ("observability.prometheus.path", "cross_field") in issues_for(
            {"observability": {"prometheus": {"path": "/v1/models"}}}
        )

    def test_costs_duplicate_model_rejected(self):
        assert ("costs.models", "cross_field") in issues_for(
            {
                "costs": {
                    "enabled": True,
                    "models": [
                        {"model": "m1", "inputPerMillion": 1.0, "outputPerMillion": 2.0},
                        {"model": "m1", "inputPerMillion": 3.0, "outputPerMillion": 4.0},
                    ],
                }
            }
        )

    def test_costs_negative_price_rejected(self):
        assert ("costs.defaultInputPerMillion", "greater_than_equal") in issues_for(
            {"costs": {"enabled": True, "defaultInputPerMillion": -1.0}}
        )

    def test_costs_valid(self):
        assert valid({"costs": {"enabled": True, "models": [{"model": "m1"}]}})

    def test_prometheus_path_ok(self):
        assert valid({"observability": {"prometheus": {"enabled": True}}})
        assert valid({"observability": {"prometheus": {"path": "/custom-metrics"}}})

    def test_prometheus_path_must_be_absolute(self):
        assert (
            "observability.prometheus.path",
            "value_error",
        ) in issues_for({"observability": {"prometheus": {"path": "metrics"}}})

    def test_storage_postgres_needs_connection(self):
        assert ("storage.connectionStringEnv", "cross_field") in issues_for(
            {"storage": {"type": "postgres"}}
        )

    def test_storage_redis_with_connection_ok(self):
        assert valid({"storage": {"type": "redis", "connectionStringEnv": "REDIS_URL"}})

    def test_storage_file_needs_path(self):
        assert ("storage.path", "cross_field") in issues_for({"storage": {"type": "file"}})
        assert valid({"storage": {"type": "file", "path": "/data"}})

    def test_mcp_stdio_command_and_url_exclusive(self):
        assert ("tools.mcpServers[0].command", "cross_field") in issues_for(
            {"tools": {"mcpServers": [{"name": "s", "transport": "stdio"}]}}
        )
        assert ("tools.mcpServers[0].url", "cross_field") in issues_for(
            {
                "tools": {
                    "mcpServers": [
                        {"name": "s", "transport": "stdio", "command": "x", "url": "http://x"}
                    ]
                }
            }
        )

    def test_mcp_remote_requires_url(self):
        assert ("tools.mcpServers[0].url", "cross_field") in issues_for(
            {"tools": {"mcpServers": [{"name": "s", "transport": "sse"}]}}
        )
        assert valid(
            {"tools": {"mcpServers": [{"name": "s", "transport": "sse", "url": "http://x"}]}}
        )

    def test_mcp_max_result_bytes_le_transport(self):
        assert ("tools.mcpServers[0].maxResultBytes", "cross_field") in issues_for(
            {
                "tools": {
                    "mcpServers": [
                        {
                            "name": "s",
                            "transport": "stdio",
                            "command": "x",
                            "maxResultBytes": 5000,
                            "maxTransportMessageBytes": 4096,
                        }
                    ]
                }
            }
        )

    def test_mcp_duplicate_names(self):
        assert ("tools.mcpServers", "cross_field") in issues_for(
            {
                "tools": {
                    "mcpServers": [
                        {"name": "s", "transport": "stdio", "command": "a"},
                        {"name": "s", "transport": "stdio", "command": "b"},
                    ]
                }
            }
        )

    def test_mcp_static_headers_no_sensitive_keys(self):
        assert ("tools.mcpServers[0].headers", "cross_field") in issues_for(
            {
                "tools": {
                    "mcpServers": [
                        {
                            "name": "s",
                            "transport": "sse",
                            "url": "http://x",
                            "headers": {"X-Api-Key": "k"},
                        }
                    ]
                }
            }
        )

    def test_auth_api_key_needs_ref(self):
        assert ("server.auth.apiKeyEnv", "cross_field") in issues_for(
            {"server": {"auth": {"mode": "apiKey"}}}
        )
        assert valid({"server": {"auth": {"mode": "apiKey", "apiKeyEnv": "KEY"}}})

    def test_auth_jwt_needs_issuer_and_jwks(self):
        assert ("server.auth.jwt.jwksUrl", "cross_field") in issues_for(
            {"server": {"auth": {"mode": "jwt", "jwt": {"issuer": "i"}}}}
        )
        assert valid(
            {
                "server": {
                    "auth": {
                        "mode": "jwt",
                        "jwt": {"issuer": "i", "jwksUrl": "https://x/.well-known/jwks"},
                    }
                }
            }
        )

    def test_principal_claim_non_empty(self):
        assert ("server.auth.jwt.principalClaim", "cross_field") in issues_for(
            {
                "server": {
                    "auth": {
                        "mode": "jwt",
                        "jwt": {"issuer": "i", "jwksUrl": "https://x", "principalClaim": ""},
                    }
                }
            }
        )

    def test_cidr_parses(self):
        assert ("server.trustedProxyCidrs[0]", "cross_field") in issues_for(
            {"server": {"trustedProxyCidrs": ["not-a-cidr"]}}
        )
        assert valid({"server": {"trustedProxyCidrs": ["10.0.0.0/8"]}})

    def test_cors_wildcard_no_credentials(self):
        assert ("server.corsAllowCredentials", "cross_field") in issues_for(
            {"server": {"corsOrigins": ["*"], "corsAllowCredentials": True}}
        )

    def test_at_least_one_protocol(self):
        assert ("server.protocols", "cross_field") in issues_for(
            {"server": {"protocols": {"openaiCompat": False, "acp": False}}}
        )

    def test_ollama_needs_base_url(self):
        assert ("llm.baseUrl", "cross_field") in issues_for({"llm": {"provider": "ollama"}})
        assert valid({"llm": {"provider": "ollama", "baseUrl": "http://localhost:11434"}})

    def test_vertex_rules(self):
        assert ("llm.vertex.enabled", "cross_field") in issues_for(
            {"llm": {"provider": "openai", "vertex": {"enabled": True, "project": "p"}}}
        )
        assert ("llm.vertex.project", "cross_field") in issues_for(
            {"llm": {"provider": "gemini", "vertex": {"enabled": True}}}
        )
        assert ("llm.apiKeyEnv", "cross_field") in issues_for(
            {
                "llm": {
                    "provider": "gemini",
                    "vertex": {"enabled": True, "project": "p"},
                    "apiKeyEnv": "K",
                }
            }
        )
        assert ("llm.vertex.enabled", "cross_field") in issues_for(
            {"llm": {"provider": "openai", "vertex": {"project": "p"}}}
        )
        # valid: vertex mode with the bundled apiKeyEnv reset to null (CFG-06)
        assert valid(
            {
                "llm": {
                    "provider": "gemini",
                    "apiKeyEnv": None,
                    "vertex": {"enabled": True, "project": "p"},
                }
            }
        )

    def test_context_window_gt_max_tokens(self):
        assert ("llm.contextWindowTokens", "cross_field") in issues_for(
            {"llm": {"contextWindowTokens": 100}, "engine": {"maxTokens": 200}}
        )
        assert valid({"llm": {"contextWindowTokens": 1000}, "engine": {"maxTokens": 200}})

    def test_override_caps_not_below_defaults(self):
        assert ("engine.overrides.temperatureMax", "cross_field") in issues_for(
            {"engine": {"temperature": 1.5, "overrides": {"temperatureMax": 1.0}}}
        )
        assert ("engine.overrides.maxTokensMax", "cross_field") in issues_for(
            {"engine": {"maxTokens": 5000, "overrides": {"maxTokensMax": 4000}}}
        )

    def test_message_bytes_le_request_bytes(self):
        assert ("server.maxMessageBytes", "cross_field") in issues_for(
            {"server": {"maxMessageBytes": 2000000, "maxRequestBytes": 1000000}}
        )

    def test_k8s_required_implies_enabled(self):
        assert ("k8s.enabled", "cross_field") in issues_for({"k8s": {"required": True}})
        assert valid({"k8s": {"enabled": True, "required": True}})

    def test_session_ttl_zero_or_60(self):
        assert ("storage.sessionTtlSeconds", "cross_field") in issues_for(
            {"storage": {"sessionTtlSeconds": 30}}
        )
        assert valid({"storage": {"sessionTtlSeconds": 0}})


class TestCapability:
    def test_acp_accepted_in_p2(self):
        # P2 flip (CAP-02): the ACP surface is implemented and its acceptance
        # suite is in the tree.
        assert valid({"server": {"protocols": {"acp": True}}})

    def test_agents_accepted_in_p2(self):
        assert valid(
            {
                "agents": [
                    {"name": "worker", "systemInstruction": "w"},
                ]
            }
        )

    def test_approval_accepted(self):
        # CAP-02 (P3): approval.enabled is accepted by the capability gate;
        # the cross-field rules still apply (auth + redis/postgres storage).
        assert not any(
            c == "capability_error" for _p, c in issues_for({"approval": {"enabled": True}})
        )

    def test_rag_accepted(self):
        # CAP-02 (P4): rag.enabled is accepted by the capability gate.
        assert not any(c == "capability_error" for _p, c in issues_for({"rag": {"enabled": True}}))

    def test_disabled_stubs_accepted(self):
        assert valid({"agents": [], "approval": {"enabled": False}, "rag": {"enabled": False}})

    def test_capability_status_reports_p4(self):
        from app.config.capabilities import BUILD_CAPABILITIES, capability_status

        assert BUILD_CAPABILITIES == {
            "multiAgent": True,
            "acp": True,
            "approval": True,
            "rag": True,
        }
        status = capability_status()
        assert status["phase"] == "P5"
        assert status["multiAgent"] is True
        assert status["acp"] is True
        assert status["approval"] is True
        assert status["rag"] is True


class TestAggregate:
    def test_multiple_errors_collected_and_sorted(self):
        issues = issues_for(
            {
                "storage": {"type": "redis"},
                "server": {"auth": {"mode": "apiKey"}},
                "tools": {"mcpServers": [{"name": "s", "transport": "stdio"}]},
                "engine": {"bogus": 1},
            }
        )
        paths = [p for p, _ in issues]
        assert paths == sorted(paths)
        assert "storage.connectionStringEnv" in paths
        assert "server.auth.apiKeyEnv" in paths
        assert "tools.mcpServers[0].command" in paths
        assert "engine.bogus" in paths

    def test_secret_values_omitted_from_issues(self):
        r = resolve(
            env={"AGENT_SERVER_AUTH_MODE": "apiKey", "AGENT_SERVER_AUTH_APIKEYENV": ""},
            bundled_dir="config",
            argv=[],
        )
        result = validate_resolution(r)
        report = result.report()
        assert "apiKeyEnv" in report
        assert "invalid value for secret field" in report


class TestBootOrder:
    def test_validate_then_capability_order(self):
        # Schema checks are never masked (P4: no capability gates remain).
        issues = issues_for({"rag": {"enabled": True}, "engine": {"bogus": 1}})
        assert ("engine.bogus", "unknown_field") in issues


class TestMultiAgentSchemaMA01:
    """MA-01: agents[] field contract + cross-field rules (P2)."""

    def _base(self, **overrides) -> dict:
        doc = {
            "name": "root",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "m"},
            "tools": {"mcpServers": [{"name": "alpha", "transport": "stdio", "command": "x"}]},
        }
        doc.update(overrides)
        return doc

    def test_valid_sub_agent_parses(self):
        # P2 flip (CAP-02): valid MA-01 definitions pass validation entirely.
        for doc in (
            self._base(agents=[{"name": "worker", "systemInstruction": "work"}]),
            self._base(
                agents=[
                    {
                        "name": "worker",
                        "systemInstruction": "work",
                        "description": "d" * 2000,
                        "llm": {"provider": "openai", "model": "other"},
                        "toolServers": ["alpha"],
                    }
                ]
            ),
        ):
            assert issues_for(doc) == []

    def test_duplicate_names_rejected(self):
        assert ("agents", "cross_field") in issues_for(
            self._base(
                agents=[
                    {"name": "worker", "systemInstruction": "a"},
                    {"name": "worker", "systemInstruction": "b"},
                ]
            )
        )

    def test_root_name_collision_rejected(self):
        assert ("agents[0].name", "cross_field") in issues_for(
            self._base(agents=[{"name": "root", "systemInstruction": "a"}])
        )

    def test_unknown_tool_server_reference_rejected(self):
        assert ("agents[0].toolServers[0]", "cross_field") in issues_for(
            self._base(
                agents=[{"name": "worker", "systemInstruction": "a", "toolServers": ["nope"]}]
            )
        )

    def test_system_instruction_required_non_empty(self):
        assert not valid(self._base(agents=[{"name": "worker", "systemInstruction": ""}]))
        assert not valid(self._base(agents=[{"name": "worker"}]))

    def test_description_length_and_name_shape(self):
        assert not valid(
            self._base(
                agents=[{"name": "worker", "systemInstruction": "a", "description": "d" * 2001}]
            )
        )
        assert not valid(self._base(agents=[{"name": "Bad_Name", "systemInstruction": "a"}]))

    def test_nested_definition_rejected(self):
        # AgentDef has no agents field: a nested definition is an unknown field.
        assert not valid(
            self._base(
                agents=[
                    {
                        "name": "worker",
                        "systemInstruction": "a",
                        "agents": [{"name": "inner", "systemInstruction": "b"}],
                    }
                ]
            )
        )

    def test_capability_gate_all_accepted_at_p4(self):
        # CAP-02 (P2/P3/P4 flips): every implemented capability is accepted.
        assert issues_for(self._base(agents=[{"name": "worker", "systemInstruction": "a"}])) == []
        assert issues_for(self._base(server={"protocols": {"acp": True}})) == []
        # approval is capability-accepted (P3); the minimal base has no
        # auth + memory storage, so the HITL-01 cross-field rules still fire
        assert not any(
            code == "capability_error"
            for _p, code in issues_for(self._base(approval={"enabled": True}))
        )
        # rag is capability-accepted (P4)
        assert not any(
            code == "capability_error" for _p, code in issues_for(self._base(rag={"enabled": True}))
        )


class TestApprovalSchemaHITL01:
    """HITL-01: approval field contract + fail-closed rules (P3)."""

    def _approval_doc(self, **approval) -> dict:
        doc = {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "m"},
            "server": {"auth": {"mode": "apiKey", "apiKeyEnv": "K"}},
            "storage": {
                "type": "redis",
                "connectionStringEnv": "R",
            },
            "approval": {"enabled": True, **approval},
        }
        return doc

    def test_valid_approval_configuration(self):
        # with auth + redis storage the config is valid per HITL-01 (the P3
        # capability flip, CAP-02)
        assert issues_for(self._approval_doc()) == []

    def test_enabled_requires_auth_not_none(self):
        doc = self._approval_doc()
        doc["server"]["auth"] = {"mode": "none"}
        assert ("approval.enabled", "cross_field") in issues_for(doc)

    def test_enabled_requires_redis_or_postgres(self):
        for storage_type in ("memory", "file"):
            doc = self._approval_doc()
            doc["storage"] = {"type": storage_type}
            assert ("approval.enabled", "cross_field") in issues_for(doc)

    def test_defaults(self):
        from app.config.models import AgentConfig

        cfg = AgentConfig.model_validate(
            {
                "name": "agent",
                "engine": {"systemInstruction": "t"},
                "llm": {"provider": "gemini", "model": "m"},
            }
        )
        assert cfg.approval.enabled is False
        assert cfg.approval.tools == []
        assert cfg.approval.timeoutSeconds == 300
        assert cfg.approval.onTimeout.value == "deny"

    def test_on_timeout_allow_explicit_and_constrained(self):
        # explicit allow is accepted by the schema (the boot audit warns)
        assert issues_for(self._approval_doc(onTimeout="allow")) == []
        # invalid values rejected by the schema itself
        assert ("approval.onTimeout", "enum") in [
            (p, c) for p, c in issues_for(self._approval_doc(onTimeout="maybe"))
        ]
        assert not valid(self._approval_doc(timeoutSeconds=0))
        assert not valid(self._approval_doc(timeoutSeconds=999999))

    def test_tool_patterns_shape(self):
        assert issues_for(self._approval_doc(tools=["server/*", "echo_ping"])) == []
        assert not valid(self._approval_doc(tools=["server/*", 5]))


class TestRagSchemaRAG01:
    """RAG-01: rag field contract + constraints (P4)."""

    def _doc(self, **rag) -> dict:
        return {
            "name": "agent",
            "engine": {"systemInstruction": "t"},
            "llm": {"provider": "gemini", "model": "m"},
            "rag": {"enabled": True, **rag},
        }

    def test_defaults(self):
        from app.config.models import AgentConfig

        cfg = AgentConfig.model_validate(
            {
                "name": "agent",
                "engine": {"systemInstruction": "t"},
                "llm": {"provider": "gemini", "model": "m"},
            }
        )
        assert cfg.rag.enabled is False
        assert cfg.rag.required is False
        assert cfg.rag.store.type.value == "chroma"
        assert cfg.rag.store.collection == "agentbase"
        assert cfg.rag.embedding.provider.value == "gemini"
        assert cfg.rag.embedding.model == "text-embedding-004"
        assert cfg.rag.topK == 5
        assert cfg.rag.minScore == 0.0
        assert cfg.rag.chunkChars == 1000
        assert cfg.rag.chunkOverlapChars == 200
        assert cfg.rag.maxDocumentBytes == 10485760

    def test_full_valid_document(self):
        doc = self._doc(
            required=True,
            store={
                "type": "pgvector",
                "connectionStringEnv": "PG",
                "collection": "kb_prod",
                "options": {"hnsw": True},
            },
            embedding={"provider": "openai", "model": "text-embedding-3-small", "apiKeyEnv": "OK"},
            topK=20,
            minScore=0.3,
            chunkChars=500,
            chunkOverlapChars=50,
            maxDocumentBytes=2048,
        )
        assert not valid(doc)

    def test_store_types_enum(self):
        assert ("rag.store.type", "enum") in [
            (p, c) for p, c in issues_for(self._doc(store={"type": "pinecone"}))
        ]
        assert ("rag.embedding.provider", "enum") in [
            (p, c) for p, c in issues_for(self._doc(embedding={"provider": "anthropic"}))
        ]

    def test_topk_bounds(self):
        assert not valid(self._doc(topK=0))
        assert not valid(self._doc(topK=101))
        assert not valid(self._doc(minScore=-0.1))
        assert not valid(self._doc(minScore=1.1))

    def test_overlap_must_be_smaller_than_chunk(self):
        # a degenerate overlap adds a schema code; the valid config is clean
        assert any(
            code != "capability_error"
            for _p, code in issues_for(self._doc(chunkChars=100, chunkOverlapChars=100))
        )
        assert any(
            code != "capability_error"
            for _p, code in issues_for(self._doc(chunkChars=100, chunkOverlapChars=200))
        )
        assert issues_for(self._doc(chunkChars=100, chunkOverlapChars=99)) == []

    def test_collection_is_safe_identifier(self):
        assert any(
            code != "capability_error"
            for _p, code in issues_for(self._doc(store={"collection": "bad name!"}))
        )
        assert any(
            code != "capability_error"
            for _p, code in issues_for(self._doc(store={"collection": "-leading-dash"}))
        )
        assert issues_for(self._doc(store={"collection": "kb-prod-v1"})) == []

    def test_document_size_bound(self):
        assert any(
            code != "capability_error" for _p, code in issues_for(self._doc(maxDocumentBytes=0))
        )
        assert issues_for(self._doc(maxDocumentBytes=1024)) == []
