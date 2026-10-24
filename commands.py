"""Voice commands: turn one spoken sentence into a short plan of launch-only steps.

    "Open Chrome and play Despacito"          -> play "despacito" on YouTube, in Google Chrome
    "open game of thrones game on my laptop"  -> open the installed app that best matches
    "play believer on spotify"                -> Spotify search for "believer"
    "universal display for redmi note 10"     -> universaldisplay.in compatible displays page
    "search for pump seal kits"               -> web search in the browser
    "open github and find top repos for iot"  -> GitHub repository search (sorted by stars for 'top')
    "pause the video" / "next song"           -> media control of what JAMES started (Spotify app, or YouTube in Chrome)
    "open chatgpt and research X ..."         -> ChatGPT with that prompt (also Claude, Perplexity, Gemini)
    "how do I fix X? search it on chrome"     -> 'it' means the question that was just asked

Every step can only OPEN something (an installed app, or a web page) or press a media key
(play, pause, next, previous) through a fixed script. Nothing spoken is ever run as a command, and no
file or setting is touched. The planner is pure text in, steps out, so it is
tested without a microphone or a camera."""
from __future__ import annotations

import datetime
import re
import urllib.parse
from dataclasses import dataclass, replace

VERBS = ("open", "launch", "start", "run", "play", "search", "google", "find", "look up", "look for", "show",
         "try to find", "try to search", "universal display", "display", "tempered glass", "identify", "scan",
         "pause", "resume", "stop", "next", "skip", "previous", "close", "quit", "exit")
MEDIA = {"pause": "pause", "stop": "pause", "resume": "play", "continue": "play", "unpause": "play",
         "next": "next", "skip": "next", "previous": "previous", "go back": "previous"}
# play targets that are not a title: "play the video", "play it", "play the video that is playing"
GENERIC_PLAY = re.compile(r"(?:the\s+|a\s+|some\s+)?(?:it|that|this|something|music|song|video|game|track|"
                          r"(?:video|song|music|track)\s+(?:that\s+is|that's|which\s+is)\s+playing|what(?:'s|\s+is|\s+was)\s+playing|"
                          r"that\s+is\s+playing)(?:\s+(?:on|in)\s+(?:youtube|spotify))?")
# AI assistants: "open chatgpt and <prompt>" opens a new chat with the prompt (the prompt is also copied to the
# clipboard, because these links pre-fill and may not send on their own). Gemini has no prompt link: clipboard only.
ASSISTANTS = {"chatgpt": "chatgpt", "chat gpt": "chatgpt", "claude": "claude", "perplexity": "perplexity",
              "gemini": "gemini"}
ASK_URL = {"chatgpt": "https://chatgpt.com/?q=", "claude": "https://claude.ai/new?q=",
           "perplexity": "https://www.perplexity.ai/search?q=", "gemini": ""}
ASK_HOME = {"chatgpt": "https://chatgpt.com/", "claude": "https://claude.ai/new", "perplexity": "https://www.perplexity.ai/",
            "gemini": "https://gemini.google.com/app"}
ASK_NAME = {"chatgpt": "ChatGPT", "claude": "Claude", "perplexity": "Perplexity", "gemini": "Gemini"}
_AI = r"(chat\s?gpt|claude|perplexity|gemini)"
_ASK = re.compile(r"\b(?:open|launch|go to|use|ask|tell)[\s,]+(?:the\s+)?(chat\s?gpt|claude|perplexity|gemini)(?:\s+app)?"
                  r"(?:\s+(?:in|on|using)\s+(?:the\s+)?(google chrome|chrome|safari|brave|firefox|arc|edge))?"
                  r"[\s,]*(?:(?:and|then|please|try|to|about|for|if|that)\s+)*(.*)$", re.I | re.S)
# "In the web version of ChatGPT, search X" / "on Claude, explain X" (log 01:19:35)
_ASK_IN = re.compile(r"^(?:in|on|using|with|inside)\s+(?:the\s+)?(?:web\s+version\s+of\s+|website\s+of\s+|app\s+)?"
                     + _AI + r"(?:\s+app|\s+website)?\s*,?\s+(.+)$", re.I | re.S)
# "Then Claude will search about X" / "ChatGPT, write a poem" (log 01:19:11)
_ASK_SUBJ = re.compile(r"^(?:then\s+|and\s+|now\s+)?" + _AI + r"\s*,?\s+(?:will|should|can|could|please|to|must)?\s*"
                       r"((?:search|find|research|write|explain|tell|make|create|generate|summari[sz]e|list|give|answer|help|draft)\b.+)$",
                       re.I | re.S)
SEND = re.compile(r"^(?:please\s+)?(?:(?:click|press|hit|tap)\s+(?:on\s+)?(?:the\s+)?(?:enter|send|submit|return)(?:\s+(?:button|key))?"
                  r"|send\s+(?:it|that|the\s+(?:prompt|message))|submit\s+(?:it|that)|enter|send|submit)"
                  r"(?:\s*$|\s+(?:on|in|to|for)\s+(?:the\s+)?(?:web\s+version\s+of\s+)?" + _AI + r"\b.*$"
                  r"|\s+(?:it|now|please)?\s*$)", re.I)
PRONOUNS = {"it", "that", "this", "it up", "that up", "this up", "them", "this question", "that question"}
SITES = {"github": "github", "git hub": "github", "youtube": "youtube", "you tube": "youtube", "spotify": "spotify",
         "google": "google", "amazon": "amazon"}
BROWSERS = {"chrome": "Google Chrome", "google chrome": "Google Chrome", "safari": "Safari", "brave": "Brave Browser",
            "firefox": "Firefox", "arc": "Arc", "edge": "Microsoft Edge", "microsoft edge": "Microsoft Edge"}
UD_HOME = "https://universaldisplay.in/"


