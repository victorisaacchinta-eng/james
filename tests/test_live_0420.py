"""Regression tests from the live run of 2026-09-26 04:19 to 04:24 IST (build 7e05442197bd, data/james_demo_log.txt)."""
import commands as C
import talk as T
from test_talk import _james


def _j(monkeypatch):
    JM, j = _james(monkeypatch)
    opened, launched, lines = [], [], []
    monkeypatch.setattr(JM.LA, "open_url", lambda url, where=None, **k: opened.append((url, where)))
    monkeypatch.setattr(JM.LA, "open_installed", lambda where: launched.append(where))
    monkeypatch.setattr(JM.LA, "find_app", lambda spoken, apps: (
        ("Google Chrome", "/Applications/Google Chrome.app", 0.95) if "chrome" in spoken.lower() else
        ("ChatGPT", "/Applications/ChatGPT.app", 0.88) if "chatgpt" in spoken.lower() else None))
    real_write = j.write
    monkeypatch.setattr(j, "write", lambda line: (lines.append(line), real_write(line)))
    j._before_listen = "READY"
    return JM, j, opened, launched, lines


# ---- 04:21:34 'Open ChatGPT on Chrome.' launched the ChatGPT desktop app ----

def test_open_x_on_chrome_is_parsed_as_the_website_in_chrome():
    assert [(s.kind, s.arg, s.browser) for s in C.plan("Open ChatGPT on Chrome.")] == [("open", "chatgpt", "Google Chrome")]
    assert [(s.kind, s.arg, s.browser) for s in C.plan("open chrome")] == [("open", "chrome", "")]
    assert T.narrate(C.plan("Open ChatGPT on Chrome."), "sir").endswith("Opening ChatGPT in Chrome.")


def test_open_chatgpt_on_chrome_opens_chatgpt_com_in_chrome_not_the_app(monkeypatch):
    JM, j, opened, launched, lines = _j(monkeypatch)
    j.on_heard("Open ChatGPT on Chrome.", 0.93)
    assert opened == [("https://chatgpt.com/", "/Applications/Google Chrome.app")] and launched == []


def test_open_netflix_on_chrome_still_opens_the_website_in_chrome(monkeypatch):
    JM, j, opened, launched, lines = _j(monkeypatch)
    j.on_heard("Open Netflix on Chrome.", 0.93)
    assert opened == [("https://www.netflix.com/", "/Applications/Google Chrome.app")]


# ---- 04:23:26 'Open IMDB on Chrome.' -> 'no app called imdb' (after saying 'Opening imdb on chrome') ----

def test_open_imdb_on_chrome_opens_imdb(monkeypatch):
    JM, j, opened, launched, lines = _j(monkeypatch)
    j.on_heard("Open IMDB on Chrome.", 0.91)
    assert opened == [("https://www.imdb.com/", "/Applications/Google Chrome.app")]
    assert not any("no app called" in s for s in j.talker.said)


def test_an_unknown_site_on_chrome_becomes_a_search_and_says_so(monkeypatch):
    JM, j, opened, launched, lines = _j(monkeypatch)
    j.on_heard("Open Blue Harbour Tiles on Chrome.", 0.9)
    assert opened == [("https://www.google.com/search?q=blue+harbour+tiles", "/Applications/Google Chrome.app")]
    assert any("searched for it" in (st[2] or "") for st in j.voice["steps"])
    assert j.talker.said[0].endswith("Searching the web for blue harbour tiles in Chrome.")


def test_a_spoken_domain_opens_that_domain(monkeypatch):
    JM, j, opened, launched, lines = _j(monkeypatch)
    ok, msg = j.open_by_name("asphalt.com")
    assert ok and opened == [("https://asphalt.com", None)]


def test_low_confidence_open_on_chrome_runs_only_for_a_known_site():
    no = lambda name: False
    assert C.sure_enough(C.plan("open imdb on chrome"), no)
    assert not C.sure_enough(C.plan("open blue harbour tiles on chrome"), no)       # would be a search: confirm first


# ---- 04:22:06 and 04:24:02: 'Open IMDB on Chrome and list ... in the Notes app' became an Amazon search ----

IMDB_1 = ("Open IMDB on Chrome and list out the top 10 grossing movies and buys the names of the top 10 grossing "
          "movies in the Notes app.")
