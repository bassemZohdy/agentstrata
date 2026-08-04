"""CLI surface tests (CFG-10, CFG-10a, CFG-11, CFG-11a)."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

from app.config.cli import EX_CONFIG, EX_OK, EX_USAGE, run

from .conftest import BUNDLED_DIR

BOOTSTRAP_VARS = (
    "AGENT_PROFILE",
    "AGENT_CONFIG_DIR",
    "AGENT_BUNDLED_DIR",
    "AGENT_APPLICATION_JSON",
)


def run_cli(
    monkeypatch: pytest.MonkeyPatch,
    *argv: str,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    for var in BOOTSTRAP_VARS:
        monkeypatch.delenv(var, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = run(list(argv), bundled_dir=BUNDLED_DIR)
    return code, out.getvalue(), err.getvalue()


class TestValidate:
    def test_ok(self, monkeypatch):
        code, out, _ = run_cli(monkeypatch, "--validate")
        assert code == EX_OK
        assert out.strip() == "OK"

    def test_failure_exits_78(self, monkeypatch):
        code, _, err = run_cli(monkeypatch, "--validate", env={"AGENT_LLM_PROVIDER": "bogus"})
        assert code == EX_CONFIG
        assert "llm.provider" in err

    def test_mutually_exclusive_with_dump(self, monkeypatch):
        code, _, err = run_cli(monkeypatch, "--validate", "--dump-config")
        assert code == EX_USAGE
        assert "mutually exclusive" in err


class TestDump:
    def test_dump_output(self, monkeypatch):
        code, out, _ = run_cli(
            monkeypatch, "--dump-config", env={"AGENT_ENGINE_TEMPERATURE": "0.25"}
        )
        assert code == EX_OK
        assert "temperature: 0.25  # tier 5: env" in out
        # stdout contains only YAML (no CLI banner/provenance preamble)
        assert out.startswith("$schema:")

    def test_dump_invalid_exits_78(self, monkeypatch):
        code, _, err = run_cli(monkeypatch, "--dump-config", env={"AGENT_STORAGE_TYPE": "redis"})
        assert code == EX_CONFIG
        assert "cross_field" in err


class TestVersion:
    def test_version_fields(self, monkeypatch):
        code, out, _ = run_cli(monkeypatch, "--version")
        assert code == EX_OK
        assert "agentbase 0.1.0" in out
        assert "schema major 1" in out
        assert "phase P4" in out


class TestHelp:
    def test_help_documents_bootstrap_flags(self, monkeypatch):
        code, out, _ = run_cli(monkeypatch, "--help")
        assert code == EX_OK
        for flag in (
            "--profile",
            "--config-dir",
            "--validate",
            "--dump-config",
            "--version",
            "--help",
        ):
            assert flag in out


class TestUsageErrors:
    def test_positional_exits_64(self, monkeypatch):
        code, _, _ = run_cli(monkeypatch, "positional")
        assert code == EX_USAGE

    def test_malformed_flag_exits_64(self, monkeypatch):
        code, _, _ = run_cli(monkeypatch, "--bogus")
        assert code == EX_USAGE

    def test_unknown_dotted_path_exits_64(self, monkeypatch):
        code, _, err = run_cli(monkeypatch, "--bogus.path=1", "--validate")
        assert code == EX_USAGE
        assert "closest" in err

    def test_missing_flag_value_exits_64(self, monkeypatch):
        code, _, _ = run_cli(monkeypatch, "--profile")
        assert code == EX_USAGE