@dataclass
class Step:
    kind: str                 # open | close | play | search | collect | message | display | glass | identify | media | ask | send
    arg: str = ""             # app name, song, query or phone model
    service: str = ""         # play: youtube | spotify
    browser: str = ""         # the browser app to open a page in ("" = default browser)
    site: str = ""            # search: github | youtube | spotify | amazon | "" (the web)
    top: bool = False         # search: 'top' / 'best' was said (GitHub sorts by stars)
    to: str = ""              # message: the contact ("sam"); collect: where the links go (notes | clipboard)

    def describe(self) -> str:
        where = f" in {self.browser}" if self.browser else ""
        if self.kind == "open":
            return f"Open {self.arg}{where}"
        if self.kind == "play":
            return f'Play "{self.arg}" on {"Spotify" if self.service == "spotify" else "YouTube"}{where}'
        if self.kind == "search":
            on = {"github": "GitHub", "youtube": "YouTube", "spotify": "Spotify", "amazon": "Amazon"}.get(self.site, "the web")
            return f'Search {on} for "{human_query(self.arg)}"{where}'
        if self.kind == "close":
            return f"Close {self.arg}" if self.arg else "Close the app"
        if self.kind == "collect":
            src = {"github": "GitHub repos", "youtube": "YouTube videos"}.get(self.site, "web links")
            dest = "Notes" if self.to == "notes" else "the clipboard"
            return f'Save {"top " if self.top else ""}{src} for "{human_query(self.arg)}" to {dest}' if self.arg else f"Save the last results to {dest}"
        if self.kind == "message":
            app = {"instagram": "Instagram", "whatsapp": "WhatsApp"}.get(self.service, self.service)
            return f'Message {self.to.title() or "?"} on {app}: "{self.arg[:40]}"'
        if self.kind == "send":
            return f"Press send in {ASK_NAME.get(self.service, 'the chat')}"
        if self.kind == "ask":
            return f'Ask {ASK_NAME.get(self.service, self.service)}: "{self.arg[:60]}{"..." if len(self.arg) > 60 else ""}"{where}'
        if self.kind == "media":
            return {"pause": "Pause", "play": "Resume", "next": "Next", "previous": "Previous"}[self.arg] + (
                f" on {'Spotify' if self.service == 'spotify' else 'YouTube'}" if self.service else "")
        if self.kind == "display":
            return f"Compatible displays for {self.arg}" if self.arg else "Open Universal Display"
        if self.kind == "glass":
            return f"Tempered glass for {self.arg}" if self.arg else "Open Universal Display"
        if self.kind == "identify":
            return "Identify what I'm holding"
        return self.kind


def _clean(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[“”\"`]", "", s)
    s = re.sub(r"(?<![a-z])'|'(?![a-z])", "", s)                          # quotes, but keep "what's"
    s = re.sub(r"[.!?]+$", "", s)
    s = re.sub(r"\b(open|launch|start|play|search|find|pause|resume)\s*,\s*", r"\1 ", s)   # "Open, Command Prompt"
    s = re.sub(r"\b(front|back|rear|tempered|screen|camera)\s+class\b", r"\1 glass", s)       # log 01:11:40 "front class"
    s = re.sub(r"^(?:hey |ok |okay )?(?:james[,]?\s+)?(?:(?:can|could|would) you\s+|please\s+)*", "", s)
    return s.strip(" ,")


_PREAMBLE = re.compile(r"(?:(?:hi|hello|hey|hiya|good (?:morning|afternoon|evening))(?:\s+(?:there|james|jarvis))?,?|"
                       r"want you to|you to|can you|could you|would you|will you|please|i want to|i'd like to|"
                       r"i would like to|let's|lets|go ahead and|just|now|and|so)\s*$")


def _strip_preamble(c: str) -> str:
    """'i want you to open asphalt' -> 'open asphalt'. Only after a polite lead-in, so a model name such as
    'play station 5' is never cut up."""
    if re.match(rf"^(?:{_VERB_RE})\b", c):
        return c
    for m in re.finditer(rf"\b(?:{_VERB_RE})\b", c):
        if _PREAMBLE.search(c[:m.start()].strip() + " ") or _PREAMBLE.search(c[:m.start()].rstrip()):
            return c[m.start():]
    return c


_VERB_RE = "|".join(re.escape(v) for v in sorted(VERBS, key=len, reverse=True))
# split "X and Y" / "X then Y" / "X, and then Y" only where a new command verb starts, so a title like
# "Pride and Prejudice" or "Tom and Jerry" stays in one piece
_SPLIT = re.compile(rf"\s*(?:,\s*)?\b(?:and then|then|and|also)\s+(?=(?:please\s+)?(?:{_VERB_RE})\b)")


_LONE_VERB = re.compile(r"(^|[.!?]\s+)(open|launch|start|close|quit|play|search(?: for)?|find)[.,]\s+(?=[a-z0-9])", re.I)


def split_clauses(text: str) -> list[str]:
    text = _LONE_VERB.sub(r"\1\2 ", unabbrev(text))            # log 03:27:53 "Open. Key Note on my MacBook."
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):                   # "So this is what... I want you to open"
        for c in _SPLIT.split(_clean(sentence)):
            c = _strip_preamble(c.strip(" ,"))
            if c:
                out.append(c)
    return out


def _browser_in(s: str) -> tuple[str, str]:
    """'despacito in chrome' -> ('despacito', 'Google Chrome')."""
    m = re.search(r"\s+(?:in|on|using|with)\s+(?:the\s+)?(" + "|".join(sorted(BROWSERS, key=len, reverse=True))
                  + r")(?:\s+browser)?$", s)
    if m:
        return s[:m.start()].strip(), BROWSERS[m.group(1)]
    return s, ""


def _model(s: str) -> str:
    s = re.sub(r"^(?:for|of|on)\s+(?:(?:a|an|the|my|this)\s+)?", "", s.strip())
    s = re.sub(r"\s+(?:phone|mobile|smartphone)$", "", s)
    return s.strip(" ,")


