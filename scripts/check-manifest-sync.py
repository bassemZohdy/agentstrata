#!/usr/bin/env python3
"""Verify `[project].dependencies` in pyproject.toml matches requirements.txt.

requirements.txt is the manifest of record (REQUIREMENTS.md STACK-01); the
[project] table in pyproject.toml mirrors it for tooling/IDE packaging. This
check fails on any name or version-range drift between the two, so a fix like
the STACK-02 `mcp` pin cannot silently go stale again.

Run: python scripts/check-manifest-sync.py   (exit 0 = in sync)
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(\[[^\]]*\])?\s*(.*)$")


def _parse_requirements(path: Path) -> dict[str, str]:
    deps: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_LINE.match(line)
        if not m:
            raise SystemExit(f"{path.name}: unparseable line: {line!r}")
        name, _extras, spec = m.group(1), m.group(2), m.group(3)
        deps[name.lower()] = spec.strip()
    return deps


def _parse_pyproject(path: Path) -> dict[str, str]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    deps: dict[str, str] = {}
    for entry in data["project"]["dependencies"]:
        m = _REQ_LINE.match(entry)
        if not m:
            raise SystemExit(f"{path.name}: unparseable dependency: {entry!r}")
        name, spec = m.group(1), m.group(3)
        deps[name.lower()] = spec.strip()
    return deps


def main() -> int:
    req = _parse_requirements(ROOT / "requirements.txt")
    pyr = _parse_pyproject(ROOT / "pyproject.toml")

    only_req = sorted(set(req) - set(pyr))
    only_pyr = sorted(set(pyr) - set(req))
    mismatched = sorted(
        (n for n in set(req) & set(pyr) if req[n] != pyr[n]),
        key=str,
    )

    if not (only_req or only_pyr or mismatched):
        print(f"manifest sync OK: {len(req)} dependencies match")
        return 0

    print("requirements.txt and pyproject.toml [project].dependencies DRIFTED:")
    for n in only_req:
        print(f"  only in requirements.txt: {n}{req[n]}")
    for n in only_pyr:
        print(f"  only in pyproject.toml: {n}{pyr[n]}")
    for n in mismatched:
        print(f"  range differs for {n}: requirements '{req[n]}' vs pyproject '{pyr[n]}'")
    return 1


if __name__ == "__main__":
    sys.exit(main())
