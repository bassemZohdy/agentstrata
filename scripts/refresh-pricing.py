#!/usr/bin/env python3
"""Refresh the default price catalog (E2-6, COST-01).

Fetches LiteLLM's public price JSON and rewrites the curated table in
``app/engine/pricing.py`` for the models currently in the catalog (the
set is curated on purpose — the catalog is a fallback, explicit
``costs.models`` config always wins).  Manual, requires network; CI does
NOT run this (a price change is not a code defect).

Usage::

    .venv/bin/python scripts/refresh-pricing.py [--url URL]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
PRICING = ROOT / "app" / "engine" / "pricing.py"


def _extract_current(price_file: Path) -> list[tuple[str, str, float, float]]:
    """(provider, model, in, out) from the current curated table."""
    text = price_file.read_text(encoding="utf-8")
    entries: list[tuple[str, str, float, float]] = []
    for provider, model, in_p, out_p in re.findall(
        r'\("([a-z0-9-]+)", "([^"]+)"\): \(([0-9.]+), ([0-9.]+)\)',
        text,
    ):
        entries.append((provider, model, float(in_p), float(out_p)))
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()

    current = _extract_current(PRICING)
    if not current:
        print(f"error: no catalog entries parsed from {PRICING}", file=sys.stderr)
        return 1

    try:
        with urllib.request.urlopen(args.url, timeout=30) as resp:  # noqa: S310
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001 - network/parse failure is a tool error
        print(f"error: cannot fetch/parse {args.url}: {exc}", file=sys.stderr)
        return 1

    lines = [
        '"""E2-6 (COST-01): default price catalog.',
        "",
        "Curated list prices in USD per 1M tokens, keyed by ``(provider, model)`` as configured "
        "(the connector adds the LiteLLM prefix; the catalog keys on the CONFIG values). "
        "Lookup chain in the runner: exact ``costs.models`` entry → catalog → flat "
        "``costs.default*PerMillion``.",
        "",
        "Provenance: prices snapshot from LiteLLM's public price JSON "
        "(``model_prices_and_context_window.json``) at the last refresh. Refresh with "
        "``scripts/refresh-pricing.py`` (manual, no network in CI); the catalog ages by design — "
        "refresh before release, and always prefer explicit ``costs.models`` entries for "
        "deployments that care.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "# (provider, model) -> (inputPerMillion, outputPerMillion)",
        "PRICE_CATALOG: dict[tuple[str, str], tuple[float, float]] = {",
    ]

    missing: list[tuple[str, str]] = []
    for provider, model, _old_in, _old_out in current:
        key = f"{provider}/{model}"
        entry = data.get(key)
        try:
            in_p = float(entry["input_cost_per_token"]) * 1_000_000
            out_p = float(entry["output_cost_per_token"]) * 1_000_000
        except (TypeError, ValueError, KeyError):
            missing.append((provider, model))
            continue
        lines.append(f'    ("{provider}", "{model}"): ({in_p:.4f}, {out_p:.4f}),')
    lines.append("}")
    lines.append("")
    lines.append("")
    lines.append("def catalog_price(provider: str, model: str) -> tuple[float, float] | None:")
    lines.append('    """Catalog lookup; ``None`` = miss (caller falls back to defaults)."""')
    lines.append("    return PRICE_CATALOG.get((provider, model))")
    lines.append("")

    PRICING.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"wrote {PRICING.relative_to(ROOT)} ({len(current) - len(missing)}/{len(current)} entries)"
    )
    if missing:
        print(
            "not found upstream (kept as-is? NO — omitted; add manually if still current): "
            + ", ".join(f"{p}/{m}" for p, m in missing),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