def parse_clause(c: str) -> Step | None:
    c = c.strip()
    if re.match(r"^(?:identify|scan)\b|^what(?:'s| is) (?:this|that)\b", c):
        return Step("identify")
    # universal display / tempered glass (phone parts compatibility, universaldisplay.in)
    m = re.match(r"^(?:open\s+|show\s+|find\s+|search\s+)?(?:the\s+)?(?:universal display(?:s)?)(?:\s+(?:site|website|app))?\s*(.*)$", c)
    if m:
        rest = m.group(1)
        if re.match(r"^(?:for\s+)?(?:tempered\s+)?glass\b", rest):
            return Step("glass", _model(re.sub(r"^(?:for\s+)?(?:tempered\s+)?glass\s*", "", rest)))
        return Step("display", _model(re.sub(r"^(?:for\s+)?(?:display|screen|combo|folder)s?\s*", "", rest)))
    m = re.match(r"^(?:show\s+|find\s+|search\s+)?(?:me\s+)?(?:a\s+|the\s+)?(?:compatible\s+)?(tempered glass|screen guard|glass)\s+(?:for|of)\s+(.+)$", c)
    if m:
        return Step("glass", _model(m.group(2)))
    m = re.match(r"^(?:show\s+|find\s+|search\s+)?(?:me\s+)?(?:a\s+|the\s+)?(?:compatible\s+)?(?:display|screen|combo|folder)s?\s+(?:for|of)\s+(.+)$", c)
    if m:
        return Step("display", _model(m.group(1)))
    # media keys: "pause the video", "next song", "resume". Misheard "pause" ("spouse", "pose") only with a media word
    m = re.match(r"^(pause|stop|resume|continue|unpause|next|skip|previous|go back)\b(.*)$", c) or \
        re.match(r"^(?:spouse|pose|paws|pours)\b(?=.*\b(?:video|song|music|youtube|spotify|it)\b)(.*)$", c)
    if m:
        verb = m.group(1) if m.lastindex and m.lastindex >= 2 else "pause"
        rest = m.group(m.lastindex) if m.lastindex else ""
        if verb == "stop" and not re.search(r"\b(?:video|song|music|playing|youtube|spotify|it)\b", rest):
            return None                                            # "stop" alone is Esc's job, not a media key
        svc = "spotify" if "spotify" in rest else ("youtube" if re.search(r"you ?tube|video", rest) else "")
        return Step("media", MEDIA.get(verb, "pause"), svc)
    m = re.match(r"^play\b\s*(.*)$", c)
    if m:
        q = re.sub(r"^(?:the\s+)?(?:song|music|video|track)\s+(?!that\b|which\b)", "", m.group(1)).strip()
        service = ""
        sm = re.search(r"\s+(?:on|in|from|using|and|with|via|through)\s+(?:the\s+)?(spotify|youtube|you tube)(?:\s+app)?$", q)
        if sm:                                                      # "play despacito and spotify" (misheard 'on')
            service, q = ("spotify" if sm.group(1) == "spotify" else "youtube"), q[:sm.start()].strip()
        q, browser = _browser_in(q)
        if not q or GENERIC_PLAY.fullmatch(q):
            return Step("media", "play", service)
        return Step("play", q, service or "youtube", browser)
    m = re.match(r"^(?:try to\s+)?(?:search|google|look up|look for|find)\s+(?:for\s+|about\s+)?(.+)$", c)
    if m:
        q, browser = _browser_in(m.group(1).strip())
        q = re.sub(r"\s+up$", "", q) if q.endswith(" up") and q[:-3] in PRONOUNS else q
        site = ""
        sm = re.search(r"\s+(?:on|in)\s+(github|git hub|youtube|you tube|spotify|amazon|google|the web|the internet)$", q)
        if sm:
            site, q = SITES.get(sm.group(1), ""), q[:sm.start()].strip()
        sm = re.match(r"^(github|git hub|youtube|you tube|spotify|amazon)\s+for\s+(.+)$", q)     # "search github for X"
        if sm:
            site, q = SITES[sm.group(1)], sm.group(2)
        sm = re.match(r"^(?:the\s+)?((?:(?:top|best|popular|good)\s+)*)(?:github|git hub)\s+(?:repos?|repositories|repose|"
                      r"reports?|projects?)\s+(?:for|on|about|in)\s+(.+)$", q)        # "top github repos for X"
        if sm:
            site, q = "github", sm.group(1) + sm.group(2)
        site = "" if site == "google" else site
        return Step("search", clean_search(q, site), browser=browser, site=site, top=wants_top(q)) if q else None
    st = close_step(c)
    if st:
        return st
    m = re.match(r"^(?:open|launch|start|run)\s+(?:up\s+)?(.+)$", c)
    if m:
        name, browser = _browser_in(m.group(1).strip())
        if browser and name and name not in BROWSERS:              # 'open chatgpt on chrome': the website, in Chrome
            return Step("open", name, browser=browser)
        return Step("open", m.group(1).strip()) if m.group(1).strip() else None
    m = re.match(r"^[a-z]+\s+(.+?)\s+(?:on|in)\s+(spotify|youtube|you tube)$", c)   # "note despacito on spotify": a misheard 'play'
    if m and m.group(1).strip():
        return Step("play", m.group(1).strip(), "spotify" if m.group(2) == "spotify" else "youtube")
    return None


_WINDOW = re.compile(r"\b(?:(?:of|in|from|for)\s+)?(?:(?:this|the|last|past)\s+)?(week|month|year)\b|\b(weekly|monthly|yearly)\b")
WINDOW_DAYS = {"week": 7, "month": 30, "year": 365}


def clean_search(q: str, site: str = "", today: datetime.date | None = None, keep: bool = True) -> str:
    """'the top best repos for iot and hardware' on GitHub -> 'iot and hardware' (ranking words become a sort).
    'the top, month, repos' (log 01:59:29) -> 'created:>YYYY-MM-DD' (made in the last 30 days, sorted by stars)."""
    q = orig = q.strip()
    if site == "github":
        q = re.sub(r"\s*[,;]+\s*", " ", q).strip()
        since = ""
        m = _WINDOW.search(q)
        if m:
            d = (today or datetime.date.today()) - datetime.timedelta(days=WINDOW_DAYS[m.group(1) or m.group(2)[:-2]])
            since = f"created:>{d.isoformat()}"
            q = re.sub(r"\s+", " ", q[:m.start()] + " " + q[m.end():]).strip()
        q = re.sub(r"^(?:the\s+|some\s+)?(?:(?:top|best|good|popular|most\s+starred|latest|new)\s+)*", "", q)
        q = re.sub(r"^(?:\d+\s+)?(?:repos?|repositories|repository|repose|reports?|projects?|libraries|library|code)\s+"
                   r"(?:for|on|about|of|in|with)\s+", "", q)
        # log 03:27:30 "the repo, which is related to weather" -> "weather"
        q = re.sub(r"^(?:(?:the|a|any|some)\s+)?(?:repos?|repositories|repository|projects?)\s+"
                   r"(?:which|that)\s+(?:is|are)\s+(?:related\s+to|about|for|on|using)\s+", "", q)
        q = re.sub(r"^(?:related\s+to|about)\s+", "", q)
        q = re.sub(r"(?:^|\s+)(?:\d+\s+)?(?:repos?|repositories|repository|repose)$", "", q).strip()
        return " ".join(x for x in (q, since) if x) or (orig if keep else "")
    return q.strip() or q


