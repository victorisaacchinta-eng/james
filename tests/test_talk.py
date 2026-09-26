"""Talkback: JAMES speaks (British voice via macOS `say`), chats, narrates commands and reads the screen,
and you can walk through a fix by voice. Runs without audio: `say` and the model are mocked."""
import subprocess
import sys
import time
import types

import pytest

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

import commands as C             # noqa: E402
import talk as T                 # noqa: E402
from agents import problems as PR  # noqa: E402

SAY_V = """Albert              en_US    # Hello! My name is Albert.
Daniel              en_GB    # Hello! My name is Daniel.
Daniel (Enhanced)   en_GB    # Hello! My name is Daniel.
Kate                en_GB    # Hello! My name is Kate.
Jamie (Premium)     en_GB    # Hello! My name is Jamie.
Rishi               en_IN    # Hello! My name is Rishi.
"""


# ---------- the voice ----------

def test_best_british_voice_is_picked(monkeypatch):
    monkeypatch.setattr(T.sys, "platform", "darwin")
    monkeypatch.setattr(T.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, SAY_V, ""))
    v = T.installed_voices()
    assert v["Daniel (Enhanced)"] == "en_GB" and v["Jamie (Premium)"] == "en_GB"
    assert T.pick_voice(v) == "Daniel (Enhanced)"                          # male voices first (iteration 3)
    assert T.pick_voice({"Daniel": "en_GB", "Albert": "en_US"}) == "Daniel"
    assert T.pick_voice({"Kate": "en_GB"}) == "Kate" and T.pick_voice({"Albert": "en_US"}) == ""
    assert T.pick_voice(v, "Rishi") == "Rishi"                                  # config.TALK_VOICE wins


def test_clean_text_for_speech():
    assert T.clean("See https://github.com/a/b · 5 stars & *bold*") == "See the link , 5 stars and bold"
    assert len(T.clean("word " * 400)) <= 602 and T.clean("-rf is not an option") == "rf is not an option"


def test_text_goes_to_say_on_stdin_never_as_arguments(monkeypatch):
    monkeypatch.setattr(T.sys, "platform", "darwin")
    monkeypatch.setattr(T, "installed_voices", lambda: {"Daniel": "en_GB"})
    calls = []

    class P:
        def __init__(self, args, **k):
            calls.append((args, k))
            self.rc = None

        def communicate(self, data, timeout=None):
            calls.append(("stdin", data))
            self.rc = 0

        def poll(self):
            return self.rc

        def terminate(self):
            self.rc = -15
    monkeypatch.setattr(T.subprocess, "Popen", P)
    t = T.Talker(True, "", 185)
    t.say('Opening "Asphalt"; rm -rf ~')
    for _ in range(100):
        if len(calls) >= 2:
            break
        time.sleep(0.01)
    args, kw = calls[0]
    assert args == ["say", "-r", "185", "-v", "Daniel"] and kw["stdin"] == subprocess.PIPE
    assert calls[1] == ("stdin", b'Opening "Asphalt"; rm -rf ~')
    t.toggle_mute()
    t.say("hello")
    assert t.q.empty()                                                          # muted: nothing queued


def test_off_the_mac_it_is_silent_but_logs(monkeypatch):
    monkeypatch.setattr(T.sys, "platform", "linux")
    lines = []
    t = T.Talker(True, log=lines.append)
    assert not t.enabled
    t.say("Hello, sir.")
    assert lines and lines[0].startswith("SAY 'Hello, sir.'")


# ---------- what JAMES says ----------

@pytest.mark.parametrize("said,starts", [
    ("hi", "Hello, sir"), ("Hi James", "Hello, sir"), ("Hey James, how are you?", "Running smoothly"),
    ("how are you doing", "Running smoothly"), ("thank you", "My pleasure"), ("what's happening", "STATUS"),
    ("What's on the screen?", "STATUS"), ("who are you", "I'm JAMES"), ("goodbye", "Goodbye")])
def test_smalltalk(said, starts):
    assert T.smalltalk(said, "STATUS").startswith(starts)


