"""Launcher targets: the allowlist of apps and links JAMES can open, per OS, with real icons.

Nothing outside TARGETS can ever be opened. Each target resolves at start-up to what this
machine actually has:
- macOS: the installed .app (its own icon is read from the bundle and cached), else its web link.
- Windows / Linux: a command or URI if available, else its web link. (Android: later.)
A target with neither is hidden, never faked.

Icons: macOS apps use their bundle icon (sips on the .icns, or a Quick Look thumbnail when the
icon lives in an asset catalogue); Terminal uses the Mac's own. Calculator and Clock always use the
generic icons in assets/icons/generic_*.png, Spotify its floating logo. Web targets use the shipped
site icons (assets/icons/web_*.png). Anything without an icon gets a monogram.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from james_core import egress

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
ICONS = ROOT / "assets" / "icons"
MAC_DIRS = ["/Applications", "/System/Applications", "/System/Applications/Utilities",
            "/Applications/Utilities", os.path.expanduser("~/Applications")]


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    mac: tuple[str, ...] = ()          # .app names, first found wins (label follows the app found)
    win: tuple[str, ...] = ()          # executables or URIs (ms-clock:, spotify:)
    linux: tuple[str, ...] = ()        # executables
    url: str | None = None             # web fallback, or the only way (YouTube, GitHub...)
    icon: str | None = None            # assets/icons/<stem>.png: fallback when the OS gives no icon
    force_icon: bool = False           # always use `icon` (generic Calculator and Clock by request)


def targets(google_account: str | None = None) -> list[Target]:
    q = f"?authuser={google_account}" if google_account else ""
    return [
        Target("youtube", "YouTube", url="https://www.youtube.com", icon="web_youtube"),
        Target("chrome", "Chrome", mac=("Google Chrome",), win=("chrome",), linux=("google-chrome", "chromium"),
               icon="web_chrome"),
        Target("github", "GitHub", url="https://github.com", icon="web_github"),
        Target("terminal", "Terminal", mac=("Terminal",), win=("wt", "cmd"),
               linux=("x-terminal-emulator", "gnome-terminal", "konsole"), icon="generic_terminal"),
        Target("calendar", "Calendar", mac=("Calendar",), win=("outlookcal:",), linux=("gnome-calendar",),
               url=f"https://calendar.google.com/calendar/r{q}", icon="web_calendar"),
        Target("calculator", "Calculator", mac=("Calculator",), win=("calc",), linux=("gnome-calculator", "kcalc"),
               icon="generic_calculator", force_icon=True),
        Target("clock", "Clock", mac=("Clock",), win=("ms-clock:",), linux=("gnome-clocks",),
               icon="generic_clock", force_icon=True),
        Target("music", "Spotify", mac=("Spotify", "Music"), win=("spotify:",), linux=("spotify", "rhythmbox"),
               icon="web_spotify"),
        Target("mail", "Gmail", url=f"https://mail.google.com/mail/{q}", icon="web_gmail"),
        Target("drive", "Drive", url=f"https://drive.google.com/drive/my-drive{q}", icon="web_drive"),
        Target("claude", "Claude", mac=("Claude",), url="https://claude.ai", icon="web_claude"),
    ]


@dataclass
class Resolved:
    id: str
    label: str
    how: str                            # "app" | "cmd" | "url"
    where: str                          # app bundle path, command, or URL
    icon: np.ndarray | None = None      # BGRA float32, square


def _mac_app(names) -> tuple[str, Path] | None:
    for n in names:
        for d in MAC_DIRS:
            p = Path(d) / f"{n}.app"
            if p.exists():
                return n, p
    return None


def _mac_icon(app: Path, out: Path) -> bool:
    """Extract an app's icon to PNG once. Returns True if `out` now exists."""
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
        name = info.get("CFBundleIconFile") or "AppIcon"
        icns = app / "Contents" / "Resources" / (name if name.endswith(".icns") else name + ".icns")
        if icns.exists():
            r = subprocess.run(["sips", "-s", "format", "png", "-Z", "128", str(icns), "--out", str(out)],
                               capture_output=True, timeout=15)
            if r.returncode == 0 and out.exists():
                return True
    except Exception:  # noqa: BLE001  fall through to Quick Look
        pass
    try:
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["qlmanage", "-t", "-s", "128", "-o", td, str(app)], capture_output=True, timeout=20)
            png = Path(td) / (app.name + ".png")
            if png.exists():
                shutil.copy(png, out)
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _load_icon(path: Path) -> np.ndarray | None:
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    a = np.asarray(im).astype(np.float32)
    return np.dstack([a[..., 2], a[..., 1], a[..., 0], a[..., 3]])


