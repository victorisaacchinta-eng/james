"""MEDIA: play / pause / next / previous for what JAMES started, through FIXED AppleScript (macOS).

Nothing spoken is ever put into a script: the action is one of four fixed words, and a Spotify track
id is only used after it matches ^[A-Za-z0-9]{22}$. First use asks macOS for permission
("Terminal wants to control Spotify / Google Chrome"): click OK once.

YouTube is controlled in Google Chrome by running a fixed line of JavaScript in the YouTube tab. Chrome
only allows that after: Chrome menu View > Developer > Allow JavaScript from Apple Events (one time)."""
from __future__ import annotations

import re
import subprocess
import sys

ACTIONS = ("play", "pause", "next", "previous")
SPOTIFY = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track"}
YT_JS = {"play": "document.querySelector('video').play();'ok'",
         "pause": "document.querySelector('video').pause();'ok'",
         "next": "(document.querySelector('.ytp-next-button')||{click(){}}).click();'ok'",
         "previous": "history.back();'ok'"}
TRACK_ID = re.compile(r"^[A-Za-z0-9]{22}$")


def _osa(script: str, timeout: float = 6.0) -> tuple[bool, str]:
    if sys.platform != "darwin":
        return False, "media control needs macOS"
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def running(app: str) -> bool:
    if app not in ("Spotify", "Google Chrome"):
        return False
    ok, out = _osa(f'application "{app}" is running')
    return ok and out == "true"


def spotify(action: str) -> tuple[bool, str]:
    if action not in SPOTIFY:
        return False, "unknown action"
    if not running("Spotify"):
        return False, "Spotify is not open"
    ok, out = _osa(f'tell application "Spotify" to {SPOTIFY[action]}')
    return ok, "done" if ok else out


def spotify_play_track(track_id: str) -> tuple[bool, str]:
    """Start a track in the Spotify app. Falls back to showing it if AppleScript is refused."""
    if not TRACK_ID.match(track_id or ""):
        return False, "bad track id"
    ok, out = _osa(f'tell application "Spotify" to play track "spotify:track:{track_id}"', timeout=12)
    if ok:
        return True, "playing in Spotify"
    subprocess.Popen(["open", f"spotify:track:{track_id}"])
    return True, "track opened in Spotify (press play)" + (f"; AppleScript: {out[:60]}" if out else "")


def chrome_youtube(action: str) -> tuple[bool, str]:
    if action not in YT_JS:
        return False, "unknown action"
    if not running("Google Chrome"):
        return False, "Chrome is not open"
    js = YT_JS[action].replace('"', '\\"')
    script = ('tell application "Google Chrome"\n'
              ' repeat with w in windows\n  repeat with t in tabs of w\n'
              '   if URL of t contains "youtube.com/watch" then\n'
              f'    return execute t javascript "{js}"\n'
              '   end if\n  end repeat\n end repeat\n return "none"\nend tell')
    ok, out = _osa(script)
    if ok and out == "none":
        return False, "no YouTube video open in Chrome"
    if not ok and "JavaScript" in out:
        return False, "turn on Chrome > View > Developer > Allow JavaScript from Apple Events"
    return ok, "done" if ok else out[:80]


def control(action: str, prefer: str = "") -> tuple[bool, str, str]:
    """Press play/pause/next/previous on the right player: the one asked for, else the last one JAMES used,
    else Spotify if it is open, else YouTube in Chrome. Returns (ok, detail, where)."""
    order = [prefer] if prefer else []
    order += [x for x in ("spotify", "youtube") if x not in order]
    last = ""
    for where in order:
        ok, msg = spotify(action) if where == "spotify" else chrome_youtube(action)
        if ok:
            return True, msg, where
        last = msg
        if prefer and where == prefer and "not open" not in msg:
            return False, msg, where                # asked for this player and it failed for a real reason
    return False, last or "nothing is playing", ""


