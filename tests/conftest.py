import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _keep_tests_out_of_real_outputs(tmp_path, monkeypatch):
    """Tests must never write into records/, data/runs.csv or the demo log: those files back real claims."""
    import config
    monkeypatch.setattr(config, "RECORDS", tmp_path / "records")
    monkeypatch.setattr(config, "ROOT", tmp_path)   # the app shows record paths relative to ROOT
    monkeypatch.setattr(config, "RUNS_CSV", tmp_path / "runs.csv")
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "DATA", data)          # james.py writes its demo log here
    monkeypatch.setattr(config, "OWNER_RING", data / "owner_ring.json", raising=False)   # never the real enrolled ring
    monkeypatch.setattr(config, "TALKBACK", False, raising=False)   # tests never speak out loud on the Mac


@pytest.fixture(autouse=True)
def _egress_mode_per_test():
    """app.App and the Lab switch the process to maintenance (local-only) mode; each test starts in consumer mode
    unless it sets otherwise, so one test's mode never leaks into the next."""
    from james_core import egress
    egress.set_mode("consumer")
    yield
    egress.set_mode("consumer")


@pytest.fixture(autouse=True)
def _no_neural_voice(monkeypatch):
    """Tests never load the neural voice (190 MB) or play audio; tests of it switch it back on explicitly."""
    import talk
    monkeypatch.setattr(talk, "neural_available", lambda: (False, "disabled in tests"))


@pytest.fixture(autouse=True)
def net_attempts(monkeypatch):
    """No test may use the network. Every attempted connection is refused and recorded (host, port), so a
    test can also assert what JAMES TRIED to reach (iteration 3, item E: a network-observed check)."""
    import socket
    tried = []

    def refuse(self, address, *a, **k):
        tried.append(address)
        raise OSError(f"network disabled in tests: {address}")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: (tried.append(address), 111)[1])

    def create(address, *a, **k):
        tried.append(address)
        raise OSError(f"network disabled in tests: {address}")
    monkeypatch.setattr(socket, "create_connection", create)
    yield tried
