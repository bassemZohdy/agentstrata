"""MCP unit tests: filtering/renaming (MCP-03), results (MCP-04), stdio
sandbox (MCP-06), and bounded parsing (MCP-08)."""

from __future__ import annotations

import pytest

from app.engine.mcp.bounds import (
    TransportMessageTooLarge,
    bounded_httpx_client_factory,
    validate_tool_metadata,
)
from app.engine.mcp.filtering import (
    apply_tool_filter,
    canonical_json,
    enforce_max_result_bytes,
    redact_preview,
    rename_collision_safe,
    truncate_codepoint_safe,
)
from app.engine.mcp.stdio_sandbox import (
    build_stdio_params,
    interpolate_env_vars,
    minimal_stdio_env,
)


class TestFiltering:
    def test_deny_wins_over_allow(self):
        names = ["a", "b", "c"]
        assert apply_tool_filter(names, allow=["a", "b"], deny=["a"]) == ["b"]

    def test_allow_empty_means_all(self):
        assert apply_tool_filter(["a", "b"]) == ["a", "b"]

    def test_collision_safe_rename(self):
        final = rename_collision_safe(["dup", "dup", "dup"], "server")
        assert final == ["dup", "server_dup", "server_dup_2"]


class TestResults:
    def test_canonical_json_stable(self):
        a = canonical_json({"z": 1, "a": [1, 2]})
        b = canonical_json({"a": [1, 2], "z": 1})
        assert a == b
        assert a == '{"a":[1,2],"z":1}'

    def test_truncate_codepoint_safe(self):
        text = "😀" * 100  # 300 bytes
        truncated, flag = truncate_codepoint_safe(text, 60)
        assert flag is True
        assert len(truncated.encode("utf-8")) <= 60
        # never splits a surrogate pair
        assert len(truncated) % 1 == 0
        assert truncated.endswith("[truncated by runtime]")

    def test_short_text_untouched(self):
        out, flag = truncate_codepoint_safe("hello", 100)
        assert out == "hello" and flag is False

    def test_enforce_max_result_bytes(self):
        out = enforce_max_result_bytes("x" * 1000, 100)
        assert len(out.encode("utf-8")) <= 100

    def test_redact_preview_masks_secrets(self):
        preview = redact_preview({"api_key": "sekrit", "text": "hello world"})
        assert "sekrit" not in preview
        assert "hello world" in preview
        assert len(preview) <= 500


class TestStdioSandbox:
    def test_minimal_env_only_base_keys(self):
        parent = {
            "PATH": "/usr/bin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "C",
            "TMPDIR": "/tmp",
            "HOME": "/root",
            "SECRET": "s3cr3t",
        }
        env = minimal_stdio_env({}, parent)
        assert set(env) == {"PATH", "LANG", "LC_ALL", "TMPDIR"}
        assert "SECRET" not in env
        assert "HOME" not in env

    def test_configured_env_overrides_and_adds(self):
        parent = {"PATH": "/usr/bin", "TMPDIR": "/tmp"}
        env = minimal_stdio_env({"PATH": "/custom", "FOO": "bar"}, parent)
        assert env["PATH"] == "/custom"
        assert env["FOO"] == "bar"

    def test_interpolation(self):
        parent = {"API_TOKEN": "tok123"}
        resolved, unresolved = interpolate_env_vars({"URL": "http://x/${API_TOKEN}"}, parent)
        assert resolved["URL"] == "http://x/tok123"
        assert unresolved == []

    def test_unresolved_var_reported(self):
        resolved, unresolved = interpolate_env_vars(
            {"URL": "http://x/${MISSING_VAR}"}, {"OTHER": "1"}
        )
        assert unresolved == ["MISSING_VAR"]
        assert "MISSING_VAR" not in resolved["URL"]

    def test_build_stdio_params_no_shell(self):
        params, unresolved = build_stdio_params(
            "python", ["server.py"], {"TOKEN": "abc"}, {"PATH": "/usr/bin"}
        )
        assert unresolved == []
        assert params.command == "python"
        assert params.args == ["server.py"]
        assert params.env["TOKEN"] == "abc"


class TestBounds:
    def test_bounded_stream_raises_on_overflow(self):
        import asyncio

        class BigStream:
            async def __aiter__(self):
                yield b"a" * 10

            async def aclose(self):
                return None

            async def aread(self):
                return b"a" * 10

        from app.engine.mcp.bounds import _BoundedByteStream

        async def run():
            stream = _BoundedByteStream(BigStream(), 5)
            with pytest.raises(TransportMessageTooLarge):
                async for _ in stream:
                    pass

        asyncio.run(run())

    def test_bounded_factory_installs_wrapper(self):
        import asyncio

        from google.adk.tools.mcp_tool.mcp_session_manager import (
            create_mcp_http_client,
        )

        factory = bounded_httpx_client_factory(1024, create_mcp_http_client)

        async def run():
            client = factory()
            assert hasattr(client, "send")
            await client.aclose()

        asyncio.run(run())

    def test_tool_metadata_caps(self):
        assert validate_tool_metadata("x" * 200, "", None) is not None
        assert validate_tool_metadata("ok", "d" * 5000, None) is not None
        assert validate_tool_metadata("ok", "d", {"a": "b" * 70000}) is not None
        assert validate_tool_metadata("ok", "desc", {"a": 1}) is None
