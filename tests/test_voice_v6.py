"""Voice v6: multi-step requests and commands that refer to the ones before (the 01:33 to 01:36 log).
Close apps, save search results to Notes, prepare Instagram / WhatsApp messages, 'close it', 'copy those links'."""
import json
import plistlib
import subprocess
import sys
import time
import types

import pytest

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

import collect                   # noqa: E402
import commands as C             # noqa: E402
import media                     # noqa: E402
from agents import planner as PL  # noqa: E402


def one(t):
    s = C.plan(t)
    assert len(s) == 1, [x.describe() for x in s]
    return s[0]


# ---------- the log, as rules ----------

def test_cut_off_sentence_drops_the_fragment():                        # 01:33:30 searched "paradise movie and take to"
    st = one("Open GitHub and search for Paradise Movie and take to...")
    assert (st.kind, st.site, st.arg) == ("search", "github", "paradise movie")


def test_close_is_close_not_open():                                    # 01:35:04 the model OPENED Asphalt
    st = one("Close the Asphalt agent's game.")
    assert st.kind == "close" and st.arg.startswith("asphalt")
    assert C.complexity("Close the Asphalt agent's game.", [st]) == ""   # rules, not the model
    assert one("quit spotify").arg == "spotify" and one("exit the game").arg == ""
    assert one("stop the music").kind == "media"                         # stop + media word is still pause


def test_github_links_to_notes():                                      # 01:35:34 became search + an invented "send"
    st = one("Open GitHub and search for Bragg Repository and copy all of the repository links and paste them on "
             "the notepad app or the Notes app.")
    assert (st.kind, st.site, st.arg, st.to) == ("collect", "github", "bragg", "notes")


def test_youtube_links_to_clipboard():
    st = one("search youtube for arduino tutorials and copy the video links to the clipboard")
    assert (st.kind, st.site, st.arg, st.to) == ("collect", "youtube", "arduino tutorials", "clipboard")


def test_instagram_message():                                          # 01:36:24 Messages app + Identify (invented)
    st = one("Open Chrome and login to Instagram and open messages and go to Sam and type Hi, How are you?")
    assert (st.kind, st.service, st.to, st.arg) == ("message", "instagram", "sam", "Hi, How are you?")


@pytest.mark.parametrize("said,svc,to,words", [
    ("Open whatsapp and message mom saying I'll be late", "whatsapp", "mom", "I'll be late"),
    ("send a message to sam on whatsapp saying see you at 5", "whatsapp", "sam", "see you at 5"),
    ("on instagram go to rahul's chat and say bro where are you", "instagram", "rahul", "bro where are you"),
    ("message priya on instagram hey what's up", "instagram", "priya", "hey what's up")])
def test_message_shapes(said, svc, to, words):
    st = one(said)
    assert (st.kind, st.service, st.to, st.arg) == ("message", svc, to, words)


@pytest.mark.parametrize("said", ["instagram is cool, say that to everyone", "open instagram", "close the door"])
def test_not_a_message(said):
    assert all(s.kind != "message" for s in C.plan(said))


# ---------- follow-up commands (correlation) ----------

def test_resolve_refers_back():
    ctx = {"app": "Asphalt", "search": ("github", "rag", True), "chat": "chatgpt", "contact": "sam"}
    steps, problems = C.resolve(C.plan("close it") + C.plan("copy those links to notes") + C.plan("send it"), ctx)
    assert [(s.kind, s.arg or s.service) for s in steps] == [("close", "Asphalt"), ("collect", "rag"), ("send", "chatgpt")]
    assert steps[1].site == "github" and steps[1].to == "notes" and not problems
    _, problems = C.resolve(C.plan("close it") + C.plan("copy those links to notes"), {})
    assert len(problems) == 2


# ---------- the model's plan is checked against the words ----------

