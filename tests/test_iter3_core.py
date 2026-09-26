"""Iteration 3, sections 4A to 4E: confirmation scope, departure policy, FOREMAN decision origins and budgets,
and transport-level egress (redirects, pinned model endpoint, what the maintenance app actually tried to reach)."""
import time

import pytest

import commands as C
from agents import llm
from james_core import egress
from test_talk import _james


def _j(monkeypatch):
    JM, j = _james(monkeypatch)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append([s.describe() for s in steps]))
    monkeypatch.setattr(JM.LA, "find_app", lambda spoken, apps: ("Safari", "/Applications/Safari.app", 0.9)
                        if "safari" in spoken.lower() else None)
    j._before_listen = "READY"
    j.s.mode = "READY"
    return JM, j, ran


# ---- 4A: every uncertain action is confirmed; the yes is scoped ----

@pytest.mark.parametrize("text", ["Open safari.", "Play Despacito on Spotify.", "Next track.", "Identify this."])
def test_low_confidence_valid_app_or_media_still_asks(monkeypatch, text):
    JM, j, ran = _j(monkeypatch)
    j.on_heard(text, 0.45)
    assert ran == [] and j.pending


def test_pause_is_the_only_immediate_exception():
    assert C.immediate_ok([C.Step("media", "pause")]) and not C.immediate_ok([C.Step("media", "next")])
    assert not C.immediate_ok([C.Step("open", "safari")]) and not C.immediate_ok([])


@pytest.mark.parametrize("reply,conf", [("yes, but do not run it", 0.9), ("yes", 0.3),
                                        ("I may have misheard, sir. Did you mean opening Safari? Say yes or no.", 0.9)])
def test_only_a_clear_yes_confirms(monkeypatch, reply, conf):
    JM, j, ran = _j(monkeypatch)
    j.on_heard("Open safari.", 0.45)
    j.on_heard(reply, conf)                        # the app's own prompt, heard back, is not a yes
    assert ran == [] and j.pending is None


def test_yes_from_another_screen_or_owner_is_refused(monkeypatch):
    JM, j, ran = _j(monkeypatch)
    j.set_owner("owner", 1)
    j.on_heard("Open safari.", 0.45)
    j.owner_epoch = 2                              # a different hand now has control (no handover event seen)
    j.on_heard("yes", 0.9)
    assert ran == []
    j.set_owner("owner", 2)
    j.on_heard("Open safari.", 0.45)
    j._before_listen = "PROBLEMS"                  # the yes arrives on another screen
    j.on_heard("yes", 0.9)
    assert ran == []


def test_a_new_task_voids_the_pending_yes(monkeypatch):
    JM, j, ran = _j(monkeypatch)
    monkeypatch.setattr(JM.James, "identify", lambda self, frame: None)
    j.on_heard("Open safari.", 0.45)
    j.press("identify")
    assert j.pending is None


# ---- 4I: stepping away does not silently run the command ----

def test_departure_needs_a_yes(monkeypatch):
    JM, j, ran = _j(monkeypatch)
    j._departed = True
    j.on_heard("Open safari.", 0.9)
    assert ran == [] and j.pending and "stepped away" in j.talker.said[-1]
    j.on_heard("yes", 0.9)
    assert ran == [["Open safari"]]


def test_departure_flag_is_set_by_the_hand_leaving(monkeypatch):
    JM, j, ran = _j(monkeypatch)

    class Rec:
        active, stats = True, None

        def stop(self):
            import numpy as np
            self.active = False
            return np.zeros(1600, np.float32)
    j.rec = Rec()
    monkeypatch.setattr(JM.James, "_transcribe", lambda self, audio: None)
    j.voice = {"state": "listening", "t0": time.monotonic(), "steps": [], "via": "gesture"}
    j.stop_listen(run=True, departed=True)
    assert j._departed


# ---- 4B, 4D: who decided each round; the whole loop is bounded ----

@pytest.fixture
def manual():
    from agents.page import ManualIndex
    return ManualIndex(use_embeddings=False)


def _foreman(manual, sensors=True):
    import yaml
    import config
    from agents.foreman import Foreman
    from james_core.ledger import Ledger
    from james_core.safety_guard import load_policy
    led = Ledger(":memory:")
    for jb in yaml.safe_load(config.SEEDED_JOBS.read_text())["jobs"]:
        led.add_job(jb["job_id"], jb["asset_id"], jb["symptoms"], jb["fix"], jb["approved_by"], cause=jb["cause"])
    return Foreman(manual, led, load_policy(), use_sensors=sensors)


def _intent(text="Pump 3 is vibrating more than usual"):
    from agents.echo import parse_intent
    return parse_intent(text, 0.9, "P-3")[0]


