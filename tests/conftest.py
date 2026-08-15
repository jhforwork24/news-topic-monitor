from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def topics_path() -> Path:
    return Path(__file__).parents[1] / "config" / "topics.yml"
