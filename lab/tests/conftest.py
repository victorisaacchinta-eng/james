import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture
def lab(tmp_path):
    from lab.service import Lab
    return Lab(tmp_path / "lab.db")


@pytest.fixture
def client(lab):
    from fastapi.testclient import TestClient
    from lab.api import create_app
    return TestClient(create_app(lab))
