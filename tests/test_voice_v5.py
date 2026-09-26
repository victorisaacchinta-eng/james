"""Voice v5: the 01:18 to 01:20 log, the complexity router (simple -> rules, complex -> local model) and
pressing send in ChatGPT / Claude."""
import subprocess
import sys
import time
import types

import pytest

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

import commands as C             # noqa: E402
import media                     # noqa: E402
from agents import llm, planner as PL   # noqa: E402


def one(t):
    s = C.plan(t)
    assert len(s) == 1, s
    return s[0]


# ---------- the log ----------

def test_open_comma_claude_is_an_ask():                       # 01:19:03 opened the app + a Google search
    st = one("Open, Claude and search about Superman versus Batman.")
    assert (st.kind, st.service) == ("ask", "claude") and "Superman versus Batman" in st.arg


def test_claude_will_search():                                 # 01:19:11 not understood
    st = one("Then Claude will search about Superman versus Batman.")
    assert (st.kind, st.service) == ("ask", "claude") and st.arg.startswith("Search about Superman")


def test_in_the_web_version_of_chatgpt():                      # 01:19:35 not understood
    st = one("In the web version of ChatGPT, search about Batman versus Superman.")
    assert (st.kind, st.service) == ("ask", "chatgpt") and "Batman versus Superman" in st.arg


def test_multi_sentence_chatgpt_prompt_is_clean_and_in_chrome():   # 01:19:59 prompt was ". Then search for ..."
    st = one("Open Chrome. Search for ChatGPT. Open ChatGPT. Then search for Batman, Superman.")
    assert (st.kind, st.service, st.browser) == ("ask", "chatgpt", "Google Chrome")
    assert st.arg == "Search for Batman, Superman."


@pytest.mark.parametrize("said,who", [("Click Enter on the web version of ChatGPT.", "chatgpt"),   # 01:20:13
                                      ("send it", ""), ("press enter", ""), ("hit enter on claude", "claude"),
                                      ("send the prompt to claude", "claude")])
def test_send(said, who):
    st = one(said)
    assert (st.kind, st.service) == ("send", who)


@pytest.mark.parametrize("said", ["enter the matrix", "send an email to mom", "open chatgpt"])
def test_not_send(said):
    assert all(s.kind != "send" for s in C.plan(said))


# ---------- complexity router ----------

@pytest.mark.parametrize("said", ["open terminal on my macbook", "Play Despacito on Spotify.", "pause", "open chatgpt",
                                  "Spouse the video that is playing on YouTube.", "Hey James, can you open Asphalt 8 on my laptop?",
                                  "Open Chrome and Google about World War II and fine good documentaries.",
                                  "Do I fix the front class of Realme 15 Pro 5G? The front class is broken. Search it on Google Chrome.",
                                  "I want you to open terminal and then search for how to install python on a mac with homebrew",
                                  "Open YouTube and play Paradise Songs.", "search github for yolo",
                                  "Open ChatGPT and research on the World War II and write 500 words. Keep it simple. Use headings."])
def test_simple_stays_on_rules(said):
    assert C.complexity(said, C.plan(said)) == ""


@pytest.mark.parametrize("said", [
    "so I was thinking, put on some lofi and also look up the best pump seal kits",                 # nothing matched
    "Open Chrome. Go to YouTube. Find a video about Arduino. Then open GitHub.",                       # 4 sentences
    "open the thing I use for drawing diagrams and the notes app too please",                          # open target = a request
    "open the calculator app and also after that I really need you to find me a nice python course that has homework, "
    "quizzes and a certificate at the end"])                                                           # 25+ words
def test_complex_goes_to_the_model(said):
    assert C.complexity(said, C.plan(said)) != ""


def test_noise_is_not_complex():
    assert C.complexity("uh", []) == ""


# ---------- the model's plan is validated ----------

def test_planner_validates_and_tidies(monkeypatch):
    monkeypatch.setattr(PL.llm, "chat_json", lambda *a, **k: {"steps": [
        {"action": "open", "target": "Google Chrome", "service": "", "browser": ""},
        {"action": "ask", "target": ". then search for batman vs superman", "service": "chatgpt", "browser": ""},
        {"action": "run_shell", "target": "rm -rf ~", "service": "", "browser": ""},              # not an action: dropped
        {"action": "media", "target": "format disk", "service": "", "browser": ""},              # bad media: dropped
        {"action": "open", "target": "a very long sentence that is not an app name at all", "service": "", "browser": ""}]})
    steps = PL.plan("Open Chrome. Search for ChatGPT. Open ChatGPT. Then search for Batman, Superman.")
    assert [(s.kind, s.service, s.browser) for s in steps] == [("ask", "chatgpt", "Google Chrome")]
    assert steps[0].arg == "Search for batman vs superman"