def resolve(google_account: str | None = None, platform: str = sys.platform) -> list[Resolved]:
    """What this machine can open, in launcher order. Caches macOS icons in assets/icons/."""
    ICONS.mkdir(parents=True, exist_ok=True)
    out = []
    for t in targets(google_account):
        r = None
        if platform == "darwin" and t.mac:
            hit = _mac_app(t.mac)
            if hit:
                name, path = hit
                label = t.label if t.id not in ("music",) else name        # Spotify, or Music if that is what exists
                r = Resolved(t.id, label, "app", str(path))
                spotify = t.id == "music" and name == "Spotify"       # Spotify: its floating logo, not the app tile
                if not t.force_icon and not spotify:
                    cache = ICONS / f"app_{t.id}_{name.replace(' ', '_')}.png"
                    if cache.exists() or _mac_icon(path, cache):
                        r.icon = _load_icon(cache)
        elif platform.startswith("win") and t.win:
            for c in t.win:
                if c.endswith(":") or shutil.which(c):
                    r = Resolved(t.id, t.label, "cmd", c)
                    break
        elif platform.startswith("linux") and t.linux:
            for c in t.linux:
                if shutil.which(c):
                    r = Resolved(t.id, t.label, "cmd", c)
                    break
        if r is None and t.url:
            r = Resolved(t.id, t.label, "url", t.url)
        if r is None:
            continue                                           # not available here: hidden, not faked
        if r.icon is None and t.icon and not (t.id == "music" and r.label != "Spotify"):
            r.icon = _load_icon(ICONS / f"{t.icon}.png")
        out.append(r)
    return out


