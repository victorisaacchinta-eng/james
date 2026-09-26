"""COLLECT: turn a search into a list of links, and put the list where the user asked (Notes, the clipboard).

    "search GitHub for RAG and copy all the repository links to the Notes app"

The links are fetched as TEXT from public endpoints (no browser automation, no scraping behind a login):
  * GitHub  : the public REST search API, GET /search/repositories (unauthenticated: 10 searches a minute)
  * YouTube : the public results page, video ids and titles read from it
  * the web : DuckDuckGo's HTML results (agents/scout.py)
The note is made with a FIXED AppleScript; the titles and links are passed as arguments (argv), never pasted
into the script, so a strange title cannot run anything."""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from james_core import egress

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Version/17.0 Safari/605.1.15")
N = 10


class CollectError(RuntimeError):
    pass


def _get(url: str, headers: dict | None = None, limit: int = 3_000_000) -> str:
    egress.check("consumer_web", url, len(url))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en", **(headers or {})})
    with egress.urlopen(req, "consumer_web", timeout=10) as r:
        return r.read(limit).decode("utf-8", "ignore")


def github_repos(q: str, n: int = N, top: bool = True) -> list[dict]:
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
        {"q": q, "per_page": n, **({"sort": "stars", "order": "desc"} if top else {})})
    try:
        data = json.loads(_get(url, {"Accept": "application/vnd.github+json"}))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise CollectError("GitHub allows 10 searches a minute without a login; try again in a minute") from e
        raise CollectError(f"GitHub search failed ({e.code})") from e
    except (OSError, ValueError) as e:
        raise CollectError(f"GitHub not reachable ({e.__class__.__name__})") from e
    out = []
    for it in data.get("items", [])[:n]:
        u = str(it.get("html_url", ""))
        if not u.startswith("https://github.com/"):
            continue
        desc = str(it.get("description") or "")[:140]
        out.append({"title": str(it.get("full_name", u)), "url": u,
                    "note": f"{int(it.get('stargazers_count') or 0):,} stars" + (f" · {desc}" if desc else "")})
    return out


_YT = re.compile(r'"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})".{0,3000}?"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"',
                 re.S)


def youtube_videos(q: str, n: int = N) -> list[dict]:
    try:
        page = _get("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q))
    except OSError as e:
        raise CollectError(f"YouTube not reachable ({e.__class__.__name__})") from e
    out, seen = [], set()
    for m in _YT.finditer(page):
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        try:
            title = json.loads('"' + m.group(2) + '"')
        except ValueError:
            title = m.group(2)
        out.append({"title": title, "url": f"https://www.youtube.com/watch?v={vid}", "note": ""})
        if len(out) >= n:
            break
    return out


def web_links(q: str, n: int = N) -> list[dict]:
    from agents import scout
    return [{"title": r["title"], "url": r["url"], "note": r.get("site", "")} for r in scout.search(q, n=n)
            if str(r.get("url", "")).startswith(("https://", "http://"))]


def gather(site: str, q: str, n: int = N, top: bool = False) -> list[dict]:
    if site == "github":
        return github_repos(q, n, top=True)          # most-starred first: what "the repos for X" usually means
    if site == "youtube":
        return youtube_videos(q, n)
    return web_links(q, n)


def as_text(title: str, items: list[dict]) -> str:
    lines = [title, ""] + [f"{i}. {it['title']}  {it['url']}" + (f"  ({it['note']})" if it["note"] else "")
                           for i, it in enumerate(items, 1)]
    return "\n".join(lines)


def as_html(title: str, items: list[dict]) -> str:
    rows = "".join(f'<li><a href="{html.escape(it["url"], quote=True)}">{html.escape(it["title"])}</a>'
                   + (f" <i>({html.escape(it['note'])})</i>" if it["note"] else "") + "</li>" for it in items)
    return (f"<h1>{html.escape(title)}</h1><ol>{rows}</ol>"
            f"<p><small>Saved by JAMES {time.strftime('%Y-%m-%d %H:%M')}</small></p>")


NOTE_SCRIPT = """on run argv
  tell application "Notes"
    try
      tell default account to tell folder "Notes" to make new note with properties {name:(item 1 of argv), body:(item 2 of argv)}
    on error
      tell default account to make new note with properties {name:(item 1 of argv), body:(item 2 of argv)}
    end try
    activate
  end tell
  return "ok"
end run"""


def to_notes(title: str, items: list[dict]) -> tuple[bool, str]:
    """A new note in Apple Notes (the default account). Title and body are argv, never script text."""
    if sys.platform != "darwin":
        return False, "Notes needs macOS"
    try:
        r = subprocess.run(["osascript", "-e", NOTE_SCRIPT, title, as_html(title, items)],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode == 0:
        return True, "new note in Notes"
    err = (r.stderr or r.stdout).strip()
    if "-1743" in err or "not allowed" in err.lower():
        return False, "allow Terminal to control Notes: System Settings > Privacy & Security > Automation"
    return False, err[:90] or "Notes refused"