def test_planner_down_raises(monkeypatch):
    def down(*a, **k):
        raise llm.LLMUnavailable("down")
    monkeypatch.setattr(PL.llm, "chat_json", down)
    with pytest.raises(llm.LLMUnavailable):
        PL.plan("anything at all here")


def _james(monkeypatch, model_up):
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: model_up)
    return JM, JM.James()


def _drain_until(j, kind, secs=2.0):
    t = time.time()
    while time.time() - t < secs:
        if not j.q.empty():
            return
        time.sleep(0.01)


def test_complex_request_runs_the_model_plan(monkeypatch):
    JM, j = _james(monkeypatch, True)
    ran = []
    monkeypatch.setattr(JM.PL, "plan_full", lambda t, *a: ([C.Step("play", "lofi", "youtube"),
                                                            C.Step("search", "pump seal kits", site="amazon")], ""))
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append([s.describe() for s in steps]))
    j._before_listen = "READY"
    j.on_heard("so I was thinking, put on some lofi and also look up the best pump seal kits on amazon", 0.8)  # a site must be named (live 04:22:06)
    assert j.voice["steps"][0][0] == "Working out the steps" and not ran
    _drain_until(j, "model_plan")
    j.drain()
    assert ran == [['Play "lofi" on YouTube', 'Search Amazon for "pump seal kits"']]


def test_simple_request_skips_the_model(monkeypatch):
    JM, j = _james(monkeypatch, True)
    called, ran = [], []
    monkeypatch.setattr(JM.PL, "plan_full", lambda t, *a: called.append(t) or ([], ""))
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append(steps))
    j._before_listen = "READY"
    j.on_heard("Play Despacito on Spotify.", 0.9)
    assert not called and ran and ran[0][0].service == "spotify"


def test_model_down_falls_back_to_the_rules(monkeypatch):
    JM, j = _james(monkeypatch, False)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append(steps))
    j._before_listen = "READY"
    j.on_heard("I want you to open terminal and then search for how to install python on a mac with homebrew fast", 0.9)
    assert ran and [s.kind for s in ran[0]] == ["open", "search"]


def test_problem_number_is_never_sent_to_the_model(monkeypatch):
    JM, j = _james(monkeypatch, True)
    called = []
    monkeypatch.setattr(JM.PL, "plan_full", lambda t, *a: called.append(t) or ([], ""))
    monkeypatch.setattr(JM.James, "choose", lambda self, i: None)
    j.s.problems = [{"title": "a"}, {"title": "b"}]
    j.s.result = {"object": "phone"}
    j._before_listen = "PROBLEMS"
    j.on_heard("problem two", 0.9)
    assert not called


# ---------- press send ----------

def test_send_script_is_fixed(monkeypatch):
    """Iteration 2 P0-C: send is bound to the tab holding the prepared prompt; the prompt never enters a script."""
    calls = []
    prompt = 'Batman versus Superman" & do shell script "rm -rf ~'

    def fake_run(args, **k):
        calls.append(args)
        s = args[2]
        out = "true" if "is running" in s else ("sent" if "on run argv" in s else
                                                 f"7\x1fhttps://chatgpt.com/c/1\x1f{prompt}\x1e")
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    assert media.chat_send("chatgpt", prompt) == (True, "sent in ChatGPT")
    scripts = [c[2] for c in calls]
    assert all("rm -rf" not in x and "Batman" not in x for x in scripts)
    assert calls[-1][3:] == ["7", "https://chatgpt.com/c/1", prompt] and "composer-submit-button" in scripts[-1]
    assert media.chat_send("perplexity", "x")[0] is False                   # JAMES never presses send there
    assert media.chat_send("chatgpt", "")[0] is False                       # nothing prepared: nothing sent


def test_send_says_how_to_enable_javascript(monkeypatch):
    def fake_run(args, **k):
        if "is running" in args[2]:
            return subprocess.CompletedProcess(args, 0, "true", "")
        return subprocess.CompletedProcess(args, 1, "", "Executing JavaScript through AppleScript is turned off.")
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    ok, msg = media.chat_send("claude", "hello there")
    assert not ok and "Allow JavaScript from Apple Events" in msg


def test_mr_does_not_end_the_sentence():                        # 00:57:10 played "mr" only
    st = one("Open YouTube and play Mr. News the Boss latest video.")
    assert st.kind == "play" and st.arg.startswith("mr news the boss")


def test_long_open_request_goes_to_the_model():                 # 01:12:09
    t = "Open Ollama Agent and try to understand how the picture front glass of 3M15.5G."
    assert "whole request" in C.complexity(t, C.plan(t))
    t = "open game of thrones game on my laptop"                    # a long title is still just an app
    assert C.complexity(t, C.plan(t)) == ""