def open_target(r: Resolved) -> None:
    """Open one resolved target. Only ever called with an entry from resolve()."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", r.where] if r.how == "app" else ["open", r.where])
    elif os.name == "nt":
        os.startfile(r.where)                                  # noqa: S606  allowlisted target only
    else:
        subprocess.Popen([r.where] if r.how == "cmd" else ["xdg-open", r.where])


# ---------- any installed app (voice: "open <name>") ----------
# Launch-only: JAMES can start any installed application by name. It never runs shell commands,
# opens files or changes settings from speech, so a misheard phrase can at worst open the wrong app.

import re as _re

NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12"}
LEADING = {"the", "a", "an", "my", "please", "james", "hey"}
TRAILING = {"app", "application", "program", "please", "now", "for", "me"}
WHERE = _re.compile(r"\b(?:on|in|from)\s+(?:my|the|this)\s+(?:laptop|mac|macbook|computer|system|pc|desktop)\b")


def _norm(s: str) -> str:
    words = _re.sub(r"[^a-z0-9]+", " ", s.lower()).split()
    return " ".join(NUMBER_WORDS.get(w, w) for w in words)


def clean_query(spoken: str) -> str:
    """'Game of Thrones game on my laptop.' -> 'game of thrones game'. Only edge words and
    'on my laptop'-style phrases are removed, so words inside a title survive."""
    q = _norm(WHERE.sub(" ", spoken.lower()))
    w = q.split()
    while w and w[0] in LEADING:
        w.pop(0)
    while w and w[-1] in TRAILING:
        w.pop()
    return " ".join(w)


def split_camel(name: str) -> str:
    """'GOTKingsroad' -> 'GOT Kingsroad', 'MySQLWorkbench' -> 'My SQL Workbench'."""
    s = _re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return _re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)


def _mac_names(app: Path) -> list[str]:
    """Other names macOS knows this app by (display name, bundle name)."""
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except Exception:  # noqa: BLE001
        return []
    return [str(info[k]) for k in ("CFBundleDisplayName", "CFBundleName") if isinstance(info.get(k), str)]


def installed_apps(platform: str = sys.platform) -> dict[str, str]:
    """Every name an app answers to -> how to launch it (bundle path, Start Menu shortcut, .desktop id).
    One app can appear under several names (file name, display name, camel-case split)."""
    apps: dict[str, str] = {}

    def add(name, where):
        for n in {name, split_camel(name)}:
            if n and n.strip():
                apps.setdefault(n.strip(), where)

    if platform == "darwin":
        for d in MAC_DIRS + ["/System/Library/CoreServices/Applications"]:
            root = Path(d)
            if not root.is_dir():
                continue
            for p in list(root.glob("*.app")) + list(root.glob("*/*.app")):   # also one folder deep (vendor folders)
                add(p.stem, str(p))
                for alias in _mac_names(p):
                    add(alias, str(p))
    elif platform.startswith("win"):
        for base in (os.environ.get("PROGRAMDATA", r"C:\\ProgramData"), os.environ.get("APPDATA", "")):
            menu = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            if menu.is_dir():
                for p in menu.rglob("*.lnk"):
                    add(p.stem, str(p))
    else:
        for base in ("/usr/share/applications", os.path.expanduser("~/.local/share/applications")):
            for p in Path(base).glob("*.desktop") if Path(base).is_dir() else []:
                name = p.stem
                try:
                    for line in p.read_text(errors="ignore").splitlines():
                        if line.startswith("Name="):
                            name = line[5:].strip()
                            break
                except OSError:
                    pass
                add(name, p.stem)
    return apps


def _score(q: str, n: str) -> float:
    import difflib
    if not q or not n:
        return 0.0
    if n == q:
        return 1.0
    if n.startswith(q + " ") or f" {q} " in f" {n} ":
        return 0.92 - 0.002 * len(n)                  # "asphalt" -> "Asphalt 8 Airborne"; shorter names win ties
    if q.startswith(n + " ") or q.replace(" ", "") == n.replace(" ", ""):
        return 0.88                                    # "asphalt 8" -> "Asphalt"
    qw = q.split()
    if len(qw) >= 2:                                   # "game of thrones (game)" -> "GOT Kingsroad"
        for k in (len(qw), len(qw) - 1):
            acro = "".join(w[0] for w in qw[:k])
            if len(acro) >= 2 and acro in n.split():
                return 0.86
    r = difflib.SequenceMatcher(None, q, n).ratio()
    return r if r >= 0.82 else 0.0                      # loose spelling only when it is close ('wisper flow')


# what people call an app on another OS, or by its job -> the Mac app (only used if that app is installed)
MAC_ALIASES = {"command prompt": "Terminal", "cmd": "Terminal", "powershell": "Terminal", "console": "Terminal",
               "file explorer": "Finder", "explorer": "Finder", "files": "Finder", "my files": "Finder",
               "settings": "System Settings", "control panel": "System Settings", "system preferences": "System Settings",
               "task manager": "Activity Monitor", "notepad": "TextEdit", "paint": "Preview", "edge": "Microsoft Edge",
               "whatsapp web": "WhatsApp", "app store": "App Store", "camera": "Photo Booth", "calculator": "Calculator"}
GENERIC_LEAD = {"game", "app", "application", "program", "software", "the"}


def find_app(spoken: str, apps: dict[str, str]) -> tuple[str, str, float] | None:
    """Best installed app for what was said: (name, launch, score), or None if nothing is close enough.
    'the game asphalt' also tries 'asphalt'; 'command prompt' on a Mac means Terminal."""
    q = clean_query(spoken)
    if not q:
        return None
    alias = MAC_ALIASES.get(q)
    if alias and alias in apps:
        return alias, apps[alias], 0.95
    hit = _best(q, apps)
    w = q.split()
    while (hit is None or hit[2] < 0.72) and len(w) > 1 and w[0] in GENERIC_LEAD:     # log 00:32: "open the game asphalt"
        w = w[1:]
        hit = _best(" ".join(w), apps)
    return hit if hit and hit[2] >= 0.72 else None


def _best(q: str, apps: dict[str, str]):
    best = None
    for name, where in apps.items():
        s = _score(q, _norm(name))
        if best is None or s > best[2]:
            best = (name, where, s)
    return best


def open_installed(where: str, platform: str = sys.platform) -> None:
    """Launch one installed app. Arguments are passed as a list (no shell), so nothing spoken is executed."""
    if platform == "darwin":
        subprocess.Popen(["open", "-a", where])
    elif platform.startswith("win"):
        os.startfile(where)                                    # noqa: S606  a Start Menu shortcut found by the scan
    else:
        subprocess.Popen(["gtk-launch", where])


def open_url(url: str, browser_where: str | None = None, platform: str = sys.platform) -> None:
    """Open a web page, in a specific browser app if given. List arguments only.
    The page load sends whatever is in the URL (a search, a prompt) to that site: consumer web family."""
    egress.check("consumer_web", url, len(url))
    if platform == "darwin":
        subprocess.Popen(["open", "-a", browser_where, url] if browser_where else ["open", url])
    elif platform.startswith("win"):
        os.startfile(url)                                      # noqa: S606
    else:
        subprocess.Popen(["xdg-open", url])



def copy_text(text: str, platform: str = sys.platform) -> bool:
    """Put text on the clipboard (the text is data on stdin, never a command)."""
    try:
        if platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), timeout=3, check=True)
        elif platform.startswith("win"):
            subprocess.run(["clip"], input=text.encode("utf-16-le"), timeout=3, check=True)
        else:
            return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def leftover(spoken: str, app_name: str) -> str:
    """'ollama and try to explain X' opened Ollama: the part after the app name ('explain X'), else ''."""
    q, n = clean_query(spoken), _norm(app_name)
    if not q.startswith(n + " "):
        return ""
    rest = _re.sub(r"^(?:and|then|to|in|on|with)\s+(?:(?:try|please|to|then)\s+)*", "", q[len(n):].strip())
    return rest if len(rest.split()) >= 3 else ""


def said_name(spoken: str) -> str:
    """The app part of a long request: 'ollama agent and try to understand X' -> 'ollama agent'."""
    q = clean_query(spoken)
    return _re.split(r"\s+(?:and|then|to|in|on|with|so|because)\s+", q, maxsplit=1)[0] or q