# ---------- press send in an AI chat tab (Chrome) ----------
CHAT_HOSTS = {"chatgpt": "chatgpt.com", "claude": "claude.ai", "perplexity": "perplexity.ai", "gemini": "gemini.google.com"}
SEND_JS = {
    "chatgpt": "(function(){var b=document.querySelector('#composer-submit-button')||"
               "document.querySelector('button[data-testid=send-button]');"
               "if(!b)return 'nobutton';if(b.disabled)return 'disabled';b.click();return 'sent';})()",
    "claude": "(function(){var b=document.querySelector('button[aria-label=\\'Send message\\']')||"
              "document.querySelector('button[aria-label=\\'Send Message\\']');"
              "if(!b)return 'nobutton';if(b.disabled)return 'disabled';b.click();return 'sent';})()",
}


# The composer box of each chat page. UNVERIFIED selectors: they read text only, and a mismatch means "not sent".
READ_JS = {
    "chatgpt": "(function(){var e=document.querySelector('#prompt-textarea')||document.querySelector('textarea');"
               "return e?(e.value||e.innerText||''):'';})()",
    "claude": "(function(){var e=document.querySelector('div.ProseMirror[contenteditable=true]')||"
              "document.querySelector('div[contenteditable=true]');return e?(e.innerText||''):'';})()",
}


def _q(js: str) -> str:
    return js.replace("\\", "\\\\").replace('"', '\\"')


RS, US = "\x1e", "\x1f"                  # record / unit separators: cannot collide with text people type


def _find_script(host: str, read_js: str) -> str:
    """Every tab whose URL mentions this chat site: '<tab id>US<url>US<text in its composer>RS'. Fixed text only.
    (The host is matched loosely here and EXACTLY in Python, from the returned URL.)"""
    return ('tell application "Google Chrome"\n set out to ""\n'
            ' repeat with w in windows\n  repeat with t in tabs of w\n'
            f'   if URL of t contains "{host}" then\n'
            f'    set out to out & (id of t as text) & (character id 31) & (URL of t) & (character id 31) & '
            f'(execute t javascript "{_q(read_js)}") & (character id 30)\n'
            '   end if\n  end repeat\n end repeat\n return out\nend tell')


def _click_script(read_js: str, send_js: str) -> str:
    """Press send in ONE tab, re-checked at the moment of the click (iteration 3, item F):
    argv 1 = tab id, argv 2 = the URL it had, argv 3 = the composer text it had. If the tab navigated or the
    text changed since it was read, nothing is clicked. The argv values are only compared, never executed."""
    return ('on run argv\n set tid to (item 1 of argv) as number\n tell application "Google Chrome"\n'
            '  repeat with w in windows\n   repeat with t in tabs of w\n'
            '    if (id of t) = tid then\n'
            '     if (URL of t) is not (item 2 of argv) then return "moved"\n'
            f'     set nowtext to (execute t javascript "{_q(read_js)}")\n'
            '     if nowtext is not (item 3 of argv) then return "changed"\n'
            f'     return execute t javascript "{_q(send_js)}"\n'
            '    end if\n   end repeat\n  end repeat\n end tell\n return "notab"\nend run')


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower()).split())


def _host_ok(url: str, host: str) -> bool:
    import urllib.parse
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return urllib.parse.urlsplit(url).scheme == "https" and (h == host or h.endswith("." + host))


def tabs_with_prompt(listing: str, prompt: str, host: str) -> list[tuple[str, str, str]]:
    """[(tab id, url, composer text)] of tabs on exactly this chat site whose box holds the prepared prompt."""
    want = _norm(prompt)[:60]
    out = []
    if not want:
        return out
    for rec in (listing or "").split(RS):
        parts = rec.split(US)
        if len(parts) != 3 or not parts[0].strip().isdigit():
            continue
        tid, url, text = parts[0].strip(), parts[1], parts[2]
        if _host_ok(url, host) and want in _norm(text):
            out.append((tid, url, text))
    return out