def _model(monkeypatch, replies):
    monkeypatch.setattr(llm, "available", lambda *a, **k: True)
    monkeypatch.setattr(llm, "embed", lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("x")))
    it = iter(replies)

    def fake(system, *a, **k):
        if "FOREMAN" in system:
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r
        raise llm.LLMUnavailable("only FOREMAN is faked")
    monkeypatch.setattr(llm, "chat_json", fake)


def test_origins_and_totals_when_the_model_decides(monkeypatch, manual):
    _model(monkeypatch, [{"option": "finish_bearing_wear", "why": "manual + sensor agree"}])
    p = _foreman(manual).plan(_intent())
    rows = [r for r in p.trace if r.round > 0]
    assert [r.origin for r in rows] == ["rules", "model"] and [r.model_tried for r in rows] == [False, True]
    assert p.decisions == {"model_attempts": 1, "model_accepted": 1, "rules": 1, "rules_fallback": 0}
    assert p.stop_reason == "concluded" and rows[0].evidence                  # the check added evidence


@pytest.mark.parametrize("reply,words", [({"option": "finish_misalignment", "why": "made up"}, "not one of the valid"),
                                         (llm.LLMUnavailable("timeout"), "timeout")])
def test_fallback_is_labelled_with_its_reason(monkeypatch, manual, reply, words):
    _model(monkeypatch, [reply])
    p = _foreman(manual).plan(_intent())
    last = [r for r in p.trace if r.round > 0][-1]
    assert last.origin == "rules-fallback" and words in last.fallback
    assert p.decisions["model_accepted"] == 0 and p.decisions["rules_fallback"] == 1
    assert p.cause == "bearing_wear"


def test_live_trace_0405_abstain_is_not_offered_while_evidence_steps_remain(monkeypatch, manual):
    """Live trace 04:05: qwen3-vl:4b chose 'abstain' before checking the evidence (after the technician said no,
    and before any narrower search). Abstain is now offered only when no evidence step is left."""
    seen = []

    def fake(system, user, *a, **k):
        if "FOREMAN" in system:
            seen.append(user)
            return {"option": "abstain", "why": "quit"}
        raise llm.LLMUnavailable("x")
    monkeypatch.setattr(llm, "available", lambda *a, **k: True)
    monkeypatch.setattr(llm, "embed", lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("x")))
    monkeypatch.setattr(llm, "chat_json", fake)
    p = _foreman(manual, sensors=False).plan(_intent(), {"bearing_hot": False})
    rows = [r for r in p.trace if r.round > 0]
    assert [r.action for r in rows[:2]] == ["check misalignment", "check bearing wear"]   # evidence first
    assert "abstain:" not in seen[0]                          # while checks remained, quitting was not an option
    assert rows[2].action == "abstain" and "abstain:" in seen[-1]  # after the evidence, the model may still stop,
    assert "a supported cause was available" in rows[2].result     # and the record says it stopped anyway
    p = _foreman(manual).plan(_intent("Pump 3 is leaking"))
    acts = [r.action.split(":")[0] for r in p.trace if r.round > 0]
    assert acts == ["search 2", "search 3", "abstain"]       # the narrower searches happen first


def test_model_off_is_plain_rules(monkeypatch, manual):
    monkeypatch.setattr(llm, "available", lambda *a, **k: False)
    p = _foreman(manual).plan(_intent())
    assert {r.origin for r in p.trace if r.round > 0} == {"rules"} and p.decisions["model_attempts"] == 0


def test_time_budget_and_cancel_stop_the_loop(monkeypatch, manual):
    monkeypatch.setattr(llm, "available", lambda *a, **k: False)
    f = _foreman(manual)
    f.TIME_BUDGET_S = -1
    p = f.plan(_intent())
    assert p.stop_reason == "time_budget" and p.status == "no_match" and p.steps == []
    f = _foreman(manual)
    real = f._observe

    def cancel_then_observe(*a):
        f.cancel.set()
        return real(*a)
    monkeypatch.setattr(f, "_observe", cancel_then_observe)
    p = f.plan(_intent())
    assert p.stop_reason == "cancelled" and p.steps == []


def test_tool_budget_forces_a_stop(monkeypatch, manual):
    monkeypatch.setattr(llm, "available", lambda *a, **k: False)
    f = _foreman(manual)
    f.MAX_TOOL_CALLS = 0
    p = f.plan(_intent("Pump 3 is leaking"))
    assert p.stop_reason.startswith("abstained") and "tool budget" in p.stop_reason


def test_round_budget(monkeypatch, manual):
    monkeypatch.setattr(llm, "available", lambda *a, **k: False)
    f = _foreman(manual)
    f.MAX_ROUNDS = 1
    p = f.plan(_intent())
    assert p.stop_reason == "round_budget" and p.steps == []


def test_repair_record_shows_decision_counts():
    from app import App
    a = App(sim=True)
    a.toggle_lock(); a.target("P-3", "asset_tag", 0.97); a.pinch()
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    rec = a.ledger.repair_record(a.session.id)
    assert "stopped: concluded · model decisions accepted 0 of 0 asked" in rec


