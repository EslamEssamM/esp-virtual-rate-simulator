import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.pipeline import run_pipeline  # noqa: E402


@pytest.fixture(scope="session")
def res():
    """Full pipeline on GC31 (uses the parquet cache when present)."""
    return run_pipeline("GC31")


@pytest.fixture(scope="session")
def mel():
    """Full pipeline on the Meleiha field."""
    return run_pipeline("Meleiha")
