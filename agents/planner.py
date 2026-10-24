"""PLANNER: complex voice requests -> a short plan, written by the local model (Qwen via Ollama).

Simple commands ("open terminal", "play despacito on spotify") are planned by fast rules in commands.py.
When commands.complexity() says a sentence is too tangled for the rules (several sentences, a long request,
nothing matched), the words go to the LOCAL model instead, which answers with JSON steps.

The model can only choose from the same small set of actions the rules use (open an app, play, search, ask
an AI chat, media keys, press send, identify). Every step is validated here; anything else is dropped. So a
bad or strange answer can at worst open the wrong page, never run a command. Nothing leaves the laptop."""
from __future__ import annotations

import re

import commands as C
from agents import llm

ACTIONS = ("open", "close", "play", "search", "collect", "message", "ask", "media", "send", "identify")
SERVICES = ("", "youtube", "spotify", "github", "amazon", "chatgpt", "claude", "perplexity", "gemini", "instagram",
            "whatsapp")
# an action the model may only use when the words ask for it (log 01:35:38: an invented "send"; 01:36:24: an invented "identify")
EVIDENCE = {
    "send": r"\b(?:send|enter|submit|return)\b",
    "identify": r"\b(?:identify|what(?:'s| is) (?:this|that)|scan|look at (?:this|it))\b",
    "media": r"\b(?:pause|resume|stop|next|previous|skip|continue|unpause|play)\b",
    "close": r"\b(?:close|quit|exit|kill|shut)\b",
    "collect": r"\b(?:copy|save|collect|paste|put|write|note|list|add|store|keep)\b",
    "message": r"\b(?:message|text|dm|type|say|tell|send|write|chat)\b",
}
BROWSERS = ("", "chrome", "safari", "brave", "firefox", "arc", "edge")
MAX_STEPS = 4
# apps that only lead into a message or a saved list; the step itself opens what it needs
LEAD_IN = re.compile(r"^(?:the\s+)?(?:google\s+)?(?:chrome|safari|brave|browser|instagram|insta|whatsapp|messages?|"
                     r"dms?|inbox|notes?|note\s*pad|notepad|github|youtube)(?:\s+app)?$")

SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "maxItems": MAX_STEPS, "items": {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": list(ACTIONS)},
                           "target": {"type": "string"},
                           "service": {"type": "string", "enum": list(SERVICES)},
                           "browser": {"type": "string", "enum": list(BROWSERS)},
                           "to": {"type": "string"}},
            "required": ["action", "target", "service", "browser", "to"]}},
        "say": {"type": "string"},
    },
    "required": ["steps", "say"],
}

PROMPT = """You turn a spoken request into at most 4 steps for a Mac assistant. The words come from speech to text,
so fix obvious mishearings (class->glass, spouse->pause, note->play, repose/reports->repos).
Actions (use only these):
- open: open an installed app or a website. target = app name only (e.g. "Terminal", "Asphalt", "Instagram").
- close: quit an app. target = app name, or "it" for the app opened last.
- collect: search and save the result links. target = search words. service = "github" | "youtube" | "" (web).
  to = "notes" (Notes / notepad app) or "clipboard". Use this when the user wants links copied, saved or listed.
- message: prepare a message to a person (the user sends it). service = "instagram" | "whatsapp".
  to = the person's name, lower case. target = the exact words to send. Login is never needed.
- play: play a song or video. target = what to play. service = "spotify" or "youtube" (default youtube).
- search: web search. target = the search words only. service = "github" | "youtube" | "amazon" | "" (Google).
- ask: send a prompt to an AI chat. service = "chatgpt" | "claude" | "perplexity" | "gemini".
  target = the full prompt, cleaned up, including every detail the user gave (length, format, topic).
- media: target = "play" | "pause" | "next" | "previous". service = "spotify" | "youtube" | "".
- send: press send in the AI chat that is open. service = the chat.
- identify: look at the object the user holds up. target = "".
browser = "chrome" (etc.) only if the user named a browser, else "". to = "" unless the action uses it.
Rules: skip steps that only lead to another step ("open Chrome, search for ChatGPT, open ChatGPT, ask X" is ONE ask
step in chrome). Do not open an app just to do something the next step does anyway. Never invent requests.
Words like "it", "that", "those links" refer to what happened just before (given as Context).
If nothing in the words is a request, return no steps.
say: ONE short sentence JAMES speaks back, in the manner of a polite, dry-witted British butler who calls the user "sir".
If the words are a task, say what you are doing in plain words. If they are conversation (a greeting, a question
about you), answer briefly and return no steps. If they ask for facts you cannot know offline (weather, news,
prices), say you can't check that from here. Never claim to have done something the steps don't do."""

EXAMPLES = [
    ("Open Chrome. Search for ChatGPT. Open ChatGPT. Then search for Batman, Superman.",
     '{"steps":[{"action":"ask","target":"Search for Batman vs Superman","service":"chatgpt","browser":"chrome","to":""}],"say":"Asking ChatGPT about Batman versus Superman, sir."}'),
    ("so I was thinking, put on some lofi on youtube and then look up the best pump seal kits on amazon",
     '{"steps":[{"action":"play","target":"lofi","service":"youtube","browser":"","to":""},'
     '{"action":"search","target":"best pump seal kits","service":"amazon","browser":"","to":""}],"say":"Lofi on YouTube, and pump seal kits on Amazon, sir."}'),
    ("Open GitHub and look for RAG projects and put all the repo links in my notes",
     '{"steps":[{"action":"collect","target":"RAG","service":"github","browser":"","to":"notes"}],"say":"Collecting the top RAG repositories into your Notes, sir."}'),
    ("Open Chrome and log in to Instagram and open messages and go to Sam and type Hi, how are you?",
     '{"steps":[{"action":"message","target":"Hi, how are you?","service":"instagram","browser":"chrome","to":"sam"}],"say":"Opening your chat with Sam. The message is on your clipboard, sir."}'),
    ("do you think it will rain today", '{"steps":[],"say":"I can\'t check the weather from here, sir, but a window might."}'),
]


