"""Source parsing safety tests (CFG-03a, CFG-03b)."""

from __future__ import annotations

import pytest

from app.config import parse
from app.config.models import MAX_SOURCE_BYTES


class TestDuplicateKeys:
    def test_yaml_duplicate_keys_rejected(self):
        with pytest.raises(parse.SourceError, match="duplicate key"):
            parse.parse_yaml_bytes(b"name: a\nname: b\n", "t")

    def test_json_duplicate_keys_rejected(self):
        with pytest.raises(parse.SourceError, match="duplicate key"):
            parse.parse_json_bytes(b'{"name": "a", "name": "b"}', "t")

    def test_json_value_duplicate_keys_rejected(self):
        with pytest.raises(parse.SourceError, match="duplicate key"):
            parse.parse_json_value('{"a": 1, "a": 2}', "t")


class TestBoundsAndEncoding:
    def test_utf8_invalid_rejected(self):
        with pytest.raises(parse.SourceError, match="UTF-8"):
            parse.parse_yaml_bytes(b"name: \xff\xfe\n", "t")

    def test_too_large_rejected(self):
        with pytest.raises(parse.SourceError, match="limit"):
            parse.parse_yaml_bytes(b" " * (MAX_SOURCE_BYTES + 1), "t")

    def test_json_too_large_rejected(self):
        with pytest.raises(parse.SourceError, match="limit"):
            parse.parse_json_value("[" + "1," * (MAX_SOURCE_BYTES // 2) + "]", "t")


class TestRootShape:
    def test_non_mapping_root_rejected_yaml(self):
        with pytest.raises(parse.SourceError, match="mapping"):
            parse.parse_yaml_bytes(b"- 1\n- 2\n", "t")

    def test_non_mapping_root_rejected_json(self):
        with pytest.raises(parse.SourceError, match="mapping"):
            parse.parse_json_bytes(b"[1,2]", "t")

    def test_scalar_root_rejected(self):
        with pytest.raises(parse.SourceError, match="mapping"):
            parse.parse_yaml_bytes(b"hello\n", "t")

    def test_array_root_ok_for_json_value(self):
        # env/CLI values bound to list paths legitimately have array roots.
        assert parse.parse_json_value("[1,2]", "t") == [1, 2]


class TestFileParsing:
    def test_parse_file_by_extension(self, tmp_path):
        y = tmp_path / "agent.yaml"
        y.write_text("name: x\n", encoding="utf-8")
        assert parse.parse_file(y) == {"name": "x"}
        j = tmp_path / "agent.json"
        j.write_text('{"name": "x"}', encoding="utf-8")
        assert parse.parse_file(j) == {"name": "x"}

    def test_parse_file_unsupported_extension(self, tmp_path):
        f = tmp_path / "agent.toml"
        f.write_text("x = 1", encoding="utf-8")
        with pytest.raises(parse.SourceError, match="extension"):
            parse.parse_file(f)

    def test_parse_file_missing(self, tmp_path):
        with pytest.raises(parse.SourceError, match="unreadable"):
            parse.parse_file(tmp_path / "nope.yaml")
