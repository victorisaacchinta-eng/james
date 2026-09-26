"""Voice v4: the 2026-09-26 01:07 to 01:12 log. ChatGPT requests, 'search it', 'google about', misheard 'class'."""
import sys
import types

import pytest

sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

import commands as C          # noqa: E402
import launcher_apps as LA    # noqa: E402


@pytest.mark.parametrize("said,who,browser,starts,has", [
    # 01:08:24, 01:10:20, 01:10:55: ChatGPT opened, the request was dropped
    ("Open ChatGPT and research on the World War II and the effect of Germany and Hitler and tell it to generate a document.",
     "chatgpt", "", "Research the World War II", "generate a document"),
    ("Open ChatGPT in Chrome and try to research on GitHub and the fact that you had on World War Two and try to make it at the document.",
     "chatgpt", "Google Chrome", "Research GitHub", "World War Two"),
    ("Open ChatGPT and research on Hitler and the impact that he had in World War II and tried to generate a dock. "
     "Make sure that it is under 500 words.", "chatgpt", "", "Research Hitler", "under 500 words"),
    ("Open ChatGPT and research on World War II and generate a 500 page essay. Sorry, 500 line essay in the docs.",
     "chatgpt", "", "Research World War II", "500 line essay"),
    ("ask claude to write a haiku about rain", "claude", "", "Write a haiku", "rain"),
    ("I want you to open perplexity and find the best phones under 20000", "perplexity", "", "Find the best", "20000"),
])
def test_ask_assistant(said, who, browser, starts, has):
    steps = C.plan(said)
    assert len(steps) == 1
    st = steps[0]
    assert (st.kind, st.service, st.browser) == ("ask", who, browser)
    assert st.arg.startswith(starts) and has in st.arg


def test_ask_urls():
    assert C.ask_url("chatgpt", "Research WW2") == "https://chatgpt.com/?q=Research%20WW2"
    assert C.ask_url("claude", "hi there").startswith("https://claude.ai/new?q=")
    assert C.ask_url("gemini", "anything") == "https://gemini.google.com/app"            # no prompt link: clipboard


@pytest.mark.parametrize("said", ["open chatgpt", "Open ChatGPT.", "the chatgpt app is open"])
def test_not_an_ask(said):
    assert all(s.kind != "ask" for s in C.plan(said))


def test_search_it_uses_the_question():
    """01:11:40: 'Do I fix the front class of Realme 15 Pro 5G? The front class is broken. Search it on Google Chrome.'
    searched Google for 'it'."""
    st = C.plan("Do I fix the front class of Realme 15 Pro 5G? The front class is broken. Search it on Google Chrome.")[0]
    assert st.kind == "search" and st.browser == "Google Chrome"
    assert st.arg.startswith("how do i fix the front glass of realme 15 pro 5g") and "class" not in st.arg


def test_google_about():
    """01:11:15: the query started with 'about'."""
    st = C.plan("Open Chrome and Google about World War II and fine good documentaries.")[0]
    assert st.kind == "search" and st.arg.startswith("world war ii") and st.browser == "Google Chrome"


def test_long_open_names_only_the_app_and_keeps_the_request():
    """01:12:09: 'No app called "ollama agent and try to understand how ..."'."""
    said = "ollama agent and try to understand how the picture front glass of 3m15.5g"
    assert LA.said_name(said) == "ollama agent"
    assert LA.leftover("ollama and try to explain this error please", "Ollama") == "explain this error"
    assert LA.leftover("chrome", "Google Chrome") == ""
    assert LA.leftover("asphalt on my macbook", "Asphalt") == ""


def test_ask_step_runs_and_copies(monkeypatch):
    import james as JM
    monkeypatch.setattr(JM.llm, "available", lambda *a, **k: False)
    j = JM.James()
    opened, copied = [], []
    monkeypatch.setattr(JM.LA, "open_url", lambda url, where=None, **k: opened.append((url, where)))
    monkeypatch.setattr(JM.LA, "copy_text", lambda t, **k: copied.append(t) or True)
    monkeypatch.setattr(JM.James, "browser_where", lambda self, n: "/Applications/Google Chrome.app" if n else None)
    j.run_plan(C.plan("Open ChatGPT in Chrome and research on the history of Hyderabad."))
    assert opened == [("https://chatgpt.com/?q=Research%20the%20history%20of%20Hyderabad.", "/Applications/Google Chrome.app")]
    assert copied == ["Research the history of Hyderabad."]
    assert j.voice["steps"][0][1] == "ok" and j.voice["steps"][1][0].startswith("Your prompt is ready in ChatGPT")  # iteration 2: not auto-sent