def _step(d: dict, text: str = "") -> C.Step | None:
    a = str(d.get("action", "")).lower()
    t = re.sub(r"\s+", " ", str(d.get("target", "") or "")).strip()[:600]
    svc = str(d.get("service", "") or "").lower()
    br = C.BROWSERS.get(str(d.get("browser", "") or "").lower(), "")
    svc = svc if svc in SERVICES else ""
    if a not in ACTIONS:
        return None
    if text and a in EVIDENCE and not re.search(EVIDENCE[a], C.unquoted(text), re.I):
        return None                                                 # the words never asked for this
    if text and (a in C.negated_kinds(text) or (C.is_question(text) and a != "search")):
        return None                                                 # "do not send it", "explain how to close"
    to = re.sub(r"[^a-z0-9._+ -]", "", str(d.get("to", "") or "").lower()).strip()[:40]
    if a == "open":
        return C.Step("open", t) if 0 < len(t.split()) <= 5 else None
    if a == "close":
        return C.Step("close", "" if C.APP_PRONOUNS.match(t.lower()) else t) if len(t.split()) <= 5 else None
    if a == "collect":
        site = svc if svc in ("github", "youtube") else ""
        dest = "clipboard" if to == "clipboard" else "notes"
        q = C.clean_search(t, site) if t and t.lower() not in C.PRONOUNS | {"those links", "the links", "them"} else ""
        return C.Step("collect", q, site=site, top=C.wants_top(t), to=dest)
    if a == "message":
        if svc not in ("instagram", "whatsapp") or not t or not re.fullmatch(r"[a-z][a-z._-]{0,30}", to or "-"):
            return None
        return C.Step("message", t, svc, br, to=to)
    if a == "play":
        return C.Step("play", t, "spotify" if svc == "spotify" else "youtube", br) if t else None
    if a == "search":
        site = svc if svc in ("github", "youtube", "amazon", "spotify") else ""
        return C.Step("search", C.clean_search(t, site), browser=br, site=site, top=C.wants_top(t)) if t else None
    if a == "ask":
        return C.Step("ask", C._prompt(t), svc, br) if svc in C.ASSISTANTS.values() and len(t.split()) >= 2 else None
    if a == "media":
        return C.Step("media", t.lower(), svc if svc in ("spotify", "youtube") else "") if t.lower() in (
            "play", "pause", "next", "previous") else None
    if a == "send":
        return C.Step("send", "", svc if svc in C.ASSISTANTS.values() else "")
    if a == "identify":
        return C.Step("identify")
    return None


def plan(text: str, context: str = "", timeout: float = 30) -> list[C.Step]:
    """The local model's plan for a complex request (validated). Raises llm.LLMUnavailable if the model is down."""
    return plan_full(text, context, timeout)[0]


def plan_full(text: str, context: str = "", timeout: float = 30) -> tuple[list[C.Step], str]:
    """(steps, what JAMES says back). context: what the previous commands did
    ('last app opened: Asphalt; last search: rag on github')."""
    shots = "\n".join(f"Request: {q}\nJSON: {a}" for q, a in EXAMPLES)
    ask = (f"Context: {context}\n" if context else "") + f"Request: {text}\nJSON:"
    out = llm.chat_json(PROMPT + "\n\nExamples:\n" + shots, ask, SCHEMA, timeout=timeout)
    steps = [s for s in (_step(d, text) for d in (out.get("steps") or [])[:MAX_STEPS] if isinstance(d, dict)) if s]
    say = re.sub(r"https?://\S+", "", str(out.get("say") or "")).strip()[:240]
    return tidy(steps), say


def tidy(steps: list[C.Step]) -> list[C.Step]:
    """Drop steps that only lead to the next one: 'open chrome' before a page step, 'open chatgpt' before an ask."""
    out: list[C.Step] = []
    for st in steps:
        prev = out[-1] if out else None
        if prev and prev.kind == "open":
            b = C.browser_name(prev.arg)
            who = C.ASSISTANTS.get(prev.arg.lower().replace(" app", ""))
            if b and st.kind in ("play", "search", "ask") and st.service != "spotify":
                st.browser = st.browser or b
                out[-1] = st
                continue
            if who and st.kind in ("ask", "send"):
                out[-1] = st
                continue
            if st.kind in ("message", "collect"):                  # "open chrome / instagram / messages / notes" first
                while out and out[-1].kind == "open" and LEAD_IN.match(out[-1].arg.lower()):
                    out.pop()
                out.append(st)
                continue
        out.append(st)
    return out


if __name__ == "__main__":                       # python -m agents.planner "open chrome. go to youtube. ..."
    import sys
    import time
    said = " ".join(sys.argv[1:]) or "Open Chrome. Go to YouTube. Find a video about Arduino. Then open GitHub."
    rules = C.plan(said)
    why = C.complexity(said, rules)
    print("said :", said)
    print("rules:", [s.describe() for s in rules] or "none", "|", f"route = MODEL ({why})" if why else "route = rules")
    t0 = time.time()
    try:
        steps = plan(said)
        print(f"model: {[s.describe() for s in steps] or 'no steps'}  ({time.time() - t0:.1f} s)")
    except llm.LLMUnavailable as e:
        print("model: unavailable:", e)