def wants_top(q: str) -> bool:
    return bool(re.search(r"\b(?:top|best|popular|most starred)\b", q))


def _open_then_search(c: str) -> list[str]:
    """'open chrome search for top github repos' (no 'and') -> ['open chrome', 'search for top github repos']."""
    m = re.match(r"^((?:open|launch|start)\s+.+?)\s+(?:and\s+)?((?:try to\s+)?(?:search|google|look up|look for|find)\s+.+)$", c)
    return [m.group(1), m.group(2)] if m else [c]


def plan(text: str) -> list[Step]:
    """Sentence -> steps. 'Open Chrome and play X' becomes one step: play X in Chrome."""
    text = re.sub(r"\s+(?:and|then|to|so)\s+(?:\S+\s+){0,2}\S*\s*(?:\.\.\.|…)\s*$", ".", text.strip())   # cut-off end (01:33:30)
    asked = ask_step(text) or message_step(text) or send_step(text)
    if asked:
        return [asked]
    steps: list[Step] = []
    raw = [part for c in split_clauses(text) for part in _open_then_search(c)]
    for c in raw:
        st = parse_clause(c)
        if st is None:
            continue
        prev = steps[-1] if steps else None
        site = SITES.get(re.sub(r"^(?:the\s+)?|\s+(?:app|website|site)$", "", prev.arg)) if prev and prev.kind == "open" else ""
        if st.kind == "media" and st.arg == "play" and prev and prev.kind == "open":
            if site in ("youtube", "spotify"):
                st.service = st.service or site                     # "open spotify and play" = resume there
                steps[-1] = st
            continue                                                # "open asphalt and play the game": just open it
        if prev and prev.kind == "open" and st.kind == "search" and not st.site and site in ("github", "youtube",
                                                                                                  "spotify", "amazon"):
            st.site, st.top, st.arg = site, st.top or wants_top(st.arg), clean_search(st.arg, site)   # "open github and find X"
            steps[-1] = st
            continue
        if prev and prev.kind == "open" and st.kind == "play" and site == "youtube" and st.service == "youtube":
            steps[-1] = st                                          # "open youtube and play X": one tab, not two
            continue
        if prev and prev.kind == "open" and st.kind in ("play", "search", "display", "glass") and not st.browser:
            b = browser_name(prev.arg)
            if b and st.service != "spotify":
                steps[-1] = replace(st, browser=b)                          # the page opens in that browser anyway
                continue
            if st.kind == "play" and st.service == "youtube" and re.fullmatch(r"(?:the\s+)?spotify(?:\s+app)?", prev.arg):
                st.service = "spotify"                                      # "open spotify and play X"
                steps[-1] = st
                continue
        steps.append(st)
    for st in steps:
        if st.kind in ("open", "close"):
            st.arg = st.arg.strip(" ,;:")
    return collect_step(text, with_context(steps, text))



def human_query(q: str) -> str:
    """For the screen and the voice: 'iot created:>2026-08-27' -> 'iot, made since August 27'."""
    m = re.search(r"created:>(\d{4})-(\d\d)-(\d\d)", q or "")
    if not m:
        return q
    d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    base = re.sub(r"\s+", " ", q[:m.start()] + " " + q[m.end():]).strip()
    return f"{base + ', ' if base else 'repos '}made since {d.strftime('%B')} {d.day}"


# ---------- what the words forbid, and what the plan leaves out (iteration 2, P0-B / ST-02 / ST-03) ----------
KIND_VERBS = {
    "send": r"send|submit|post",
    "close": r"close|quit|exit|kill|shut(?:\s+down)?",
    "open": r"open|launch|start",
    "message": r"message|text|dm|type|write to|tell",
    "collect": r"copy|save|collect|paste|note\s+down|store",
    "play": r"play",
    "search": r"search|google|look\s+up",
    "media": r"pause|resume|skip",
    "ask": r"ask",
}
_NEG = r"(?:do\s+not|don'?t|dont|never|no\s+need\s+to|not|without)"
_QUESTION = re.compile(r"^(?:(?:hey\s+)?(?:james\s*,?\s*)?)(?:can you\s+)?(?:explain|how\s+(?:do|can|would|should|to)|"
                       r"what\s+(?:is|are|does|happens)|why|tell me (?:how|why|what)|teach me|show me how)\b", re.I)
ANSWER = re.compile(r"\b(?:return|answer|tell me|give me|find out|figure out|summari[sz]e|translate|calculate)\b", re.I)


def unquoted(text: str) -> str:
    """Words inside quotes are a name or a title, never an instruction ('search for "close the door"')."""
    return re.sub(r"[\"“”][^\"“”]*[\"“”]", " ", text or "")


def negated_kinds(text: str) -> set[str]:
    """'Do not send it' -> {'send'}. 'I said send yesterday, but do not send this' -> {'send'} (the whole
    request is treated as not wanting a send)."""
    t = unquoted(text).lower()
    return {k for k, rx in KIND_VERBS.items() if re.search(rf"\b{_NEG}\s+(?:\w+\s+){{0,2}}?(?:{rx})\b", t)}


def is_question(text: str) -> bool:
    """'Explain how to close an app' is a question about closing, not a command to close."""
    return bool(_QUESTION.search(unquoted(text).strip()))


