import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import pytest

from pii_platform.config import Settings
from pii_platform.pipeline import build_pipeline


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env(data_dir=tmp_path / "data")


@pytest.fixture
def pipe(settings):
    return build_pipeline(settings)
