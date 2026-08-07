#!/usr/bin/env python3
"""Generate the committed env-var reference (REQUIREMENTS.md CFG-17).
Never hand-edit generated files; CI regenerates and zero-diffs:

    .venv/bin/python scripts/gen-env-reference.py && git diff --exit-code -- docs/env-reference.md
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config.env_catalog import render_catalog  # noqa: E402

OUT = ROOT / "docs" / "env-reference.md"


def main() -> None:
    OUT.write_text(render_catalog(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(render_catalog().splitlines())} lines)")


if __name__ == "__main__":
    main()
