"""SCOUT: finds the right part on the web.
Only the text query leaves the laptop (never the image). Every query is logged."""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request

from agents import llm
from james_core import egress

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


def query_for(part: str | None, brand: str | None, model: str | None, problem: str | None = None,
              obj: str | None = None) -> str:
    """The text-only web query. A part if the fix needs one, else how to fix the chosen problem.
    (Log 2026-09-25 21:26: the old default assumed 'replacement screen glass' for a water bottle.)"""
    model = (model or "").strip()
    name = model if not brand or brand.lower() in model.lower() else f"{brand} {model}".strip()
    name = name or (obj or "").strip()
    if part:
        return f"{part} for {name}".strip()
    if problem:
        return f"how to fix {problem.lower()} {name}".strip()
    return f"{name} common problems and fixes".strip()


def browser_url(q: str) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)


def parse_ddg(page: str, n: int = 5) -> list[dict]:
    out = []
    for attrs, inner in re.findall(r"<a([^>]*)>(.*?)</a>", page, flags=re.S):
        if "result__a" not in attrs:
            continue
        m = re.search(r'href="([^"]+)"', attrs)
        if not m:
            continue
        href = html.unescape(m.group(1))
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        url = q.get("uddg", [href])[0]
        if url.startswith("//"):
            url = "https:" + url
        if "duckduckgo.com/y.js" in url:      # ads
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        if title:
            out.append({"title": title, "url": url, "site": urllib.parse.urlparse(url).netloc.replace("www.", "")})
        if len(out) >= n:
            break
    return out


def search(q: str, n: int = 5) -> list[dict]:
    """Web results for q, or [] if the web isn't reachable. Refused (EgressBlocked) in maintenance mode."""
    egress.check("consumer_web", "https://html.duckduckgo.com/html/", len(q))
    try:
        req = urllib.request.Request("https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q}),
                                     headers={"User-Agent": UA})
        with egress.urlopen(req, "consumer_web", timeout=8) as r:
            return parse_ddg(r.read().decode("utf-8", "replace"), n)
    except Exception:
        return []


def recommend(q: str, results: list[dict]) -> str:
    """One-line recommendation from the result titles (local model; rules if it's down)."""
    if not results:
        return ""
    titles = "\n".join(f"- {r['title']} ({r['site']})" for r in results)
    try:
        out = llm.chat_json(
            "From these shop and web results, say in one short sentence which part to buy. "
            "Use only what the titles say. Reply JSON {\"recommendation\": str}.",
            f"Need: {q}\nResults:\n{titles}",
            {"type": "object", "properties": {"recommendation": {"type": "string"}}, "required": ["recommendation"]})
        return str(out.get("recommendation", ""))[:160]
    except llm.LLMUnavailable:
        return f"Look for: {q} (top result: {results[0]['site']})"
