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

    def test_approval_forbidden(self):
        assert ("approval.enabled", "capability_error") in issues_for(
            {"approval": {"enabled": True}}
        )

    def test_rag_forbidden(self):
        assert ("rag.enabled", "capability_error") in issues_for({"rag": {"enabled": True}})

    def test_disabled_stubs_accepted(self):
        assert valid({"agents": [], "approval": {"enabled": False}, "rag": {"enabled": False}})

    def test_capability_status_reports_p2(self):
        from app.config.capabilities import BUILD_CAPABILITIES, capability_status

        assert BUILD_CAPABILITIES == {
            "multiAgent": True,
            "acp": True,
            "approval": False,
            "rag": False,
        }
        status = capability_status()
        assert status["phase"] == "P2"
        assert status["multiAgent"] is True
        assert status["acp"] is True
        assert status["approval"] is False
        assert status["rag"] is False


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
        # A config with BOTH a schema error and a capability error must report
        # both, with capability checks not masking schema checks (P2: acp is
        # accepted; approval still gates).
        issues = issues_for({"approval": {"enabled": True}, "engine": {"bogus": 1}})
        codes = dict(issues)
        assert codes["approval.enabled"] == "capability_error"
        assert codes["engine.bogus"] == "unknown_field"


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

    def test_capability_gate_fail_closed_for_p3_p4(self):
        # CAP-01: P3/P4 capabilities stay fail-closed; multi-agent and ACP are
        # accepted (the P2 flip, CAP-02).
        assert issues_for(self._base(agents=[{"name": "worker", "systemInstruction": "a"}])) == []
        assert issues_for(self._base(server={"protocols": {"acp": True}})) == []
        assert ("approval.enabled", "capability_error") in issues_for(
            self._base(approval={"enabled": True})
        )
        assert ("rag.enabled", "capability_error") in issues_for(self._base(rag={"enabled": True}))