@pytest.mark.parametrize("said", ["open chrome", "hello there, open chrome", "play despacito", "fix problem two",
                                  "how do I fix the front glass", "what is this"])
def test_commands_are_not_smalltalk(said):
    assert T.smalltalk(said, "STATUS") is None


def test_narrate_says_what_it_is_about_to_do():
    s = T.narrate(C.plan("Open Chrome. Go to YouTube and play Believer.") + C.plan("close asphalt"), "sir")
    assert s.split(".")[0] in ("Right away, sir", "Certainly, sir", "Very good, sir", "Of course, sir")
    assert "Playing believer on YouTube" in s and "closing asphalt" in s
    m = T.narrate([C.Step("message", "hi", "instagram", to="sam")])
    assert "Sam" in m and "You press send" in m
    c = T.narrate(C.plan("Open GitHub and search for RAG and copy all the repository links to the Notes app"))
    assert "collecting the top github results for rag into your notes" in c.lower()


def test_identified_reads_the_problems():
    probs = PR.merge("smartphone", [{"title": "Cracked screen", "sign": "cracks", "seen": True}], "cracked screen")
    s = T.identified("smartphone", "cracked screen", probs)
    assert s.startswith("That appears to be a smartphone. I can see cracked screen.")
    assert "one, Cracked screen, which I can see; two," in s and "fix problem one" in s
    assert "an object" in T.identified("object", "no visible damage", probs) and "can't see any damage" in \
        T.identified("object", "no visible damage", probs)


def test_fix_intro_and_steps():
    fix = PR.catalog_fix("smartphone", "Battery drains fast or is swollen")
    fix["cause"] = "Batteries wear out with charge cycles"
    s = T.fix_intro({"title": "Battery"}, fix)
    assert "Batteries wear out" in s and "A word of caution: A swollen battery" in s and "There are four steps." in s
    assert "Step one: Check battery health." in s and "Say next step" in s
    assert "I have no steps" in T.fix_intro({"title": "X"}, {"steps": []})


@pytest.mark.parametrize("said,want", [("next step", ("next",)), ("next", ("next",)), ("previous step", ("prev",)),
                                       ("read step three", ("step", 3)), ("step 2", ("step", 2)), ("the fourth step", ("step", 4)),
                                       ("repeat that", ("repeat",)), ("read all the steps", ("all",)),
                                       ("what is the nature of the problem", ("nature",)), ("James, next step please", ("next",)),
                                       ("open chrome", None)])
def test_nav(said, want):
    assert T.nav(T.drop_name(said)) == want


# ---------- in JAMES ----------

class FakeTalker:
    def __init__(self):
        self.said, self.stops, self.muted, self.last, self.enabled, self.voice = [], 0, False, "", True, "Daniel"

    def say(self, text, interrupt=False):
        self.last = T.clean(text)
        if not self.muted:
            self.said.append(self.last)

    def stop(self):
        self.stops += 1

    def toggle_mute(self):
        self.muted = not self.muted
        return self.muted

    def set_muted(self, m):
        self.muted = bool(m)
        return self.muted

    def describe(self):
        return "fake"


def _james(monkeypatch):
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    j = JM.James()
    j.talker = FakeTalker()
    return JM, j


def _problems_screen(JM, j):
    j.s.mode, j.s.t_identify = "IDENTIFYING", time.time()
    r = {"object": "smartphone", "brand": None, "model": None, "model_confidence": 0, "problem": "no visible damage",
         "problem_confidence": 1, "common_problems": [], "part_needed": None, "fix": []}
    r["problems"] = PR.merge("smartphone", [], r["problem"])
    j.q.put(("identified", r))
    j.drain()


def test_chat_replies_without_running_anything(monkeypatch):
    JM, j = _james(monkeypatch)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append(steps))
    j._before_listen = "READY"
    j.on_heard("Hey James, how are you doing?", 0.8)
    assert j.talker.said[-1].startswith("Running smoothly") and not ran
    j.on_heard("what's happening", 0.8)
    assert j.talker.said[-1].startswith("All quiet, sir")


