"""Source parsing safety (REQUIREMENTS.md CFG-03a, CFG-03b).

Every file and tier-8 payload MUST be UTF-8, no larger than 1 MiB, contain
one mapping at its root, and reject duplicate mapping keys instead of
silently keeping the last value. File reads use a single immutable byte
snapshot so a concurrent mount update cannot produce a partially parsed
document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .models import MAX_SOURCE_BYTES


class SourceError(ValueError):
    """A configuration error naming the source (CFG-03a: exit 78 at boot)."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys (CFG-03a)."""


def _construct_mapping_no_dupes(
    loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_no_dupes
)


def _check_root(data: Any, source: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SourceError(source, "root must be a single mapping object")
    return data


def parse_yaml_bytes(raw: bytes, source: str) -> dict[str, Any]:
    if len(raw) > MAX_SOURCE_BYTES:
        raise SourceError(source, f"file exceeds the {MAX_SOURCE_BYTES}-byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceError(source, "not valid UTF-8") from exc
    try:
        # SafeLoader (subclassed to reject duplicate keys) constructs only
        # standard YAML scalar/mapping/sequence types — never arbitrary
        # Python objects — so this is a safe load (CFG-03a mandates safe mode).
        loader = _UniqueKeySafeLoader(text)
        try:
            data = loader.get_single_data()
        finally:
            loader.dispose()
    except yaml.YAMLError as exc:
        raise SourceError(source, f"YAML parse failure: {exc}") from exc
    return _check_root(data, source)


def parse_json_bytes(raw: bytes, source: str) -> dict[str, Any]:
    if len(raw) > MAX_SOURCE_BYTES:
        raise SourceError(source, f"payload exceeds the {MAX_SOURCE_BYTES}-byte limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceError(source, "not valid UTF-8") from exc
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise SourceError(source, f"JSON parse failure: {exc}") from exc
    except ValueError as exc:  # duplicate-key hook
        raise SourceError(source, f"JSON parse failure: {exc}") from exc
    return _check_root(data, source)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def parse_json_text(text: str, source: str) -> dict[str, Any]:
    """Parse an inline JSON payload (CFG-03a applies to tier 6/8 as well)."""
    raw = text.encode("utf-8")
    if len(raw) > MAX_SOURCE_BYTES:
        raise SourceError(source, f"payload exceeds the {MAX_SOURCE_BYTES}-byte limit")
    try:
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise SourceError(source, f"JSON parse failure: {exc}") from exc
    except ValueError as exc:  # duplicate-key hook
        raise SourceError(source, f"JSON parse failure: {exc}") from exc
    return _check_root(data, source)


def parse_json_value(text: str, source: str) -> Any:
    """Parse a JSON value (env/CLI binding for list/model/passthrough paths).

    Unlike files and tier-8 payloads (CFG-03a), a value bound to a list path
    legitimately has an array root; duplicate keys inside objects are still
    rejected.
    """
    raw = text.encode("utf-8")
    if len(raw) > MAX_SOURCE_BYTES:
        raise SourceError(source, f"payload exceeds the {MAX_SOURCE_BYTES}-byte limit")
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise SourceError(source, f"JSON parse failure: {exc}") from exc
    except ValueError as exc:  # duplicate-key hook
        raise SourceError(source, f"JSON parse failure: {exc}") from exc


def read_source_bytes(path: Path, source: str) -> bytes:
    """Immutable byte snapshot of a file (CFG-03b)."""
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SourceError(source, f"unreadable: {exc}") from exc


def parse_file(path: Path) -> dict[str, Any]:
    """Parse a config file by extension using a single byte snapshot."""
    source = str(path)
    raw = read_source_bytes(path, source)
    suffix = path.suffix.lower()
    if suffix == ".json":
        return parse_json_bytes(raw, source)
    if suffix in (".yaml", ".yml"):
        return parse_yaml_bytes(raw, source)
    raise SourceError(source, f"unsupported config file extension {suffix!r}")
