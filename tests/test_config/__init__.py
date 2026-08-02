"""Shared fixtures for config tests."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_DIR = str(REPO_ROOT / "config")


@pytest.fixture()
def bundled_dir() -> str:
    return BUNDLED_DIR
