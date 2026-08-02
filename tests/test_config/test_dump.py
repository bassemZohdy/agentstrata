"""Canonical dump tests (CFG-11, NFR-05 determinism)."""

from __future__ import annotations

import itertools

from app.config.dump import dump_config
from app.config.resolver import resolve
from app.config.validate import validate_resolution


def dump_for(env: dict[str, str] | None = None, argv: list[str] | None = None) -> str:
    res = resolve(env=env or {}, bundled_dir="config", argv=argv or [])
    result = validate_resolution(res)
    assert result.ok, result.report()
    assert result.config is not None
    return dump_config(res, result.config)


class TestCanonical:
    def test_comments_and_order(self):
        out = dump_for(env={"AGENT_LLM_MODEL": "m", "AGENT_ENGINE_TEMPERATURE": "0.25"})
        # schema field order: top-level fields come before engine details
        assert out.index("name:") < out.index("engine:")
        assert out.index("engine:") < out.index("llm:")
        # provenance comments
        assert "temperature: 0.25  # tier 5: env" in out
        assert "model: " in out and "# tier 5: env" in out
        # default labels present
        assert "# default" in out

    def test_reset_labeled(self):
        out = dump_for(env={"AGENT_SERVER_PORT": "null"})
        assert "port: 8080  # tier 5: env (reset-to-default)" in out

    def test_derived_labeled(self):
        out = dump_for()
        assert "k8s:" in out
        assert "derived from top-level name" in out

    def test_secrets_masked(self):
        out = dump_for(env={"AGENT_LLM_APIKEYENV": "GEMINI_API_KEY"})
        assert "GEMINI_API_KEY" not in out
        assert 'apiKeyEnv: "***"' in out

    def test_passthrough_keys_sorted(self):
        out = dump_for(env={"AGENT_LLM_EXTRA": '{"z": 1, "a": 2}'})
        za = out.index("a:")
        zz = out.index("z:")
        assert za < zz

    def test_utf8_lf_single_final_newline(self):
        out = dump_for(env={"AGENT_ENGINE_SYSTEMINSTRUCTION": "héllo"})
        assert out.endswith("\n")
        assert "\r" not in out
        assert "héllo" in out

    def test_no_timestamps(self):
        out = dump_for()
        assert "2026" not in out

    def test_cli_flag_comment(self):
        out = dump_for(argv=["--engine.temperature=0.5"])
        assert "temperature: 0.5  # tier 7: cli" in out


class TestNfr05:
    def test_permuted_env_order_byte_identical(self):
        env = {
            "AGENT_ENGINE_SYSTEM_INSTRUCTION": "Be brief.",
            "AGENT_LLM_MODEL": "gpt-4o-mini",
            "AGENT_LLM_PROVIDER": "openai",
            "AGENT_SERVER_PORT": "9090",
            "AGENT_STORAGE_TYPE": "file",
            "AGENT_STORAGE_PATH": "/data",
            "AGENT_TOOLS_MCPSERVERS": (
                '[{"name":"echo","transport":"stdio","command":"/bin/echo",'
                '"maxTransportMessageBytes":4096}]'
            ),
            "AGENT_ENGINE_TEMPERATURE": "0.31",
            "AGENT_SERVER_MAXCONCURRENTREQUESTS": "150",
        }
        outputs = set()
        for perm in itertools.islice(itertools.permutations(list(env.items())), 0, 60):
            outputs.add(dump_for(env=dict(perm)))
        assert len(outputs) == 1, "permuted env order produced different dumps (NFR-05 violated)"

    def test_identical_inputs_byte_identical(self):
        a = dump_for(env={"AGENT_LLM_MODEL": "m"})
        b = dump_for(env={"AGENT_LLM_MODEL": "m"})
        assert a == b

    def test_cli_argv_order_identical(self):
        a = dump_for(argv=["--engine.temperature=0.5", "--server.port=1234"])
        b = dump_for(argv=["--server.port=1234", "--engine.temperature=0.5"])
        assert a == b