def guard(text: str, steps: list[Step]) -> tuple[list[Step], list[str]]:
    """Drop steps the words rule out. Returns (steps, reasons) so the drop is shown, never silent."""
    neg, q = negated_kinds(text), is_question(text)
    keep, why = [], []
    for st in steps:
        if st.kind in neg:
            why.append(f"not doing '{st.describe()}': you said not to")
        elif q and st.kind not in ("search",):
            why.append(f"not doing '{st.describe()}': that was a question, not a command")
        else:
            keep.append(st)
    return keep, why


def _fragment(text: str, start: int) -> str:
    frag = re.split(r"[.;!?]", text[start:], maxsplit=1)[0]
    return " ".join(frag.split()[:10]).strip(" ,")


def leftovers(text: str, steps: list[Step]) -> list[str]:
    """Parts of the request no step covers, so JAMES can say so instead of reporting success
    (log 02:01:16 'find his son and return his full name', 02:21:23 'copy and paste 6 movies on Macpad')."""
    t = unquoted(text)
    low = t.lower()
    kinds = {s.kind for s in steps}
    neg = negated_kinds(text)
    covers = {"send": {"send", "ask", "message"}, "close": {"close"}, "message": {"message", "ask"},
              "collect": {"collect"}}
    args = " ".join(s.arg.lower() for s in steps)
    out = []
    for kind, have in covers.items():
        if kind in neg or is_question(text):
            continue
        for m in re.finditer(rf"\b(?:{KIND_VERBS[kind]})\b", low):
            words = " ".join(re.findall(r"[a-z0-9']+", low[m.start():])[:2])
            if not (kinds & have) and words not in args:                  # 'search for Close Encounters'
                out.append(_fragment(t, m.start()))
                break
    m = ANSWER.search(low)
    if m and not is_question(text):
        out.append(_fragment(t, m.start()))
    d = _NOTES_DEST.search(low)                  # live 04:22:06 / 04:24:02: '... their names in the Notes app'
    if d and not is_question(text) and not any(
            s.kind == "collect" or s.to == "notes" or (s.kind == "open" and re.search(r"\bnote", s.arg)) for s in steps):
        seps = (" and ", ",", ".", ";", " then ")
        start = max((low.rfind(sp, 0, d.start()) + len(sp) for sp in seps if low.rfind(sp, 0, d.start()) >= 0), default=0)
        words = t[start:d.end()].split()
        out.append(" ".join(words[-12:]).strip(" ,"))
    return list(dict.fromkeys(f for f in out if f))


_NOTES_DEST = re.compile(r"\b(?:in|into|to|on)\s+(?:the\s+|my\s+)?(?:apple\s+)?(?:notes?(?:\s+app)?|note\s*pad|notepad)\b")


def drop_answer_searches(steps: list[Step]) -> list[Step]:
    """'Find his son and return his full name' is a question to answer, not a web search for those words."""
    return [s for s in steps if not (s.kind == "search" and (ANSWER.search(s.arg)
                                                              or re.match(r"^(?:his|her|their|its)\b", s.arg)))]


SURE_KINDS = {"open", "play", "media", "identify"}


def immediate_ok(steps: list[Step]) -> bool:
    """Iteration 3: an uncertain request is confirmed before it runs, whatever it is. The one exception is
    stopping playback ('pause'): it only stops something, and waiting to confirm it would be worse."""
    return bool(steps) and all(st.kind == "media" and st.arg == "pause" for st in steps)


def sure_enough(steps: list[Step], can_open) -> bool:
    """Heard below 0.5 confidence: run only short, checkable commands, and only when every app named really
    exists. Log 02:00:39 'Open the KPDR search for a go-by request.' (0.43) ran a Google search;
    20:40:26 'Open safari.' (0.43) and 21:25:17 'Open chrome and play Despacito.' (0.47) were real commands."""
    return bool(steps) and all(st.kind in SURE_KINDS and (st.kind != "open" or can_open(st.arg)
                                                           or (st.browser and web_target(st.arg)[1] != "search"))
                               for st in steps)


SITE_NAMES = {"amazon": "Amazon", "github": "GitHub", "youtube": "YouTube", "spotify": "Spotify"}


def ungrounded(text: str, steps: list[Step]) -> tuple[list[Step], list[str]]:
    """A model plan may only use a site the person named. Live 04:22:06 and 04:24:02: 'Open IMDB on Chrome and
    list the top 10 grossing movies...' became 'Search Amazon for top 10 grossing movies' (twice)."""
    low = " " + re.sub(r"[^a-z0-9]+", " ", (text or "").lower()) + " "
    keep, why = [], []
    for st in steps:
        if st.kind in ("search", "collect") and st.site:
            names = [k for k, v in SITES.items() if v == st.site] or [st.site]
            if not any(f" {n} " in low for n in names):
                why.append(f"not doing '{st.describe()}': the plan used {SITE_NAMES.get(st.site, st.site)}, "
                           "which you didn't ask for")
                continue
        keep.append(st)
    return keep, why

# ---------- multi-step: close an app, save results to Notes, message someone ----------
_CLOSE = re.compile(r"^(?:close|quit|exit|kill|shut)\s+(?:down\s+)?(?:the\s+)?(.+?)(?:\s+(?:app|application|window))?$")
_COLLECT = re.compile(r"\b(?:copy|save|collect|paste|put|write|note\s+down|list|add|store|keep)\b.{0,60}?"
                      r"\b(?:links?|urls?|repos?|repositor(?:y|ies)|results?|videos?|them|those|these|all)\b", re.I | re.S)
_DEST = re.compile(r"\b(notes?|note\s*pad|notepad|apple notes|clipboard)\b", re.I)
_CUT_COLLECT = re.compile(r"\s+(?:and\s+)?(?:then\s+)?(?:copy|save|collect|paste|put|write|note\s+down|list|store|keep)\b.*$")
_MSG = re.compile(r"\b(instagram|insta|whatsapp|whats app)\b(.*?)\b(?:saying|type|say|send|write|tell\s+(?:her|him|them))\b"
                  r"\s*[:,]?\s*(.+)$", re.I | re.S)