def test_commands_are_narrated(monkeypatch):
    JM, j = _james(monkeypatch)
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: None)
    j._before_listen = "READY"
    j.on_heard("open terminal on my macbook", 0.9)
    assert j.talker.said[-1].endswith("Opening terminal.") and j.voice["said"] == j.talker.said[-1]


def test_identify_is_read_out_and_fix_problem_two_picks_it(monkeypatch):
    JM, j = _james(monkeypatch)
    _problems_screen(JM, j)
    assert j.talker.said[-1].startswith("That appears to be a smartphone. I can't see any damage.")
    picked = []
    monkeypatch.setattr(JM.James, "_fix", lambda self, i, obj, name, prob: picked.append(prob["title"]))
    j._before_listen = "PROBLEMS"
    j.on_heard("fix problem two", 0.8)                        # not "open an app called problem two"
    time.sleep(0.05)
    assert picked == ["Battery drains fast or is swollen"] and j.s.mode == "FIXING"
    assert j.talker.said[-1].startswith("Battery drains fast or is swollen. The usual sign: ")


def test_walk_through_the_fix_by_voice(monkeypatch):
    JM, j = _james(monkeypatch)
    _problems_screen(JM, j)
    j.s.mode, j.s.choice = "FIXING", 1
    fix = dict(PR.catalog_fix("smartphone", "Battery drains fast or is swollen"), source="catalog")
    j.q.put(("fix", 1, fix))
    j.drain()
    assert "Step one: Check battery health." in j.talker.said[-1]
    j._before_listen = "FIXES"
    j.on_heard("next step", 0.8)
    assert j.talker.said[-1].startswith("Step two: Find the apps using power") and j.s.expanded == 1
    j.on_heard("read step four", 0.8)
    assert j.talker.said[-1].startswith("Step four: Replace the battery")
    j.on_heard("next step", 0.8)
    assert j.talker.said[-1].startswith("That was the last step.")
    j.on_heard("read step nine", 0.8)
    assert j.talker.said[-1] == "There are only four steps."
    j.on_heard("what is the nature of the problem", 0.8)
    assert "The usual sign is needs charging" in j.talker.said[-1] and "can't confirm it" in j.talker.said[-1]
    j.on_heard("repeat that", 0.8)
    assert j.talker.said[-1] == j.talker.said[-2]
    j.press("step:2")                                              # pinching a step open reads it too
    assert j.talker.said[-1].startswith("Step three:")


def test_model_conversation_reply(monkeypatch):
    JM, j = _james(monkeypatch)
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: True)
    monkeypatch.setattr(JM.PL, "plan_full", lambda t, *a: ([], "I can't check the weather from here, sir."))
    j._before_listen = "READY"
    j.on_heard("do you think it is going to rain later today", 0.8)
    for _ in range(100):
        if not j.q.empty():
            break
        time.sleep(0.01)
    j.drain()
    assert j.talker.said[-1] == "I can't check the weather from here, sir."


def test_failures_are_spoken_and_listening_stops_speech(monkeypatch):
    JM, j = _james(monkeypatch)
    i = j.voice_step("Close asphalt")
    j.set_step(i, "fail", "No app called asphalt to close")
    assert j.talker.said[-1] == "I'm afraid no app called asphalt to close."
    monkeypatch.setattr(j.rec, "start", lambda: None)
    j.start_listen("gesture")
    assert j.talker.stops == 1


def test_mute_by_voice(monkeypatch):
    JM, j = _james(monkeypatch)
    j._before_listen = "READY"
    j.on_heard("be quiet", 0.9)
    assert j.talker.muted
    j.on_heard("talk to me", 0.9)
    assert not j.talker.muted and j.talker.said[-1] == "I'm here, sir."


def test_tests_never_speak():
    import config
    assert config.TALKBACK is False                                   # conftest turns the voice off for every test


def test_no_trademark_names_in_talk_ui():
    from pathlib import Path
    t = (Path(T.__file__)).read_text().lower()
    assert "iron man" not in t and "marvel" not in t
