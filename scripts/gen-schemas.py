#!/usr/bin/env python3
"""Generate ``schemas/agent.schema.json`` (REQUIREMENTS.md SCH-02, DEL-02).

Generated artifacts are never hand-edited. CI regenerates and zero-diffs:
    .venv/bin/python scripts/gen-schemas.py && git diff --exit-code -- schemas/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config.models import SCHEMA_MAJOR, AgentConfig  # noqa: E402

SCHEMA_ID = f"https://agentstrata.dev/schemas/agent.schema.v{SCHEMA_MAJOR}.json"


def build_schema() -> dict:
    schema = AgentConfig.model_json_schema(by_alias=True)
    schema["$id"] = SCHEMA_ID
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "AgentStrata Agent Definition"
    return schema


def main() -> int:
    out = ROOT / "schemas" / "agent.schema.json"
    data = build_schema()
    out.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