_MSG_WHO = re.compile(r"(?=\b(?:go\s+to|message|dm|text|chat\s+with|to|open|find|with)\s+(?:the\s+chat\s+(?:with|of)\s+)?"
                      r"([a-z][a-z.'_-]{1,30}))", re.I)                  # lookahead: overlapping ("message to sam")
NOT_A_NAME = {"messages", "message", "chats", "chat", "inbox", "dms", "dm", "direct", "instagram", "insta", "whatsapp",
              "chrome", "the", "my", "app", "her", "him", "them", "it", "login", "log", "safari", "profile", "search",
              "to", "a", "an", "on", "in", "me", "someone", "everyone", "and", "then"}
_MSG2 = re.compile(r"^(?:send\s+(?:a\s+)?(?:message|dm|text)\s+to|message|dm|text)\s+([a-z][a-z.'_-]{1,30})\s+(?:on|in|via)\s+"
                   r"(instagram|insta|whatsapp|whats app)\s*(?:saying\b|that\b|:|,)?\s*(.+)$", re.I | re.S)
APP_PRONOUNS = re.compile(r"^(?:(?:the|that|this)\s+)?(?:it|that|this|app|game|window|application|program)$")


def message_step(text: str) -> Step | None:
    """'Open Chrome and login to Instagram and open messages and go to Sam and type Hi, How are you?' ->
    open the DM with Sam and put the words on the clipboard (JAMES never sends a message to a person itself)."""
    t = unabbrev(text.strip())
    m = _MSG.search(t)
    if m:
        region = t[:m.start()] + " " + m.group(2)                  # the name can come before or after the app
        names = [re.sub(r"'s$", "", w) for w in _MSG_WHO.findall(region)]
        names = [w for w in names if w.lower() not in NOT_A_NAME]
        m = m if names else None
    if m:
        svc, who, words = m.group(1), names[-1], m.group(3)
    else:
        m = _MSG2.match(t)
        if not m:
            return None
        who, svc, words = m.group(1), m.group(2), m.group(3)
    svc = "whatsapp" if svc.lower().startswith("whats") else "instagram"
    words = re.sub(r"^[\s\"'“”]+|[\s\"'“”]+$", "", words.strip())
    return Step("message", words, svc, to=who.lower()) if words else None


def collect_step(text: str, steps: list[Step]) -> list[Step]:
    """'Search GitHub for RAG and copy all the repository links to the Notes app' -> one collect step
    (fetch the top results as text, write them into a new note and onto the clipboard)."""
    if not _COLLECT.search(text):
        return steps
    d = _DEST.search(text)
    dest = "clipboard" if d and d.group(1).lower() == "clipboard" else ("notes" if d else "")
    if not dest:
        return steps
    out, done = [], False
    for st in steps:
        if not done and st.kind == "search":
            since = re.findall(r"created:>\S+", st.arg)             # made by clean_search; keep it past the cut
            q = _CUT_COLLECT.sub("", re.sub(r"\s*created:>\S+", "", st.arg)).strip()
            if st.site == "github":
                c = clean_search(q, "github", keep=not since)
                q = re.sub(r"\s+(?:repos?|repositor(?:y|ies)|projects?)$", "", c).strip() or (c if since else q)
                q = " ".join(x for x in (q, *since) if x)
            out.append(Step("collect", q, site=st.site, top=st.top or wants_top(st.arg), to=dest))
            done = True
        elif st.kind == "open" and _DEST.fullmatch(re.sub(r"^(?:the\s+)|\s+app$", "", st.arg)):
            continue                                                # "open notes": the note is made anyway
        elif st.kind == "open" and st.arg.split()[0] in ("notepad", "notes", "note") and len(st.arg.split()) <= 3:
            continue
        else:
            out.append(st)
    if not done:                                                    # "copy those links to notes": the last search
        site = next((SITES[w] for w in SITES if re.search(rf"\b{w}\b", text.lower())), "")
        out.append(Step("collect", "", site=site if site in ("github", "youtube") else "", to=dest))
    return out


def resolve(steps: list[Step], ctx: dict) -> tuple[list[Step], list[str]]:
    """Tie a command to the ones before it: 'close it' = the app JAMES opened last, 'save those links' = the last
    search, 'send it' = the last chat. ctx: {"app": name, "search": (site, query, top), "chat": who, "contact": name}.
    Returns (steps, problems) where problems explain steps that could not be tied to anything."""
    out, problems = [], []
    for st in steps:
        if st.kind == "close" and (not st.arg or APP_PRONOUNS.match(st.arg)):
            if ctx.get("app"):
                st = replace(st, arg=ctx["app"])
            else:
                problems.append("Close what? Say the app name.")
                continue
        if st.kind == "collect" and not st.arg:
            if ctx.get("search"):
                site, q, top = ctx["search"]
                st = replace(st, arg=q, site=st.site or site, top=st.top or top)
            else:
                problems.append("Search for something first, then say: copy the links to Notes.")
                continue
        if st.kind == "message" and st.to in ("her", "him", "them", "") and ctx.get("contact"):
            st = replace(st, to=ctx["contact"])
        if st.kind == "send" and not st.service and ctx.get("chat"):
            st = replace(st, service=ctx["chat"])
        out.append(st)
    return out, problems


def close_step(c: str) -> Step | None:
    m = _CLOSE.match(c)
    if not m:
        return None
    name = re.sub(r"\s+(?:please|now|for me)$", "", m.group(1).strip())
    return Step("close", "" if APP_PRONOUNS.match(name) else name)


# well-known web apps: "open instagram" when there is no Instagram app opens the website (log 01:36:24)
WEB_APPS = {"instagram": "https://www.instagram.com/", "insta": "https://www.instagram.com/",
            "whatsapp": "https://web.whatsapp.com/", "whatsapp web": "https://web.whatsapp.com/",
            "gmail": "https://mail.google.com/", "youtube": "https://www.youtube.com/", "github": "https://github.com/",
            "linkedin": "https://www.linkedin.com/", "facebook": "https://www.facebook.com/", "x": "https://x.com/",
            "twitter": "https://x.com/", "netflix": "https://www.netflix.com/", "amazon": "https://www.amazon.in/",
            "chatgpt": "https://chatgpt.com/", "claude": "https://claude.ai/new", "perplexity": "https://www.perplexity.ai/",
            "gemini": "https://gemini.google.com/app", "google drive": "https://drive.google.com/",
            "drive": "https://drive.google.com/", "google docs": "https://docs.google.com/", "notion": "https://www.notion.so/",
            "spotify": "https://open.spotify.com/", "reddit": "https://www.reddit.com/", "maps": "https://maps.google.com/",
            "wikipedia": "https://en.wikipedia.org/", "imdb": "https://www.imdb.com/"}
_DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|in|org|net|io|ai|dev|app|co|edu|gov|me|tv)(?:/\S*)?$")


def web_target(name: str) -> tuple[str, str]:
    """'open <name> on Chrome': the page to open and how it was found (site | domain | search). Live 04:21:34
    'Open ChatGPT on Chrome' launched the ChatGPT app; 04:23:26 'Open IMDB on Chrome' found nothing."""
    n = re.sub(r"^(?:the\s+)", "", (name or "").strip().lower().rstrip(". "))
    n = re.sub(r"\s+(?:website|web\s*site|site|page|app)$", "", n)
    for k in (n, n.replace(" ", "")):
        if k in WEB_APPS:
            return WEB_APPS[k], "site"
    if _DOMAIN.match(n.replace(" ", "")):
        return "https://" + n.replace(" ", ""), "domain"
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(n), "search"
IG_USER = re.compile(r"^[A-Za-z0-9._]{1,30}$")
WA_PHONE = re.compile(r"^\+?[0-9]{8,15}$")


def message_url(service: str, handle: str, text: str) -> str:
    """The chat with that person (their saved handle), else the inbox. Handles are validated."""
    if service == "instagram":
        return f"https://ig.me/m/{handle}" if IG_USER.match(handle or "") else "https://www.instagram.com/direct/inbox/"
    if service == "whatsapp":
        num = (handle or "").replace(" ", "")
        if WA_PHONE.match(num):
            return f"https://wa.me/{num.lstrip('+')}?text=" + urllib.parse.quote(text)
        return "https://web.whatsapp.com/"
    return ""


def _prompt(p: str) -> str:
    """Tidy a spoken prompt: no leading punctuation or 'then', 'research on' -> 'Research'."""
    p = re.sub(r"^[\s.,;:!?-]+", "", p.strip())
    p = re.sub(r"^(?:then|and|also|now|so)\s+", "", p, flags=re.I)
    p = re.sub(r"^(?:research|search)\s+on\s+", "Research ", p, flags=re.I)
    return (p[0].upper() + p[1:]) if p else p


def _who(name: str) -> str:
    return ASSISTANTS[re.sub(r"\s+", " ", name.lower())]


def ask_step(text: str) -> Step | None:
    """'Open ChatGPT (in Chrome) and research X. Make sure it is under 500 words.' -> one ask step with the whole
    rest as the prompt, in the words that were said (later sentences included, never split on 'and').
    Also 'Open, Claude and ...', 'In the web version of ChatGPT, search X', 'Then Claude will search X'."""
    t = re.sub(r"^\s*(?:hey |ok |okay )?(?:james[,]?\s+)?", "", text.strip(), flags=re.I)
    bm = re.search(r"\b(?:open|in|on|using)\s+(?:the\s+)?(google chrome|chrome|safari|brave|firefox|arc|edge)\b", t, re.I)
    for rx in (_ASK_IN, _ASK_SUBJ):
        m = rx.match(t)
        if m and len(m.group(2).split()) >= 2:
            return Step("ask", _prompt(m.group(2)), _who(m.group(1)), BROWSERS.get(bm.group(1).lower(), "") if bm else "")
    m = _ASK.search(t)
    if not m:
        return None
    before = t[:m.start()].strip().lower()
    if before and not (_PREAMBLE.search(before + " ") or _PREAMBLE.search(before) or re.search(r"[.!?,]$", before)):
        return None
    prompt = _prompt(m.group(3))
    prompt = re.sub(r"^(?:search\s+(?:for|about)\s+)(?=\w)", "Search for ", prompt, flags=re.I) if prompt else prompt
    if len(prompt.split()) < 2:
        return None                                                        # just "open chatgpt": open the app
    b = BROWSERS.get((m.group(2) or "").lower(), "")
    if not b and bm and bm.start() < m.start():                             # "Open Chrome. ... Open ChatGPT. Then ..."
        b = BROWSERS.get(bm.group(1).lower(), "")
    return Step("ask", prompt, _who(m.group(1)), b)


def send_step(text: str) -> Step | None:
    """'Click Enter on the web version of ChatGPT' / 'send it' -> press the chat's send button (log 01:20:13)."""
    m = SEND.match(_clean(text))
    if not m:
        return None
    who = m.group(1)
    return Step("send", "", _who(who) if who else "")


_WHERE = re.compile(r"\s+(?:on|in|from)\s+(?:my|the|this)\s+(?:laptop|mac|macbook|computer|system|pc|desktop)\b")


FILLER = set("""a an the and or but so then also now just please hey james ok okay uh um hmm like well you can could would
will i i'm im me my we us our it its this that these those is are was were be been to of for on in at by with from into
about up some any want wanted wanna need think thinking was guess maybe really actually basically go going gonna let lets
let's do does did done get got make sure laptop mac macbook computer system pc desktop app application there here what
which who how when where why yes no not don't""".split())


# words the rules consume on purpose (ranking words become a sort, 'the game' after 'open X' is dropped)
NOISE_WORDS = {"top", "best", "popular", "good", "latest", "new", "repos", "repo", "repositories", "repository", "repose",
               "reports", "report", "projects", "project", "game", "video", "song", "music", "track", "playing",
               "happening", "happen", "hello", "hi", "thanks", "thank"}


def unabbrev(text: str) -> str:
    """'Mr. News' must not end a sentence (log 00:57:10 split 'play Mr.' from 'News the Boss')."""
    return re.sub(r"\b(Mr|Mrs|Ms|Dr|St|Vs|Jr|Sr|No|Prof|Mt)\.(?=\s)", r"\1", text, flags=re.I)


