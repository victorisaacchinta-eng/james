"""Iteration 2 (voice): P0-A low-confidence confirmation, P0-B negation / questions / quotes, ST-03 partial plans.
Every sentence here is either from the live log or from the iteration 2 scenario list."""
import time

import commands as C
from agents import planner as PL
from test_talk import _james, _problems_screen


def _j(monkeypatch, installed=("Safari", "Google Chrome", "Spotify")):
    JM, j = _james(monkeypatch)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append([s.describe() for s in steps]))
    monkeypatch.setattr(JM.LA, "find_app", lambda spoken, apps: (
        next(((n, "/Applications/" + n + ".app", 0.9) for n in installed if n.lower() in spoken.lower()), None)))
    j._before_listen = "READY"
    return JM, j, ran


# ---- ST-01: low confidence -> clarification, then only a clear yes runs it ----

def test_st01_low_confidence_asks_then_yes_runs(monkeypatch):
    JM, j, ran = _j(monkeypatch)
    j.on_heard("Open the KPDR search for a go-by request.", 0.43)          # log 02:00:39
    assert ran == [] and j.pending and "Did you mean" in j.talker.said[-1]
    assert "kpdr" in j.voice["heard"].lower() and "kpdr" in j.voice["steps"][-1][2].lower()   # what was heard + what would run
    j.on_heard("yes", 0.9)
    assert ran and ran[0][-1].startswith("Search the web") and j.pending is None


def test_st01_anything_but_yes_cancels(monkeypatch):
    for reply in ("no", "cancel", "open safari", "what"):
        JM, j, ran = _j(monkeypatch)
        j.on_heard("Open the KPDR search for a go-by request.", 0.43)
        j.on_heard(reply, 0.9)
        assert not any("go-by" in " ".join(r) for r in ran), reply
        assert j.pending is None


def test_st01_confirmation_expires(monkeypatch):
    JM, j, ran = _j(monkeypatch)
    j.on_heard("Open the KPDR search for a go-by request.", 0.43)
    j.pending["until"] = time.monotonic() - 1
    j.on_heard("yes", 0.9)
    assert ran == []


def test_st01_low_confidence_problem_pick_is_confirmed_first(monkeypatch):
    JM, j = _james(monkeypatch)
    _problems_screen(JM, j)
    picked = []
    monkeypatch.setattr(JM.James, "choose", lambda self, i: picked.append(i))
    j._before_listen = "PROBLEMS"
    j.on_heard("fix problem two", 0.41)
    assert picked == [] and "problem two" in j.talker.said[-1]
    j.on_heard("yes", 0.9)
    assert picked == [1]


def test_st01_model_is_never_asked_at_low_confidence(monkeypatch):
    JM, j, ran = _j(monkeypatch)
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(JM.James, "_model_plan", lambda self, *a: called.append(a))
    j.on_heard("You have it in? You do what you do.", 0.43)                   # log 01:59:10
    assert called == [] and ran == []


# ---- ST-02: negation, questions, quotes ----

NEGATION_CASES = ["Do not send it.", "Explain how to close an app.", "Search for Close Encounters.",
                   "I said send yesterday, but do not send this."]


def test_st02_rule_plans_for_the_negation_sentences():
    got = {t: [s.kind for s in C.guard(t, C.plan(t))[0]] for t in NEGATION_CASES}
    assert got == {"Do not send it.": [], "Explain how to close an app.": [],
                   "Search for Close Encounters.": ["search"],
                   "I said send yesterday, but do not send this.": []}
    assert C.plan("Search for Close Encounters.")[0].arg == "close encounters"


def test_st02_model_cannot_add_a_forbidden_step():
    bad = [{"action": "send", "target": ""}, {"action": "close", "target": "chrome"}]
    for text in ("Do not send it.", "I said send yesterday, but do not send this.", "Never close Chrome."):
        assert [PL._step(d, text) for d in bad if PL._step(d, text)] == [], text
    assert PL._step({"action": "close", "target": "an app"}, "Explain how to close an app.") is None
    assert PL._step({"action": "close", "target": "the door"}, 'Search for "close the door"') is None
    assert PL._step({"action": "close", "target": "spotify"}, "close spotify") is not None


def test_st02_negation_replies_without_running_or_asking_the_model(monkeypatch):
    JM, j, ran = _j(monkeypatch)
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(JM.James, "_model_plan", lambda self, *a: called.append(a))
    j.on_heard("Do not send it.", 0.9)
    assert ran == [] and called == [] and "won't send" in j.talker.said[-1]


def test_st02_model_plan_is_guarded_again_in_the_app(monkeypatch):
    JM, j, ran = _j(monkeypatch)
    j.q.put(("model_plan", 0, [C.Step("send")], "model", 1.0, "", "I said send yesterday, but do not send this."))
    j.voice_step("Working out the steps")
    j.drain()
    assert ran == []


def test_titles_with_negative_words_still_play():
    assert [s.kind for s in C.guard("Play the song Don't Stop Me Now", C.plan("Play the song Don't Stop Me Now"))[0]] \
        == ["play"]


# ---- ST-03: partial plans are disclosed ----

def test_st03_unsupported_parts_are_named():
    t = "Open Wikipedia. Search for Bhopal Krishna, Chairman of East Engineering College. Find his son and return his name full name."
    steps = C.drop_answer_searches(C.plan(t))                                 # log 02:01:16
    assert [s.kind for s in steps] == ["open", "search"] and steps[0].arg == "wikipedia"
    assert C.leftovers(t, steps) == ["return his name full name"]
    t = "Open any browsers for the top 5 latest movies, Hollywood, and paste, copy and paste 6 movies on Macpad."
    assert C.leftovers(t, [C.Step("search", "top 5 latest movies hollywood", site="youtube")]) == \
        ["paste, copy and paste 6 movies on Macpad"]                        # log 02:21:23 (model dropped it)
    assert C.leftovers("Search for Close Encounters.", C.plan("Search for Close Encounters.")) == []
    assert C.leftovers("play despacito on spotify and then close chrome",
                       C.plan("play despacito on spotify and then close chrome")) == []


def test_st03_app_says_what_it_did_not_do(monkeypatch):
    JM, j, ran = _j(monkeypatch)
    j.on_heard("Open Wikipedia. Search for Bhopal Krishna. Find his son and return his full name.", 0.8)
    assert ran and any(r[1] == "fail" and r[0].startswith("Not done") for r in j.voice["steps"])


def test_trailing_comma_after_app_name():
    assert C.plan("Open WhatsApp, Search for Siri")[0].arg == "whatsapp"       # log 02:03:34
