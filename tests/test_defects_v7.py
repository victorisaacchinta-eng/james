"""Regression tests for defects found in the live log of 2026-09-26 (see docs/ITERATIONS.md, iteration 1)."""
import datetime

import numpy as np

import commands as C
import talk as T
from test_talk import _james, _problems_screen

DAY = datetime.date(2026, 9, 26)


# ---- 1. low-confidence commands ----

def _run(monkeypatch, text, conf, installed=("Safari", "Google Chrome")):
    JM, j = _james(monkeypatch)
    ran = []
    monkeypatch.setattr(JM.James, "run_plan", lambda self, steps: ran.append([s.kind for s in steps]))
    monkeypatch.setattr(JM.LA, "find_app", lambda spoken, apps: (
        next(((n, "/Applications/" + n + ".app", 0.9) for n in installed if n.lower() in spoken.lower()), None)))
    j._before_listen = "READY"
    j.on_heard(text, conf)
    return j, ran


def test_misheard_sentence_below_half_confidence_does_not_run(monkeypatch):
    j, ran = _run(monkeypatch, "Open the KPDR search for a go-by request.", 0.43)      # log 02:00:39
    assert ran == [] and j.talker.said[-1].startswith("I may have misheard") and j.pending


def test_clear_short_commands_at_low_confidence_run_after_yes(monkeypatch):
    """Iteration 3 changed the policy: an installed app is not proof it was the app meant, so these confirm too."""
    for text, kinds in (("Open safari.", ["open"]), ("Open chrome and play Despacito.", ["play"])):   # 20:40, 21:25
        j, ran = _run(monkeypatch, text, 0.45)
        assert ran == [] and j.pending
        j.on_heard("yes", 0.9)
        assert ran == [kinds]


def test_low_confidence_open_of_unknown_app_does_not_run(monkeypatch):
    _, ran = _run(monkeypatch, "Open the KPDR.", 0.45)
    assert ran == []


def test_confident_sentences_are_unchanged(monkeypatch):
    _, ran = _run(monkeypatch, "Open the KPDR search for a go-by request.", 0.8)
    assert ran and ran[0][-1] == "search"


def test_sure_enough_rules():                                        # kept for reference; no longer gates
    yes = lambda name: True
    assert not C.sure_enough([], yes)
    assert not C.sure_enough(C.plan("search for cats"), yes)                 # free text never at low confidence
    assert not C.sure_enough(C.plan("close spotify"), yes)                   # quitting the wrong app is worse
    assert C.sure_enough(C.plan("open safari"), yes)
    assert not C.sure_enough(C.plan("open safari"), lambda name: False)


# ---- 2. collect query cleanup ----

def test_ranking_words_with_commas_and_a_time_window():
    assert C.clean_search("the top, month, repos", "github", DAY) == "created:>2026-08-27"   # log 01:59:29
    assert C.clean_search("iot repos this month", "github", DAY) == "iot created:>2026-08-27"
    assert C.clean_search("top weekly python repos", "github", DAY) == "python created:>2026-09-19"
    assert C.clean_search("the top best repos for iot and hardware", "github", DAY) == "iot and hardware"
    assert C.clean_search("rag", "github", DAY) == "rag"
    assert C.clean_search("top repos", "github", DAY) == "top repos"                # nothing left: keep the words
    assert C.clean_search("the top, month, repos", "", DAY) == "the top, month, repos"   # only GitHub is rewritten


def test_collect_from_the_live_log_keeps_the_time_window():
    st = C.plan("Open GitHub. Search for the top, month, repos and copy the link of the top and repos "
                "to Notepad or Moon Taking App.")
    assert len(st) == 1 and st[0].kind == "collect" and st[0].site == "github" and st[0].top
    assert st[0].arg.startswith("created:>") and "," not in st[0].arg
    st = C.plan("Search GitHub for RAG and copy all the repository links to the Notes app")
    assert [(s.kind, s.arg) for s in st] == [("collect", "rag")]


# ---- 4. spoken grammar ----

def test_chosen_problem_sign_reads_naturally():
    said = T.chosen({"title": "Screen cracks", "sign": "Users notice cracks or chips on the display"})
    assert said == "Screen cracks. The usual sign: cracks or chips on the display. Finding the usual fixes."
    said = T.chosen({"title": "Battery issues", "sign": "Phone shuts down unexpectedly or drains quickly."})
    assert "The usual sign: phone shuts down unexpectedly or drains quickly." in said
    assert "USB" in T.chosen({"title": "Port", "sign": "USB port is loose"})
    assert T.chosen({"title": "Port", "sign": ""}) == "Port. Finding the usual fixes."


def test_voice_pick_reads_the_sign(monkeypatch):
    JM, j = _james(monkeypatch)
    _problems_screen(JM, j)
    monkeypatch.setattr(JM.James, "_fix", lambda self, i, obj, name, prob: None)
    j._before_listen = "PROBLEMS"
    j.on_heard("fix problem one", 0.8)
    assert "notice" not in j.talker.said[-1] and "The usual sign:" in j.talker.said[-1]


# ---- 5. microphone: PortAudio restarted once ----

class _FakeSD:
    def __init__(self, fail_times):
        self.fail, self.restarts, self.closed = fail_times, 0, 0

    def _terminate(self):
        self.restarts += 1

    def _initialize(self):
        pass

    def InputStream(self, **kw):
        sd = self

        class S:
            def start(self):
                if sd.fail > 0:
                    sd.fail -= 1
                    raise RuntimeError("Internal PortAudio error [PaErrorCode -9986]")

            def close(self):
                sd.closed += 1

            def stop(self):
                pass
        return S()


def _recorder(monkeypatch, fake):
    import sys
    from agents import echo
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return echo.Recorder()


def test_mic_recovers_after_one_portaudio_error(monkeypatch):
    fake = _FakeSD(1)
    r = _recorder(monkeypatch, fake)
    r.start()
    assert r.active and fake.restarts == 1 and fake.closed == 1


def test_mic_failure_leaves_recorder_inactive(monkeypatch):
    fake = _FakeSD(5)
    r = _recorder(monkeypatch, fake)
    try:
        r.start()
        raised = False
    except RuntimeError:
        raised = True
    assert raised and not r.active and fake.closed == 2
    assert r.stop().size == 0 and isinstance(r.stop(), np.ndarray)


# ---- 8. greeting after midnight ----

def test_greeting_by_hour():
    assert T.greeting("sir", 2) == "Working late, sir? All systems are online."
    assert T.greeting("sir", 9) == "Good morning, sir. All systems are online."
    assert T.greeting("sir", 14).startswith("Good afternoon")
    assert T.greeting("sir", 21).startswith("Good evening")
    assert T.greeting("", 2) == "Working late? All systems are online."


def test_time_window_is_read_out_in_words():
    assert C.human_query("iot created:>2026-08-27") == "iot, made since August 27"
    assert C.human_query("created:>2026-08-27") == "repos made since August 27"
    assert C.human_query("rag") == "rag"
    st = C.plan("Open GitHub. Search for the top, month, repos and copy the link of the top and repos to Notepad.")
    assert "created:>" not in st[0].describe() and "created:>" not in T.narrate(st, "sir")
    assert "made since" in T.narrate(st, "sir")


def test_live_github_search_line_is_unchanged():
    st = C.plan("Open GitHub and search for top repositories for IoT and hardware-based projects.")   # log 01:08:03
    assert [(s.kind, s.arg, s.top) for s in st] == [("search", "iot and hardware-based projects", True)]
