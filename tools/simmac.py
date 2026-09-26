"""Dev only: pytest plugin that makes the Linux VM look like macOS (sys.platform darwin, afplay present), so
platform-dependent paths are tested before the Mac run. Usage: PYTHONPATH=tools python -m pytest -q -p simmac
(Mac 04:35: 3 voice tests failed on the real Mac only, because the Mac took the afplay path.)"""
import shutil, sys, pytest
@pytest.fixture(autouse=True)
def _mac(monkeypatch):
    import urllib.request  # noqa: F401  (import before pretending to be macOS)
    monkeypatch.setattr(sys, "platform", "darwin")
    w = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/afplay" if name == "afplay" else w(name, *a, **k))
    yield