def test_model_cannot_invent_send_or_identify(monkeypatch):
    """The exact bad plans from the log are cleaned."""
    monkeypatch.setattr(PL.llm, "chat_json", lambda *a, **k: {"steps": [
        {"action": "search", "target": "Bragg Repository", "service": "github", "browser": "", "to": ""},
        {"action": "send", "target": "", "service": "", "browser": "", "to": ""}]})
    s = PL.plan("Open GitHub and search for Bragg Repository and copy all of the repository links and paste them on the notes app")
    assert [x.kind for x in s] == ["search"]                                   # 'send' was never said
    monkeypatch.setattr(PL.llm, "chat_json", lambda *a, **k: {"steps": [
        {"action": "open", "target": "chrome", "service": "", "browser": "", "to": ""},
        {"action": "open", "target": "instagram", "service": "", "browser": "", "to": ""},
        {"action": "open", "target": "messages", "service": "", "browser": "", "to": ""},
        {"action": "message", "target": "Hi, How are you?", "service": "instagram", "browser": "chrome", "to": "sam"},
        {"action": "identify", "target": "", "service": "", "browser": "", "to": ""}]})
    s = PL.plan("Open Chrome and login to Instagram and open messages and go to Sam and type Hi, How are you?")
    assert [(x.kind, x.to) for x in s] == [("message", "sam")]


def test_model_close_keeps_its_meaning(monkeypatch):
    monkeypatch.setattr(PL.llm, "chat_json", lambda *a, **k: {"steps": [
        {"action": "close", "target": "it", "service": "", "browser": "", "to": ""}]})
    s = PL.plan("could you shut that game for me", "last app opened: Asphalt")
    assert [(x.kind, x.arg) for x in s] == [("close", "")]
    assert C.resolve(s, {"app": "Asphalt"})[0][0].arg == "Asphalt"


def test_planner_sends_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(PL.llm, "chat_json", lambda sys_, user, *a, **k: seen.update(user=user) or {"steps": []})
    PL.plan("close that and copy those links", "last app opened: Asphalt")
    assert seen["user"].startswith("Context: last app opened: Asphalt")


# ---------- collect ----------

ITEMS = [{"title": 'evil" & do shell script "rm -rf ~', "url": "https://github.com/a/b", "note": "5 stars <b>"}]


def test_notes_gets_the_text_as_arguments_not_code(monkeypatch):
    calls = []
    monkeypatch.setattr(collect.sys, "platform", "darwin")
    monkeypatch.setattr(collect.subprocess, "run", lambda args, **k: calls.append(args) or
                        subprocess.CompletedProcess(args, 0, "ok", ""))
    ok, _ = collect.to_notes("GitHub: rag", ITEMS)
    script = calls[0][2]
    assert ok and calls[0][:2] == ["osascript", "-e"] and "rm -rf" not in script and calls[0][3] == "GitHub: rag"
    assert "&quot;" in calls[0][4] and "&lt;b&gt;" in calls[0][4]              # HTML-escaped inside the note


def test_github_rate_limit_message(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 403, "rate limited", {}, None)
    monkeypatch.setattr(collect, "_get", boom)
    with pytest.raises(collect.CollectError, match="10 searches a minute"):
        collect.github_repos("rag")


def test_youtube_parse(monkeypatch):
    page = ('"videoRenderer":{"videoId":"tiGw9PQbvrg","x":1,"title":{"runs":[{"text":"Arduino is \\"easy\\""}]}'
            '"videoRenderer":{"videoId":"tiGw9PQbvrg","title":{"runs":[{"text":"dup"}]}'
            '"videoRenderer":{"videoId":"BLrHTHUjPuw","title":{"runs":[{"text":"Masterclass"}]}')
    monkeypatch.setattr(collect, "_get", lambda *a, **k: page)
    v = collect.youtube_videos("arduino")
    assert [x["title"] for x in v] == ['Arduino is "easy"', "Masterclass"] and v[1]["url"].endswith("BLrHTHUjPuw")


# ---------- close ----------

def _app(tmp_path, bid, name="X"):
    c = tmp_path / f"{name}.app" / "Contents"
    c.mkdir(parents=True)
    (c / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bid}))
    return str(tmp_path / f"{name}.app")


def test_quit_is_by_bundle_id_and_never_terminal(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media.subprocess, "run", lambda args, **k: calls.append(args) or
                        subprocess.CompletedProcess(args, 0, "ok", ""))
    assert media.quit_app(_app(tmp_path, "com.gameloft.asphalt", "Asphalt"), "Asphalt") == (True, "Asphalt closed")
    assert calls[-1][-1] == "com.gameloft.asphalt" and "quit" in calls[-1][2]
    assert media.quit_app(_app(tmp_path, "com.apple.Terminal", "Terminal"), "Terminal")[0] is False
    assert media.quit_app(_app(tmp_path, 'bad" id', "Bad"), "Bad")[0] is False