IMDB_2 = "Open Chrome, Go to IMDB and find out the top 10 crossing movies. Write out their names in the Notes app."
AMAZON = C.Step("search", "top 10 grossing movies", browser="Google Chrome", site="amazon")


def test_a_model_plan_may_not_use_a_site_nobody_named():
    for heard in (IMDB_1, IMDB_2):
        keep, why = C.ungrounded(heard, [AMAZON])
        assert keep == [] and "Amazon, which you didn't ask for" in why[0]
    assert C.ungrounded("search amazon for top 10 grossing movies", [AMAZON]) == ([AMAZON], [])
    web = C.Step("search", "top 10 grossing movies")
    assert C.ungrounded(IMDB_1, [web]) == ([web], [])                # a plain web search names no site


def test_the_app_runs_nothing_and_does_not_read_out_the_dropped_plan(monkeypatch):
    for heard in (IMDB_1, IMDB_2):
        JM, j, opened, launched, lines = _j(monkeypatch)
        j.voice_step("Working out the steps")
        j.q.put(("model_plan", 0, [AMAZON], "model", 8.1, "Searching for the top 10 grossing movies on Amazon, sir.", heard))
        j.drain()
        assert opened == [] and not any("Searching for the top 10" in s for s in j.talker.said)
        assert "which you didn't ask for" in j.talker.said[-2] or "which you didn't ask for" in " ".join(j.talker.said)
        assert any(l.startswith("GUARD") and "Amazon" in l for l in lines)
        assert any(l.startswith("NOT_DONE") and "Notes app" in l for l in lines)
        assert j.voice["steps"][0][1] == "fail"
        assert "I'm afraid the plan used Amazon, which you didn't ask for, so nothing was run." in j.talker.said


def test_the_notes_part_of_a_request_is_reported_when_no_step_does_it():
    assert C.leftovers(IMDB_2, []) == ["find out the top 10 crossing movies", "Write out their names in the Notes app"]
    assert "the names of the top 10 grossing movies in the Notes app" in C.leftovers(IMDB_1, [])
    st = C.plan("Search GitHub for RAG and copy all the repository links to the Notes app")
    assert C.leftovers("Search GitHub for RAG and copy all the repository links to the Notes app", st) == []
    assert C.leftovers("open the notes app", C.plan("open the notes app")) == []


# ---- 04:20:42 identify: brand came back as the string 'null'; 04:21:11 fix: 'Go to Device Manager' on a Mac ----

def test_null_strings_from_the_model_become_none(monkeypatch):
    import numpy as np
    from agents import vision
    monkeypatch.setattr(vision.llm, "chat_json", lambda *a, **k: {
        "object": "wireless mouse", "brand": "null", "model": "None", "model_confidence": 0, "problem": "no visible damage",
        "problem_confidence": 1, "common_problems": [], "part_needed": "null"})
    r = vision.identify(np.zeros((32, 32, 3), np.uint8))
    assert r["brand"] is None and r["model"] is None and r["part_needed"] is None


def test_fix_steps_are_asked_for_the_mac(monkeypatch):
    from agents import vision
    asked = []
    monkeypatch.setattr(vision.sys, "platform", "darwin")
    monkeypatch.setattr(vision.llm, "chat_json", lambda prompt, ask, *a, **k: asked.append(ask) or {"steps": []})
    vision.fix_for("wireless mouse", "", {"title": "Connectivity issues", "sign": "disconnects"})
    assert "Mac" in asked[0] and "Device Manager" in asked[0]


# ---- 04:21:14 'TALK slow start: 2.7 s': a 104-character first sentence ----

def test_a_long_first_sentence_is_split_at_a_comma_for_a_quicker_first_word():
    s = T.sentences("Wireless mice often disconnect due to low battery, interference from other devices, or outdated "
                    "drivers. There are six steps.")
    assert s == ["Wireless mice often disconnect due to low battery,",
                 "interference from other devices, or outdated drivers.", "There are six steps."]
    assert T.sentences("Certainly, sir. Opening Chrome.") == ["Certainly, sir.", "Opening Chrome."]
    long_no_comma = "A" * 120 + "."
    assert T.sentences(long_no_comma) == [long_no_comma]