def test_result_for_an_ended_job_is_ignored():
    from app import App
    a = App(sim=True)
    a.toggle_lock(); a.target("P-3", "asset_tag", 0.97); a.pinch()
    a.q.put(("heard", "Pump 3 is vibrating more than usual", 0.95, time.perf_counter(), "TYPED")); a.drain()
    old_plan, old = a.plan, a.session.id
    a.new_session(); a.toggle_lock()
    a.q.put(("plan", old_plan, old)); a.drain()
    assert a.plan is None and any(e["kind"] == "investigation_ignored" for e in a.ledger.events(old))


# ---- 4E: transport ----

def test_redirects_are_checked(monkeypatch):
    import urllib.request
    h = egress._CheckedRedirect("consumer_web")
    req = urllib.request.Request("https://www.youtube.com/results?search_query=x")
    with pytest.raises(egress.EgressBlocked):
        h.redirect_request(req, None, 302, "Found", {}, "http://localhost:11434/api/tags")
    egress.set_mode("maintenance")
    h = egress._CheckedRedirect("local_model")
    with pytest.raises(egress.EgressBlocked):
        h.redirect_request(urllib.request.Request("http://localhost:11434/api/tags"), None, 302, "Found", {},
                           "https://example.com/steal")


def test_hosts_are_parsed_not_substring_matched():
    for url in ("https://localhost.example.com/", "http://127.0.0.1@evil.com/"):
        egress.check("consumer_web", url)                       # consumer: a normal outside site, allowed
    with pytest.raises(egress.EgressBlocked):
        egress.check("consumer_web", "http://127.0.0.1:8080/admin")   # consumer tools never reach local services


def test_maintenance_run_only_ever_tries_the_local_model(monkeypatch, net_attempts):
    """Network-observed: the full sim investigation with the model 'up' (so every model path is exercised);
    every connection attempt is recorded by conftest and none may leave localhost:11434."""
    from tools.maintenance_trace import run
    monkeypatch.setattr(llm, "_status", {"checked": time.time(), "ok": True, "models": ["qwen3-vl:4b"]})
    run()
    assert egress.mode() == "maintenance"
    assert net_attempts, "the model path was not exercised"
    assert {(h, p) for h, p, *_ in net_attempts} <= {("localhost", 11434), ("127.0.0.1", 11434), ("::1", 11434)}


# ---- 4F: the send contract ----

def _chrome(monkeypatch, listing, click="sent"):
    import subprocess
    import media
    calls = []

    def run(args, **k):
        calls.append(args)
        s = args[2]
        if "is running" in s:
            return subprocess.CompletedProcess(args, 0, "true", "")
        if "on run argv" in s:
            return subprocess.CompletedProcess(args, 0, click, "")
        return subprocess.CompletedProcess(args, 0, listing, "")
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "run", run)
    return calls


def _tab(i, url, text):
    return f"{i}\x1f{url}\x1f{text}\x1e"


P = "Research the history of Hyderabad."


def test_send_needs_the_exact_chat_host(monkeypatch):
    import media
    calls = _chrome(monkeypatch, _tab(5, "https://chatgpt.com.evil.example/c/1", P) + _tab(6, "http://chatgpt.com/", P))
    ok, msg = media.chat_send("chatgpt", P)
    assert not ok and not any("on run argv" in c[2] for c in calls)


def test_two_tabs_with_the_same_prompt_abort(monkeypatch):
    import media
    calls = _chrome(monkeypatch, _tab(5, "https://chatgpt.com/c/1", P) + _tab(6, "https://chatgpt.com/c/2", P))
    ok, msg = media.chat_send("chatgpt", P)
    assert not ok and "two chat tabs" in msg and not any("on run argv" in c[2] for c in calls)


@pytest.mark.parametrize("click,words", [("moved", "another page"), ("changed", "text in the box changed"),
                                         ("notab", "closed")])
def test_change_between_read_and_click_sends_nothing_and_is_not_retried(monkeypatch, click, words):
    import media
    calls = _chrome(monkeypatch, _tab(5, "https://chatgpt.com/c/1", P), click=click)
    ok, msg = media.chat_send("chatgpt", P, wait_s=2.0)
    assert not ok and words in msg
    assert sum("on run argv" in c[2] for c in calls) == 1                   # one click attempt, no retries


def test_click_script_rechecks_url_and_text_from_argv_only(monkeypatch):
    import media
    calls = _chrome(monkeypatch, _tab(5, "https://chatgpt.com/c/1", P + " \" & do shell script \"x"))
    media.chat_send("chatgpt", P)
    click = [c for c in calls if "on run argv" in c[2]][-1]
    assert "(item 2 of argv)" in click[2] and "(item 3 of argv)" in click[2]
    assert "do shell script" not in click[2] and click[5].endswith('do shell script "x')