# ---------- message ----------

def test_message_urls_are_validated():
    assert C.message_url("instagram", "sam.r_01", "hi") == "https://ig.me/m/sam.r_01"
    assert C.message_url("instagram", "no/slashes", "hi") == "https://www.instagram.com/direct/inbox/"
    assert C.message_url("whatsapp", "+91 90000 00000", "see you") == "https://wa.me/919000000000?text=see%20you"
    assert C.message_url("whatsapp", "", "x") == "https://web.whatsapp.com/"


def _james(monkeypatch):
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    return JM, JM.James()


def test_message_opens_the_chat_and_never_sends(monkeypatch, tmp_path):
    import config
    JM, j = _james(monkeypatch)
    (config.DATA / "contacts.json").write_text(json.dumps({"sam": {"instagram": "sam.r"}}))
    opened, copied, sent = [], [], []
    monkeypatch.setattr(JM.LA, "open_url", lambda url, where=None, **k: opened.append(url))
    monkeypatch.setattr(JM.LA, "copy_text", lambda t, **k: copied.append(t) or True)
    monkeypatch.setattr(JM.media, "chat_send", lambda *a, **k: sent.append(a) or (True, ""))
    j._before_listen = "READY"
    j.on_heard("Open Chrome and login to Instagram and open messages and go to Sam and type Hi, How are you?", 0.8)
    assert opened == ["https://ig.me/m/sam.r"] and copied == ["Hi, How are you?"] and not sent
    assert j.ctx["contact"] == "sam"
    (config.DATA / "contacts.json").write_text("{}")
    opened.clear()
    j.on_heard("message priya on instagram hey", 0.8)
    assert opened == ["https://www.instagram.com/direct/inbox/"] and "contacts.json" in j.voice["steps"][-1][2]


def test_open_then_close_it(monkeypatch):
    JM, j = _james(monkeypatch)
    j.installed = {"Asphalt": "/Applications/Asphalt.app"}
    monkeypatch.setattr(JM.LA, "open_installed", lambda w, **k: None)
    closed = []
    monkeypatch.setattr(JM.media, "quit_app", lambda where, name: closed.append(name) or (True, f"{name} closed"))
    j._before_listen = "READY"
    j.on_heard("open asphalt", 0.9)
    assert j.ctx["app"] == "Asphalt"
    j.on_heard("close it", 0.9)
    time.sleep(0.1)
    j.drain()
    assert closed == ["Asphalt"] and "app" not in j.ctx


def test_open_instagram_without_the_app_opens_the_website(monkeypatch):
    JM, j = _james(monkeypatch)
    j.installed = {}
    monkeypatch.setattr(JM.LA, "installed_apps", lambda *a, **k: {})
    opened = []
    monkeypatch.setattr(JM.LA, "open_url", lambda url, where=None, **k: opened.append(url))
    ok, _ = j.open_by_name("instagram")
    assert ok and opened == ["https://www.instagram.com/"]


def test_collect_writes_notes_and_clipboard(monkeypatch):
    JM, j = _james(monkeypatch)
    monkeypatch.setattr(JM.collect, "gather", lambda site, q, top=False: ITEMS * 3)
    notes, copied = [], []
    monkeypatch.setattr(JM.collect, "to_notes", lambda title, items: notes.append((title, len(items))) or (True, "new note in Notes"))
    monkeypatch.setattr(JM.LA, "copy_text", lambda t, **k: copied.append(t) or True)
    j._before_listen = "READY"
    j.on_heard("Open GitHub and search for RAG repositories and copy all of the repository links to the Notes app.", 0.8)
    for _ in range(100):
        if notes:
            break
        time.sleep(0.01)
    time.sleep(0.05)
    j.drain()
    assert notes == [("GitHub: rag", 3)] and copied and "https://github.com/a/b" in copied[0]
    assert j.voice["steps"][-1][1] == "ok" and j.ctx["search"] == ("github", "rag", False)
