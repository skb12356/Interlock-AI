"""Skeleton smoke test: the package imports and the layout the plan mandates exists.

Guards D1-J1.1. Would fail if the repository tree were flattened or a
work-stream package were deleted.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The layout frozen in Implementation01 "Repository layout (create this on Day 1, hour 1)".
EXPECTED_PACKAGES = [
    "interlock.core",
    "interlock.gateway",
    "interlock.gate",
    "interlock.interlock_tools",
    "interlock.ledger",
    "interlock.observer",
    "interlock.signals",
    "interlock.risk",
    "interlock.lanec",
    "interlock.eval",
]


@pytest.mark.parametrize("module", EXPECTED_PACKAGES)
def test_package_importable(module: str) -> None:
    assert importlib.import_module(module) is not None


@pytest.mark.parametrize("directory", ["policies", "migrations", "scripts", "corpus", "docs"])
def test_support_directory_exists(directory: str) -> None:
    assert (REPO_ROOT / directory).is_dir()


def test_secrets_are_gitignored() -> None:
    """CLAUDE.md §9: never commit secrets, keys, or tenant canary strings."""
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "*.key", "canaries.local.json"):
        assert pattern in ignored
