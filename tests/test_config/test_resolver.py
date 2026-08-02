"""Resolver tests (CFG-01 – CFG-11a: tiers, merge, env, CLI, provenance)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.config.resolver import (
    ConfigError,
    UsageError,
    camel_to_env_alias,
    resolve,
)


def resolve_with(
    files: dict[str, str], env: dict[str, str] | None = None, argv: list[str] | None = None
):
    tmp = Path(tempfile.mkdtemp())
    for name, content in files.items():
        (tmp / name).write_text(content, encoding="utf-8")
    return resolve(env=env or {}, argv=argv or [], bundled_dir=str(tmp))


def write_yaml(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")


class TestTiers:
    def test_tier1_bundled_base(self, bundled_dir):
        r = resolve(env={}, bundled_dir=bundled_dir, argv=[])
        assert r.data["name"] == "agent"
        assert r.provenance["name"].tier == 1

    def test_tier2_bundled_profile(self, bundled_dir):
        r = resolve(env={"AGENT_PROFILE": "test"}, bundled_dir=bundled_dir, argv=[])
        assert r.data["server"]["port"] == 9091
        assert r.provenance["server.port"].tier == 2

    def test_tier3_mounted_base_first_match_and_sibling_warning(self, tmp_path):
        write_yaml(tmp_path, "agent.yaml", "name: from-yaml")
        write_yaml(tmp_path, "agent.yml", "name: from-yml")
        write_yaml(tmp_path, "config.yaml", "name: from-config")
        r = resolve(env={}, bundled_dir="config", cli_config_dir=str(tmp_path), argv=[])
        assert r.data["name"] == "from-yaml"
        assert any("ignoring siblings" in w for w in r.warnings)

    def test_tier3_candidate_order(self, tmp_path):
        write_yaml(tmp_path, "config.yaml", "name: from-config")
        r = resolve(env={}, bundled_dir="config", cli_config_dir=str(tmp_path), argv=[])
        assert r.data["name"] == "from-config"

    def test_tier4_mounted_profile(self, tmp_path):
        write_yaml(tmp_path, "agent-prod.yaml", "name: prod")
        r = resolve(
            env={"AGENT_PROFILE": "prod"},
            bundled_dir="config",
            cli_config_dir=str(tmp_path),
            argv=[],
        )
        assert r.data["name"] == "prod"
        assert r.provenance["name"].tier == 4

    def test_precedence_env_beats_files(self, tmp_path):
        write_yaml(tmp_path, "agent.yaml", "name: file")
        r = resolve(env={"AGENT_NAME": "env"}, bundled_dir=tmp_path, argv=[])
        assert r.data["name"] == "env"
        assert r.provenance["name"].tier == 5

    def test_precedence_cli_beats_env(self, tmp_path):
        write_yaml(tmp_path, "agent.yaml", "name: file")
        r = resolve(env={"AGENT_NAME": "env"}, bundled_dir=tmp_path, argv=["--name=cli"])
        assert r.data["name"] == "cli"
        assert r.provenance["name"].tier == 7

    def test_tier6_inline_json(self):
        r = resolve(
            env={"AGENT_APPLICATION_JSON": '{"name": "inline", "server": {"port": 7777}}'},
            bundled_dir="config",
            argv=[],
        )
        assert r.data["name"] == "inline"
        assert r.provenance["name"].tier == 6

    def test_tier6_invalid_json_fatal(self):
        with pytest.raises(ConfigError):
            resolve(env={"AGENT_APPLICATION_JSON": "{not json"}, bundled_dir="config", argv=[])


class TestMerge:
    def test_recursive_merge(self, tmp_path):
        write_yaml(tmp_path, "agent.yaml", "server:\n  port: 1111\n  host: 0.0.0.0\n")
        r = resolve(env={"AGENT_SERVER_PORT": "2222"}, bundled_dir=tmp_path, argv=[])
        assert r.data["server"]["port"] == 2222
        assert r.data["server"]["host"] == "0.0.0.0"

    def test_list_wholesale_replacement(self, tmp_path):
        write_yaml(
            tmp_path,
            "agent.yaml",
            "tools:\n  mcpServers:\n    - name: a\n      transport: stdio\n      command: x\n",
        )
        r = resolve(
            env={"AGENT_TOOLS_MCPSERVERS": '[{"name":"b","transport":"sse","url":"http://y"}]'},
            bundled_dir=tmp_path,
            argv=[],
        )
        assert [s["name"] for s in r.data["tools"]["mcpServers"]] == ["b"]

    def test_null_reset_requests_default(self, tmp_path):
        write_yaml(tmp_path, "agent.yaml", "server:\n  port: 9999\n")
        r = resolve(env={"AGENT_SERVER_PORT": "null"}, bundled_dir=tmp_path, argv=[])
        assert "port" not in r.data["server"]
        assert r.provenance["server.port"].reset
        assert r.provenance["server.port"].tier == 5

    def test_null_reset_list(self, tmp_path):
        write_yaml(
            tmp_path,
            "agent.yaml",
            "tools:\n  mcpServers:\n    - name: a\n      transport: stdio\n      command: x\n",
        )
        r = resolve(env={"AGENT_TOOLS_MCPSERVERS": "null"}, bundled_dir=tmp_path, argv=[])
        assert "mcpServers" not in r.data["tools"]

    def test_provenance_for_every_set_leaf(self, tmp_path):
        write_yaml(tmp_path, "agent.yaml", "name: a\nserver:\n  port: 5\n")
        r = resolve(env={"AGENT_LLM_MODEL": "m"}, bundled_dir=tmp_path, argv=[])
        assert r.provenance["name"].tier == 1
        assert r.provenance["server.port"].tier == 1
        assert r.provenance["llm.model"].tier == 5
        assert r.provenance["k8s.name"].source == "derived from top-level name"


class TestEnvBinding:
    def test_canonical_alias(self):
        assert camel_to_env_alias("engine.systemInstruction") == "AGENT_ENGINE_SYSTEM_INSTRUCTION"
        assert camel_to_env_alias("llm.model") == "AGENT_LLM_MODEL"

    def test_alias_case_and_underscore_insensitive(self):
        r = resolve(env={"agent_engine_systeminstruction": "s"}, bundled_dir="config", argv=[])
        assert r.data["engine"]["systemInstruction"] == "s"

    def test_enum_binding(self):
        r = resolve(env={"AGENT_LLM_PROVIDER": "ollama"}, bundled_dir="config", argv=[])
        assert r.data["llm"]["provider"].value == "ollama"

    def test_invalid_bool_fatal(self):
        with pytest.raises(ConfigError, match="invalid boolean"):
            resolve(env={"AGENT_K8S_ENABLED": "yes"}, bundled_dir="config", argv=[])

    def test_invalid_int_fatal(self):
        with pytest.raises(ConfigError, match="invalid integer"):
            resolve(env={"AGENT_SERVER_PORT": "abc"}, bundled_dir="config", argv=[])

    def test_unmatched_variable_warns_with_closest(self):
        r = resolve(env={"AGENT_LLM_MODL": "x"}, bundled_dir="config", argv=[])
        assert any("AGENT_LLM_MODL" in w and "closest" in w for w in r.warnings)

    def test_unmatched_sensitive_var_value_not_logged(self):
        r = resolve(env={"AGENT_API_KEY": "supersecret"}, bundled_dir="config", argv=[])
        assert not any("supersecret" in w for w in r.warnings)
        assert any("AGENT_API_KEY" in w for w in r.warnings)

    def test_reserved_vars_not_warned(self):
        r = resolve(
            env={
                "AGENT_PROFILE": "x",
                "AGENT_CONFIG_DIR": "/etc/agent",
                "AGENT_APPLICATION_JSON": '{"name":"a"}',
            },
            bundled_dir="config",
            argv=[],
        )
        assert not any("AGENT_PROFILE" in w or "AGENT_CONFIG_DIR" in w for w in r.warnings)


class TestCliFlags:
    def test_cli_scalar(self):
        r = resolve(env={}, bundled_dir="config", argv=["--engine.temperature=0.3"])
        assert r.data["engine"]["temperature"] == 0.3
        assert r.provenance["engine.temperature"].tier == 7

    def test_cli_json_value(self):
        r = resolve(
            env={},
            bundled_dir="config",
            argv=['--tools.mcpServers=[{"name":"s","transport":"stdio","command":"x"}]'],
        )
        assert r.data["tools"]["mcpServers"][0]["name"] == "s"

    def test_cli_last_occurrence_wins_with_warning(self):
        r = resolve(
            env={},
            bundled_dir="config",
            argv=["--engine.temperature=0.1", "--engine.temperature=0.9"],
        )
        assert r.data["engine"]["temperature"] == 0.9
        assert any("more than once" in w for w in r.warnings)

    def test_cli_unknown_path_is_usage_error(self):
        with pytest.raises(UsageError):
            resolve(env={}, bundled_dir="config", argv=["--bogus.path=1"])

    def test_cli_list_item_path_not_bindable(self):
        with pytest.raises(UsageError):
            resolve(env={}, bundled_dir="config", argv=["--tools.mcpServers.name=x"])


class TestHttpAlias:
    def test_http_normalized_to_streamable_http(self):
        r = resolve(
            env={"AGENT_TOOLS_MCPSERVERS": '[{"name":"s","transport":"http","url":"http://x"}]'},
            bundled_dir="config",
            argv=[],
        )
        assert r.data["tools"]["mcpServers"][0]["transport"] == "streamable-http"
        assert any("http" in w and "streamable-http" in w for w in r.warnings)


class TestBootstrap:
    def test_config_dir_must_be_absolute(self):
        with pytest.raises(ConfigError, match="absolute"):
            resolve(env={"AGENT_CONFIG_DIR": "relative"}, bundled_dir="config", argv=[])

    def test_invalid_profile_rejected(self):
        with pytest.raises(ConfigError, match="invalid profile"):
            resolve(env={"AGENT_PROFILE": "../etc"}, bundled_dir="config", argv=[])

    def test_cli_profile_wins_over_env(self):
        r = resolve(
            env={"AGENT_PROFILE": "test"},
            bundled_dir="config",
            argv=[],
            cli_profile="",
        )
        assert r.profile == ""
