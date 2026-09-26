import pytest

from james_core.egress import CloudDisabled, EgressGuard


def test_disabled_by_default_and_audited():
    log = []
    g = EgressGuard(audit=log.append)
    with pytest.raises(CloudDisabled):
        g.prepare({"symptoms": ["vibration"]})
    assert log[0]["event"] == "cloud_refused"


def test_enabled_sends_only_allowlisted_redacted_text():
    log = []
    g = EgressGuard(enabled=True, allowed_fields={"symptoms", "excerpts", "image"}, audit=log.append)
    out = g.prepare({"symptoms": ["vibration"], "excerpts": ["call ravi@plant.in or 9876543210, EMP-4411"],
                     "image": b"...", "audio": b"...", "raw_log": "..."})
    assert set(out) == {"symptoms", "excerpts"}
    assert out["excerpts"] == ["call [email] or [phone], [employee-id]"]
    assert log[-1]["event"] == "cloud_request" and "image" in log[-1]["dropped"]
