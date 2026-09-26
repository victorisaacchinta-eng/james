"""Iteration 2 P0-C / P0-D: bound send, and one egress gate with separate tool families (ST-04, ST-05, ST-06)."""
import subprocess

import pytest

import collect
import launcher_apps as LA
import media
from agents import llm, scout
from james_core import egress


@pytest.fixture(autouse=True)
def _consumer_after():
    yield
    egress.set_mode("consumer")


def test_st06_maintenance_mode_refuses_every_consumer_web_tool(monkeypatch):
    opened = []
    monkeypatch.setattr(LA.subprocess, "Popen", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(scout.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("net")))
    egress.set_mode("maintenance")
    with pytest.raises(egress.EgressBlocked):
        LA.open_url("https://www.google.com/search?q=pump+P-3+bearing")
    with pytest.raises(egress.EgressBlocked):
        scout.search("P-3 bearing 6205 replacement")
    with pytest.raises(egress.EgressBlocked):
        collect._get("https://api.github.com/search/repositories?q=pump")
    with pytest.raises(egress.EgressBlocked):
        media.chat_send("chatgpt", "pump 3 is vibrating")
    assert opened == []
    log = egress.outbound()[-4:]
    assert all(not r["allowed"] and r["mode"] == "maintenance" for r in log)
    assert all("pump" not in str(r) for r in log)                      # the log keeps hosts and sizes, never text


def test_maintenance_app_switches_itself_to_local_only():
    import app
    app.App(sim=True)
    assert egress.mode() == "maintenance"


def test_the_model_must_be_local(monkeypatch):
    import config
    monkeypatch.setattr(config, "OLLAMA_URL", "https://example-cloud-llm.com")
    with pytest.raises(egress.EgressBlocked):
        llm._post("/api/chat", {"x": 1}, 1)
    monkeypatch.setattr(config, "OLLAMA_URL", "http://localhost:11434")
    egress.check("local_model", "http://localhost:11434/api/chat")      # fine
    for bad in ("http://localhost:8080/api/chat", "https://localhost:11434/api/chat",
                "http://127.0.0.1.evil.com:11434/api/chat", "http://localhost.evil.com:11434/"):
        with pytest.raises(egress.EgressBlocked):                        # pinned: host, port and scheme
            egress.check("local_model", bad)


def test_consumer_mode_still_opens_pages(monkeypatch):
    opened = []
    monkeypatch.setattr(LA.subprocess, "Popen", lambda *a, **k: opened.append(a[0]))
    monkeypatch.setattr(LA, "sys", type("S", (), {"platform": "darwin"}))
    LA.open_url("https://www.youtube.com/results?search_query=x", platform="darwin")
    assert opened and egress.outbound()[-1]["allowed"]


# ---- ST-04 / ST-05: no hidden send; send only into the tab holding the prepared prompt ----

def _fake_chrome(monkeypatch, tabs):
    calls = []

    def run(args, **k):
        calls.append(args)
        s = args[2]
        if "is running" in s:
            return subprocess.CompletedProcess(args, 0, "true", "")
        if "on run argv" in s:
            return subprocess.CompletedProcess(args, 0, "sent" if args[3] in tabs else "notab", "")
        return subprocess.CompletedProcess(args, 0, "".join(f"{i}\x1fhttps://chatgpt.com/c/{i}\x1f{t}\x1e"
                                                             for i, t in tabs.items()), "")
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "run", run)
    return calls


def test_st05_changed_tab_is_not_sent(monkeypatch):
    calls = _fake_chrome(monkeypatch, {"11": "some other thing the user typed"})
    ok, msg = media.chat_send("chatgpt", "Research the history of Hyderabad.")
    assert not ok and "nothing was sent" in msg and not any("on run argv" in c[2] for c in calls)


def test_st05_picks_the_tab_with_the_prompt(monkeypatch):
    calls = _fake_chrome(monkeypatch, {"11": "other", "12": "Research the history of Hyderabad."})
    assert media.chat_send("chatgpt", "Research the history of Hyderabad.")[0]
    assert calls[-1][3] == "12"


def test_st04_ask_fills_but_never_sends(monkeypatch):
    import commands as C
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    j = JM.James()
    sent = []
    monkeypatch.setattr(JM.LA, "open_url", lambda *a, **k: None)
    monkeypatch.setattr(JM.LA, "copy_text", lambda *a, **k: True)
    monkeypatch.setattr(JM.media, "chat_send", lambda *a, **k: sent.append(a) or (True, ""))
    j.run_plan(C.plan("Open ChatGPT and research on the history of Hyderabad."))
    assert sent == [] and "send it" in j.voice["steps"][-1][0]


def test_send_it_is_bound_to_the_prepared_prompt(monkeypatch):
    import time
    import commands as C
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    j = JM.James()
    sent = []
    monkeypatch.setattr(JM.LA, "open_url", lambda *a, **k: None)
    monkeypatch.setattr(JM.LA, "copy_text", lambda *a, **k: True)
    monkeypatch.setattr(JM.media, "chat_send", lambda who, prompt, wait=0.0: sent.append((who, prompt)) or (True, "ok"))
    j._before_listen = "READY"
    j.on_heard("send it", 0.9)                                         # nothing prepared yet
    time.sleep(0.05)
    assert sent == [] or sent[-1][1] == ""
    j.run_plan(C.plan("Open ChatGPT and research on the history of Hyderabad."))
    j.on_heard("send it", 0.9)
    time.sleep(0.05)
    assert sent[-1] == ("chatgpt", "Research the history of Hyderabad.")
