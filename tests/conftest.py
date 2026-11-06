"""Shared fixtures. Creates the example database once per test session."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import setup_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def example_database():
    """Recreate test.db so tests never depend on a stale or missing fixture."""
    setup_db.create_database()
    yield setup_db.DB_PATH