def chat_send(who: str, prompt: str, wait_s: float = 0.0) -> tuple[bool, str]:
    """Press send in the ChatGPT / Claude tab that holds exactly the prompt JAMES prepared.

    Bound to one service (exact https host), one tab (by id, URL unchanged), and one prompt (composer text
    unchanged between the read and the click). Two tabs holding the same prompt, a changed tab, a changed
    text: nothing is sent and the click is never retried. wait_s only waits for the page to load (no clicks)."""
    import time
    if who not in SEND_JS:
        return False, f"{who.title() or 'That chat'}: JAMES does not press send there; press Enter" if who \
            else "no chat to send"
    if not _norm(prompt):
        return False, "I haven't prepared a prompt there, so I won't press send; press Enter yourself"
    from james_core import egress
    egress.check("consumer_web", f"https://{CHAT_HOSTS[who]}/", 0)     # pressing send submits the prompt
    t_end = time.monotonic() + wait_s
    hits: list = []
    while True:
        if not running("Google Chrome"):
            return False, "Chrome is not open"
        ok, out = _osa(_find_script(CHAT_HOSTS[who], READ_JS[who]), timeout=6)
        if not ok and "JavaScript" in out:
            return False, "turn on Chrome > View > Developer > Allow JavaScript from Apple Events, or press Enter"
        hits = tabs_with_prompt(out if ok else "", prompt, CHAT_HOSTS[who])
        if hits or time.monotonic() >= t_end:
            break
        time.sleep(0.8)
    if not hits:
        return False, "no chat tab holds the prepared prompt any more, so nothing was sent; press Enter"
    if len(hits) > 1:
        return False, "two chat tabs hold the same prompt; not sure which one, so nothing was sent; press Enter"
    tid, url, text = hits[0]
    ok, out = _osa_argv(_click_script(READ_JS[who], SEND_JS[who]), [tid, url, text], timeout=6)
    if ok and out == "sent":
        return True, f"sent in {'ChatGPT' if who == 'chatgpt' else 'Claude'}"
    if not ok and "JavaScript" in out:
        return False, "turn on Chrome > View > Developer > Allow JavaScript from Apple Events, or press Enter"
    return False, {"notab": "that tab closed; nothing sent",
                   "moved": "that tab went to another page before the click; nothing sent",
                   "changed": "the text in the box changed before the click; nothing sent",
                   "nobutton": "send button not found; press Enter",
                   "disabled": "the prompt box is empty; press Enter after typing"}.get(out, (out or "not sent")[:80])


# ---------- close (quit) an app ----------
QUIT_SCRIPT = """on run argv
  tell application id (item 1 of argv) to quit
  return "ok"
end run"""
NEVER_QUIT = {"com.apple.Terminal", "com.googlecode.iterm2", "com.apple.finder", "com.apple.loginwindow",
              "com.apple.dock", "com.apple.systemuiserver"}          # JAMES runs in Terminal; the rest keep macOS usable


def bundle_id(app_path: str) -> str:
    import plistlib
    from pathlib import Path
    try:
        info = plistlib.loads((Path(app_path) / "Contents" / "Info.plist").read_bytes())
        bid = str(info.get("CFBundleIdentifier") or "")
    except Exception:  # noqa: BLE001
        return ""
    return bid if re.fullmatch(r"[A-Za-z0-9.-]{3,200}", bid) else ""


def quit_app(app_path: str, name: str) -> tuple[bool, str]:
    """Ask an app to quit (like Cmd+Q: it can still ask to save). Never force-quits."""
    bid = bundle_id(app_path)
    if not bid:
        return False, f"could not identify {name}"
    if bid in NEVER_QUIT:
        return False, f"{name} stays open (JAMES or macOS needs it)"
    ok, out = _osa_argv(QUIT_SCRIPT, [bid])
    return ok, f"{name} closed" if ok else out[:80]


def _osa_argv(script: str, args: list[str], timeout: float = 10.0) -> tuple[bool, str]:
    if sys.platform != "darwin":
        return False, "needs macOS"
    try:
        r = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout or r.stderr).strip()