def uncovered(text: str, steps: list[Step]) -> list[str]:
    """Content words the rule plan did not use: a sign that part of the request was dropped."""
    used = set(" ".join(VERBS).split()) | {"google", "web", "internet"} | NOISE_WORDS
    for st in steps:
        used |= set(re.findall(r"[a-z0-9']+", " ".join((st.arg, st.service, st.site, st.browser)).lower()))
    words = re.findall(r"[a-z0-9']+", _clean(text))
    return [w for w in words if w not in FILLER and w not in used and not w.isdigit()]


def complexity(text: str, steps: list[Step]) -> str:
    """'' if the rule planner can be trusted with this sentence, else the reason it should go to the local model.
    Simple, one-thing commands stay on the fast rules; long, multi-part, half-understood or unparsed requests go to Qwen."""
    text = unabbrev(text)
    words = len(re.findall(r"[a-z0-9']+", text.lower()))
    if not steps:
        return "no rule matched" if words >= 3 else ""
    if any(st.kind in ("ask", "send", "media", "identify", "display", "glass", "close", "collect", "message") for st in steps):
        return ""                                                           # clear intents the rules handle fully
    left = uncovered(text, steps)
    key = set(SITES) | set(" ".join(BROWSERS).split()) | set(ASSISTANTS) | {"spotify", "youtube"}
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", text.strip()) if re.search(r"[a-z]", x, re.I)]
    if len(left) >= 2 or any(w in key for w in left) or (left and len(sentences) >= 3):
        return "rules missed: " + " ".join(left[:4])
    if words >= 25:
        return f"{words} words"
    for st in steps:
        name = _WHERE.sub("", " " + st.arg).strip()
        if st.kind == "open" and (len(name.split()) >= 6 or re.search(
                r"\b(?:and\s+(?:try|then|also|search|find|tell|make|do|ask|write)|try\s+to|so\s+that|because)\b", name)):
            return "open target looks like a whole request"
        if re.match(r"^[^\w]", st.arg or "x"):
            return "step starts with punctuation"
    return ""


def ask_url(who: str, prompt: str) -> str:
    base = ASK_URL.get(who, "")
    return base + urllib.parse.quote(prompt) if base else ASK_HOME.get(who, "")


def with_context(steps: list[Step], text: str) -> list[Step]:
    """'How do I fix the front glass of a Realme 15 Pro? Search it on Chrome.' -> search for the question.
    A search for 'it' / 'that' uses the other sentences as the query (log 01:11:40 searched Google for 'it')."""
    for st in steps:
        if st.kind == "search" and st.arg in PRONOUNS:
            ctx = [x for x in re.split(r"(?<=[.!?])\s+", unabbrev(text).strip())
                   if x.strip() and not re.search(r"\b(?:search|google|look)\b.*\b(?:it|that|this)\b", x, re.I)]
            q = _clean(" ".join(ctx)) if ctx else ""
            q = re.sub(r"^do i\b", "how do i", q).replace("?", "")
            if q:
                st.arg = q
    return steps


def browser_name(spoken: str) -> str:
    s = re.sub(r"^(?:the\s+)?", "", spoken.strip().lower())
    s = re.sub(r"\s+(?:browser|app)$", "", s)
    return BROWSERS.get(s, "")


# ---------- URLs (all public pages) ----------

def youtube_search_url(q: str) -> str:
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q)


def first_youtube_video(html: str) -> str | None:
    """The first video id on a YouTube results page, as a watch URL (which plays straight away)."""
    m = re.search(r'"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})"', html) or \
        re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', html)
    return f"https://www.youtube.com/watch?v={m.group(1)}" if m else None


def spotify_search_uri(q: str) -> str:
    return "spotify:search:" + urllib.parse.quote(q)


def web_search_url(q: str) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)


def site_search_url(site: str, q: str, top: bool = False) -> str:
    """Search on one site (public pages). GitHub 'top/best' sorts by stars."""
    qq = urllib.parse.quote_plus(q)
    if site == "github":
        return f"https://github.com/search?q={qq}&type=repositories" + ("&s=stars&o=desc" if top else "")
    if site == "youtube":
        return youtube_search_url(q)
    if site == "spotify":
        return "https://open.spotify.com/search/" + urllib.parse.quote(q)
    if site == "amazon":
        return "https://www.amazon.in/s?k=" + qq
    return web_search_url(q)


def spotify_track_id(results: list[dict]) -> str | None:
    """First open.spotify.com/track/<22 chars> in web results (strictly validated: it is passed to AppleScript)."""
    for r in results:
        m = re.match(r"^https://open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]{22})(?:[?#].*)?$", r.get("url", ""))
        if m:
            return m.group(1)
    return None


def ud_slug(s: str) -> str:
    """Same slug rule universaldisplay.in uses for its model pages."""
    s = str(s).lower().strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\w\-]+", "", s)
    s = re.sub(r"\-\-+", "-", s)
    return s.strip("-")


def ud_url(model: str, kind: str = "display") -> str:
    """Universal Display (third-party site): the public page for this phone model, or its home page."""
    slug = ud_slug(model) if model else ""
    if not slug:
        return UD_HOME
    return f"{UD_HOME}{'tempered-glass' if kind == 'glass' else 'display'}/{slug}"


def whisper_prompt(app_names, limit_chars: int = 600) -> str:
    """Vocabulary hint for speech to text: the command words plus short installed app names, so
    'Asphalt' or 'Spotify' are heard as names. Whisper reads at most ~224 tokens of prompt."""
    head = ("Open Chrome and play Despacito. Play Despacito on Spotify. Pause the video. Next song. Open Asphalt. "
            "Search GitHub for RAG repos and copy the links to Notes. Close Asphalt. Message on Instagram. "
            "Open ChatGPT and research a topic. Front glass, back glass. "
            "Universal Display for Redmi Note 10. Tempered glass for iPhone 13. Apps: ")
    names, seen = [], set()
    for n in sorted(app_names, key=len):
        k = n.lower()
        if len(n) > 24 or k in seen or not re.search(r"[a-z]", k):
            continue
        seen.add(k)
        names.append(n)
    out = head
    for n in names:
        if len(out) + len(n) + 2 > limit_chars:
            break
        out += n + ", "
    return out.rstrip(", ") + "."
