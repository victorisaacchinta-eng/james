"""CANTUS: slice to the beat with your hands. A JAMES app, in the launcher next to ATLAS.

    python cantus.py                 # camera: pick a song, swing through the blocks (Esc or Q quits)
    python cantus.py --demo          # no camera: pictures of every screen in data/cantus_frames/
    python cantus.py --list          # the songs and their maps
    python cantus.py --map FILE      # analyse one audio file now and print its map

Blocks fly towards you and reach the frame on the beat. Swing through each one the way its arrow points:
red with your left hand, blue with your right (a dot means any direction). Alone with one hand, colours don't
count. Timing gives PERFECT, GOOD or OK; a wrong swing is BAD; a block you let pass is a MISS. Hits in a row
raise the multiplier (x2, x4, x8); a miss drops it one step.

Songs: four public-domain classics built in (synthesised here, see james_core/cantus_songs.py), plus any audio
file you drop into data/cantus/songs/. JAMES maps your file once (james_core/beatmap.py) and caches the map.
Your files stay on your Mac: data/ is never committed.

Hands:  point at a card and pinch = play it  ·  pinch Easy / Normal / Hard = difficulty
        peace sign held = pause  ·  pinch Resume / Songs on the pause and results screens
Keys:   1-9 play that song  ·  D difficulty  ·  R rescan your songs  ·  Space pause  ·  [ ] audio offset
        Enter retry (results)  ·  Esc back  ·  X owner key on/off  ·  Q quit
"""
from __future__ import annotations

import argparse
import atexit
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

import config
import jsil as J
from james_core import beatmap as BM
from james_core import cantus_songs as CS
from james_core import handpose as HP

RED = J.hex_bgr("#E8505B")
BLUE = J.hex_bgr("#4C8DFF")
HAND_COL = (RED, BLUE)
DIFFS = ("easy", "normal", "hard")
JUDGE = (("PERFECT", 0.06, 115, 0.02), ("GOOD", 0.11, 90, 0.01), ("OK", 9.0, 60, 0.005))
MULT_STEPS = (2, 4, 8)                     # hits needed at x1, x2, x4 to go up a level
LEAD_S = 2.4                               # countdown before the music starts
AFPLAY_OK = (".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".caf", ".flac")   # what macOS afplay can open
RANKS = ((0.90, "SS"), (0.80, "S"), (0.65, "A"), (0.50, "B"), (0.35, "C"), (0.20, "D"), (0.0, "E"))


def cfg(name, default):
    return getattr(config, name, default)


def data_dir() -> Path:
    return Path(cfg("CANTUS_DIR", config.DATA / "cantus"))


def rank_for(acc: float) -> str:
    return next(r for a, r in RANKS if acc >= a)


# ---------------- audio ----------------

class Player:
    """Plays one song. On the Mac through `afplay` in its own process (the same reason talk.py does: PortAudio
    output sharing JAMES's process with the microphone crashed Python); pause is SIGSTOP / SIGCONT. Elsewhere
    sounddevice if present, else silent (the game still runs on its own clock)."""

    _all: list = []

    def __init__(self):
        self.proc = None
        self.sd = None
        self.audio, self.sr, self.t_play, self.pos = None, CS.SR, 0.0, 0.0
        Player._all.append(self)

    def start(self, path: str, audio: np.ndarray | None = None, sr: int = CS.SR):
        self.stop()
        afplay = shutil.which("afplay") if sys.platform == "darwin" else None
        if afplay and path and Path(path).exists():
            self.proc = subprocess.Popen([afplay, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "afplay"
        try:
            import sounddevice as sd
            if audio is not None:
                sd.play(audio, sr)
                self.sd, self.audio, self.sr, self.t_play, self.pos = sd, audio, sr, time.monotonic(), 0.0
                return "sounddevice"
        except Exception:  # noqa: BLE001  no audio device: play silently
            pass
        return "silent"

    def pause(self, on: bool):
        if self.proc and self.proc.poll() is None:
            try:
                os.kill(self.proc.pid, signal.SIGSTOP if on else signal.SIGCONT)
            except (OSError, AttributeError):
                pass
        elif self.sd is not None and self.audio is not None:      # sounddevice: stop, then play on from there
            try:
                if on:
                    self.pos += time.monotonic() - self.t_play
                    self.sd.stop()
                else:
                    self.sd.play(self.audio[int(self.pos * self.sr):], self.sr)
                    self.t_play = time.monotonic()
            except Exception:  # noqa: BLE001
                pass

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.kill(self.proc.pid, signal.SIGCONT)
            except (OSError, AttributeError):
                pass
            self.proc.terminate()
        self.proc = None
        if self.sd is not None:
            try:
                self.sd.stop()
            except Exception:  # noqa: BLE001
                pass
            self.sd = None
        self.audio = None

    @classmethod
    def stop_all(cls):
        for p in cls._all:
            p.stop()


atexit.register(Player.stop_all)                                  # never leave afplay running (or stopped) behind


# ---------------- the song library ----------------

@dataclass
class Entry:
    chart: BM.Chart | None
    status: str = "ready"            # ready | analysing | error
    error: str = ""
    path: str = ""
    song: object = None              # CS.Song for built-ins
    play_path: str = ""              # a file the Mac can play, when the original can't be (e.g. .ogg)


class Library:
    """Built-in songs plus the files in data/cantus/songs, mapped in the background."""

    def __init__(self, root: Path | None = None):
        self.root = root or data_dir()
        self.songs_dir = self.root / "songs"
        self.maps = self.root / "maps"
        self.cache = self.root / "cache"
        self.entries: list[Entry] = []
        for s in CS.builtin_songs():
            wav = self.cache / f"{s.id}-{s.fingerprint}.wav"     # a changed melody or synth gets a new file
            self.entries.append(Entry(BM.chart_for_song(s, str(wav)), path=str(wav), song=s))
        self._lock = threading.Lock()
        self._running = False
        self.scores = self._load_scores()

    @property
    def builtin_count(self) -> int:
        return sum(1 for e in self.entries if e.song is not None)

    def scan(self, background: bool = True):
        """Find new audio files and map them (in a thread, so the screen stays live)."""
        self.songs_dir.mkdir(parents=True, exist_ok=True)
        known = {e.path for e in self.entries}
        new = [p for p in sorted(self.songs_dir.iterdir()) if p.suffix.lower() in BM.AUDIO_EXT and str(p) not in known]
        with self._lock:
            for p in new:
                self.entries.append(Entry(None, "analysing", path=str(p)))
            for e in self.entries:                                # a rescan retries files that failed (half-copied?)
                if e.status == "error":
                    e.status, e.error = "analysing", ""
            todo = any(e.status == "analysing" for e in self.entries)
            start = todo and not self._running
            if start:
                self._running = True
        if start:
            if background:
                threading.Thread(target=self._map_all, daemon=True).start()
            else:
                self._map_all()
        return len(new)

    def _map_all(self):
        while True:
            with self._lock:
                e = next((x for x in self.entries if x.status == "analysing"), None)
                if e is None:
                    self._running = False                         # under the lock: a scan right now starts a new run
                    return
            try:
                ch = BM.chart_for_file(Path(e.path), self.maps)
                play = ""
                if sys.platform == "darwin" and Path(e.path).suffix.lower() not in AFPLAY_OK:
                    wav = self.cache / f"{ch.id}.wav"             # the Mac can't play this format: keep a WAV copy
                    if not wav.exists():
                        CS.write_wav(wav, BM.decode(Path(e.path), 44100), 44100)
                    play = str(wav)
                with self._lock:
                    e.chart, e.status, e.play_path = ch, "ready", play
            except Exception as ex:  # noqa: BLE001  one bad file never stops the others
                with self._lock:
                    e.status, e.error = "error", str(ex)

    def audio_for(self, e: Entry) -> tuple[str, np.ndarray | None]:
        """A playable file for this entry (built-ins are rendered once to data/cantus/cache)."""
        if e.song is not None:
            wav = Path(e.path)
            if not wav.exists():
                audio = CS.render(e.song)
                CS.write_wav(wav, audio)
                return str(wav), audio
            return str(wav), None
        path = e.play_path or e.path
        if sys.platform != "darwin":                              # sounddevice needs samples: decode now, before
            try:                                                  # the countdown, not when the music must start
                return path, BM.decode(Path(path), 44100)
            except Exception:  # noqa: BLE001  can't decode here: play silently on the game's clock
                return path, None
        return path, None

    # best scores
    def _load_scores(self) -> dict:
        try:
            return json.loads((self.root / "scores.json").read_text())
        except (OSError, ValueError):
            return {}

    def best(self, chart_id: str, diff: str) -> dict | None:
        return self.scores.get(chart_id, {}).get(diff)

    def record(self, chart_id: str, diff: str, score: int, acc: float) -> bool:
        old = self.best(chart_id, diff)
        if score <= 0 or (old and old["score"] >= score):
            return False
        self.scores.setdefault(chart_id, {})[diff] = {"score": int(score), "acc": round(acc, 4), "rank": rank_for(acc)}
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / "scores.json").write_text(json.dumps(self.scores, indent=1))
        except OSError:
            pass
        return True


# ---------------- hands as blades ----------------

class Blade:
    """One hand's fingertip path: where it went this frame, how fast and which way."""

    def __init__(self):
        self.hist: deque = deque(maxlen=12)
        self.palm = 100.0
        self.seen = -9.0

    def push(self, t: float, p, palm: float):
        if self.hist and t - self.hist[-1][0] > 0.25:
            self.hist.clear()                                   # it went away: no streak across the gap
        elif self.hist and math.dist(p, self.hist[-1][1:]) > 3.0 * max(palm, 40.0) and t - self.hist[-1][0] < 0.05:
            self.hist.clear()                                   # a jump no hand makes in one frame: a different hand
        self.hist.append((t, float(p[0]), float(p[1])))
        self.palm, self.seen = palm, t

    def segment(self):
        if len(self.hist) < 2:
            return None
        (_, x0, y0), (_, x1, y1) = self.hist[-2], self.hist[-1]
        return (x0, y0), (x1, y1)

    def velocity(self, span: float = 0.09):
        if len(self.hist) < 2:
            return 0.0, 0.0
        t1, x1, y1 = self.hist[-1]
        old = next((h for h in list(self.hist)[:-1] if t1 - h[0] <= span), self.hist[-2])
        dt = max(1e-3, t1 - old[0])
        return (x1 - old[1]) / dt, (y1 - old[2]) / dt

    def swinging(self, width: int = 1280) -> bool:
        vx, vy = self.velocity()
        floor = cfg("CANTUS_SWING_PX_S", 420.0) * width / 1280.0          # the same arm speed at any window size
        return math.hypot(vx, vy) >= max(floor, cfg("CANTUS_SWING_PALMS_S", 4.0) * self.palm)


def seg_hits_rect(a, b, rect) -> bool:
    """Liang-Barsky: does segment a-b cross the rectangle (x0, y0, x1, y1)?"""
    x0, y0, x1, y1 = rect
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return False
    return True


# ---------------- one play of one chart ----------------

@dataclass
class Live:
    b: BM.Block
    state: str = "live"              # live | hit | bad | miss
    at: float = 0.0
    judge: str = ""
    cut: tuple = (0.0, 1.0)          # swing direction, for the halves


@dataclass
class Run:
    chart: BM.Chart
    diff: str
    blocks: list = field(default_factory=list)
    score: int = 0
    combo: int = 0
    max_combo: int = 0
    level: int = 0                   # 0..3 -> x1 x2 x4 x8
    progress: int = 0
    energy: float = 0.5
    counts: dict = field(default_factory=lambda: {"PERFECT": 0, "GOOD": 0, "OK": 0, "BAD": 0, "MISS": 0})
    max_score: int = 0
    failed: bool = False

    def __post_init__(self):
        self.blocks = [Live(b) for b in self.chart.blocks.get(self.diff, [])]
        lvl, prog, total = 0, 0, 0
        self.best_so_far = [0]                                   # the score if every block so far were PERFECT
        for _ in self.blocks:
            total += JUDGE[0][2] * (1, 2, 4, 8)[lvl]
            self.best_so_far.append(total)
            if lvl < 3:
                prog += 1
                if prog >= MULT_STEPS[lvl]:
                    lvl, prog = lvl + 1, 0
        self.max_score = max(1, total)

    @property
    def mult(self) -> int:
        return (1, 2, 4, 8)[self.level]

    @property
    def accuracy(self) -> float:
        return self.score / self.max_score

    @property
    def live_accuracy(self) -> float:
        """Score over what a perfect run would have by now: the number to watch while playing."""
        return self.score / max(1, self.best_so_far[self.done])

    @property
    def done(self) -> int:
        return sum(1 for x in self.blocks if x.state != "live")

    def hit(self, lv: Live, dt: float, now_s: float, cut) -> int:
        name, _, base, gain = next(j for j in JUDGE if abs(dt) <= j[1])
        pts = base * self.mult
        self.score += pts
        self.combo += 1
        self.max_combo = max(self.max_combo, self.combo)
        self.counts[name] += 1
        self.energy = min(1.0, self.energy + gain)
        if self.level < 3:
            self.progress += 1
            if self.progress >= MULT_STEPS[self.level]:
                self.level, self.progress = self.level + 1, 0
        lv.state, lv.at, lv.judge, lv.cut = "hit", now_s, name, cut
        return pts

    def fault(self, lv: Live, kind: str, now_s: float, cut=(0.0, 1.0)):
        self.counts[kind] += 1
        self.combo = 0
        self.level, self.progress = max(0, self.level - 1), 0
        self.energy = max(0.0, self.energy - (0.15 if kind == "MISS" else 0.1))
        lv.state, lv.at, lv.judge, lv.cut = kind.lower(), now_s, kind, cut


# ---------------- geometry ----------------

def stage(W: int, H: int) -> dict:
    """The hit frame (4 x 2 cells) in the lower middle, and the vanishing point blocks come from."""
    gw, gh = W * 0.56, H * 0.40
    x0, y0 = (W - gw) / 2, H * 0.40
    return {"x0": x0, "y0": y0, "cw": gw / BM.COLS, "ch": gh / BM.ROWS, "vp": (W / 2, H * 0.17)}


def block_rect(g: dict, b: BM.Block, dt: float, approach: float):
    """Screen square of a block `dt` seconds before its hit time (negative = after)."""
    p = 1.0 - dt / approach
    s = 1.0 / (1.0 + 3.0 * max(0.0, 1.0 - p)) * (1.0 + 0.25 * max(0.0, p - 1.0))
    cx = g["x0"] + (b.col + 0.5) * g["cw"]
    cy = g["y0"] + (b.row + 0.5) * g["ch"]
    vx, vy = g["vp"]
    x, y = vx + (cx - vx) * s, vy + (cy - vy) * s
    half = 0.39 * min(g["cw"], g["ch"]) * s
    return x, y, half


# ---------------- the app ----------------

class CantusApp:
    """The CANTUS screen: song select, play, pause, results. Hands and keys; nothing leaves this window."""

    def __init__(self, library: Library | None = None):
        self.lib = library or Library()
        self.active = False
        self.screen = "select"
        self.diff = cfg("CANTUS_DIFFICULTY", "normal")
        self.player = Player()
        self.run: Run | None = None
        self.entry: Entry | None = None
        self.start_at = 0.0                     # monotonic time of song second 0
        self.audio_started = False
        self.paused_at = None
        self.offset = cfg("CANTUS_AUDIO_OFFSET_MS", 80) / 1000.0
        self.blades = (Blade(), Blade())
        self.two_seen = -9.0
        self.fx: list = []                      # (kind, t0, data)
        self.buttons: list = []                 # (key, rect) of the current screen
        self.hover = None
        self._press = None
        self._was_pinch = False
        self.poses = HP.PoseTracker()
        self.msg = None
        self.header = J.Header()
        self.new_best = False
        self.sel = 0
        self._last_t = None

    # ---------- open / close ----------
    def open(self, now: float | None = None):
        now = time.monotonic() if now is None else now
        self.active, self.screen = True, "select"
        self._was_pinch, self._press = True, None              # a pinch carried in from the launcher must end
        n = self.lib.scan(background=True)
        if n:
            self.flash(f"Mapping {n} new song{'s' if n > 1 else ''} from data/cantus/songs", now, 4)

    def close(self):
        self.player.stop()
        self.active, self.run = False, None

    def flash(self, text: str, now: float | None = None, secs: float = 3.0):
        self.msg = (text, (time.monotonic() if now is None else now) + secs)

    # ---------- song clock ----------
    def song_t(self, now: float) -> float:
        if self.paused_at is not None:
            now = self.paused_at
        return now - self.start_at - self.offset

    def play(self, i: int, now: float):
        e = self.lib.entries[i]
        if e.status != "ready" or e.chart is None:
            self.flash("Still mapping that song" if e.status == "analysing" else f"Can't play it: {e.error}", now, 4)
            return False
        if not e.chart.blocks.get(self.diff):
            self.flash("No blocks at this difficulty", now)
            return False
        self.entry, self.sel = e, i
        self.run = Run(e.chart, self.diff)
        self.fx.clear()
        self.screen, self.paused_at, self.audio_started = "play", None, False
        self.start_at = now + LEAD_S
        self._audio = self.lib.audio_for(e)                    # renders a built-in once, before the countdown
        self.new_best = False
        return True

    def pause(self, on: bool, now: float):
        if self.screen not in ("play", "paused"):
            return
        if on and self.paused_at is None:
            self.paused_at, self.screen = now, "paused"
            self.player.pause(True)
        elif not on and self.paused_at is not None:
            self.start_at += now - self.paused_at
            self.paused_at, self.screen = None, "play"
            self.poses.reset()                                    # the peace sign that paused must be made again
            self.player.pause(False)

    def finish(self, now: float):
        self.player.stop()
        r = self.run
        self.new_best = self.lib.record(r.chart.id, r.diff, r.score, r.accuracy) if not r.failed else False
        self.screen = "results"

    def to_select(self):
        self.player.stop()
        self.run, self.screen, self.paused_at = None, "select", None

    # ---------- input ----------
    def update(self, view, now: float):
        if not self.active or view is None:
            return
        hands = [h for h in [getattr(view, "hand", None)] + list(getattr(view, "others", None) or []) if HP.valid(h)]
        tip = getattr(view, "fingertip", None)
        pinching = bool(getattr(view, "pinching", False))
        driver = getattr(view, "hand", None)
        pose = self.poses.update(driver if HP.valid(driver) else None, pinching, now) if (tip or driver) else None
        if self.screen in ("play", "paused"):
            self._play_update(hands, pose, now)
        if self.screen != "play":
            self._ui(tip, getattr(view, "pinch_point", None) or tip, pinching, now)
        self._was_pinch = pinching

    def _ui(self, tip, aim, pinching, now):
        self.hover = self._hit_button(tip)
        if pinching and not self._was_pinch:
            self._press = self._hit_button(aim)
        elif not pinching and self._was_pinch and self._press:
            if self._hit_button(aim) == self._press or self._hit_button(tip) == self._press:
                self.activate(self._press, now)
            self._press = None
        elif not pinching:
            self._press = None

    def _hit_button(self, p):
        if p is None:
            return None
        for key, (x, y, w, h) in self.buttons:
            if x <= p[0] <= x + w and y <= p[1] <= y + h:
                return key
        return None

    def activate(self, key: str, now: float):
        if key.startswith("song:"):
            self.play(int(key[5:]), now)
        elif key.startswith("diff:"):
            self.diff = key[5:]
        elif key == "resume":
            self.pause(False, now)
        elif key == "retry" and self.entry is not None:
            self.play(self.lib.entries.index(self.entry), now)
        elif key == "songs":
            self.to_select()
        elif key == "close":
            self.close()

    def _play_update(self, hands, pose, now):
        if self.screen == "paused":
            return
        # peace sign held = pause
        if pose == "peace" and now - self.poses.since >= cfg("CANTUS_PAUSE_HOLD_S", 0.8):
            self.pause(True, now)
            return
        t = self.song_t(now)
        if not self.audio_started and t >= -self.offset:
            path, audio = self._audio
            self.player.start(path, audio)
            self.audio_started = True
        # blades: leftmost hand is red, rightmost blue; one hand plays whichever side it is on
        W = getattr(self, "_W", 1280)
        hands = sorted(hands, key=lambda h: HP.palm_centre(h)[0])[:2]
        if len(hands) == 2:
            self.two_seen = now
            pairs = ((0, hands[0]), (1, hands[1]))
        elif len(hands) == 1:
            pairs = ((self._side_for(hands[0], now, W), hands[0]),)
        else:
            pairs = ()
        for side, h in pairs:
            self.blades[side].push(now, h[8], HP.palm_size(h))
        strict = now - self.two_seen < 1.5                       # colours count only when both hands play
        run, cfgd = self.run, BM.DIFFICULTY[self.diff]
        g = stage(W, getattr(self, "_H", 720))
        for lv in run.blocks:
            if lv.state != "live":
                continue
            dt = lv.b.t - t
            if dt < -cfgd["window"]:
                run.fault(lv, "MISS", t)
                continue
            if dt > cfgd["window"]:
                continue
            x, y, half = block_rect(g, lv.b, dt, cfgd["approach"])
            rect = (x - half * 1.35, y - half * 1.35, x + half * 1.35, y + half * 1.35)
            for side, _ in pairs:
                bl = self.blades[side]
                seg = bl.segment()
                if seg is None or not bl.swinging(W) or not seg_hits_rect(seg[0], seg[1], rect):
                    continue
                vx, vy = bl.velocity()
                sp = math.hypot(vx, vy) or 1.0
                cut = (vx / sp, vy / sp)
                want = BM.DIRS[lv.b.dir]
                ok_dir = want is None or (cut[0] * want[0] + cut[1] * want[1]) >= 0.5
                ok_hand = (not strict) or side == lv.b.hand
                if ok_dir and ok_hand:
                    pts = run.hit(lv, dt, t, cut)
                    self.fx.append(("pop", now, (x, y - half - 10, f"{lv.judge}  +{pts}", HAND_COL[lv.b.hand])))
                else:
                    run.fault(lv, "BAD", t, cut)
                    self.fx.append(("pop", now, (x, y - half - 10, "WRONG HAND" if not ok_hand else "BAD", J.ERROR)))
                self.fx.append(("cut", now, (x, y, half, cut, HAND_COL[lv.b.hand], lv.state == "hit")))
                break
        if run.energy <= 0 and not cfg("CANTUS_NO_FAIL", True):
            run.failed = True
            self.finish(now)
            return
        last = run.blocks[-1].b.t if run.blocks else 0.0
        if t > max(last + 1.5, min(run.chart.duration, last + 4.0)):
            self.finish(now)

    def _side_for(self, h, now: float, W: int) -> int:
        """Which blade a lone hand is: the one whose last fingertip it is nearest to (when either is fresh),
        so a partner hand dropping out for a frame never hands its streak to the other one."""
        tip = h[8]
        fresh = [(math.dist(tip, b.hist[-1][1:]), side) for side, b in enumerate(self.blades)
                 if b.hist and now - b.hist[-1][0] < 0.3]
        if fresh:
            return min(fresh)[1]
        return 0 if HP.palm_centre(h)[0] < W / 2 else 1

    def key(self, c: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if not self.active:
            return False
        if self.screen == "select":
            if c.isdigit() and c != "0":
                i = int(c) - 1
                if i < len(self.lib.entries):
                    self.play(i, now)
                return True
            if c == "d":
                self.diff = DIFFS[(DIFFS.index(self.diff) + 1) % 3]
                return True
            if c == "r":
                n = self.lib.scan(background=True)
                self.flash(f"Found {n} new song{'s' if n != 1 else ''}" if n else "No new songs in data/cantus/songs", now)
                return True
            if c == "esc":
                self.close()
                return True
        elif self.screen in ("play", "paused"):
            if c == " ":
                self.pause(self.screen == "play", now)
                return True
            if c == "esc":
                self.to_select()
                return True
            if c in "[]":
                self.offset += 0.01 if c == "]" else -0.01
                self.flash(f"Audio offset {self.offset * 1000:+.0f} ms (blocks {'later' if c == ']' else 'earlier'})", now, 2)
                return True
        elif self.screen == "results":
            if c in ("\r", "\n", "enter"):
                self.activate("retry", now)
                return True
            if c == "esc":
                self.to_select()
                return True
        return False

    # ---------- drawing ----------
    def render(self, frame, view=None, now: float | None = None):
        now = time.monotonic() if now is None else now
        H, W = frame.shape[:2]
        self._W, self._H = W, H
        self.buttons = []
        if self.screen in ("play", "paused"):
            cv2.convertScaleAbs(frame, dst=frame, alpha=0.5)
            self._draw_play(frame, now)
            if self.screen == "paused":
                self._draw_pause(frame)
        elif self.screen == "results":
            cv2.convertScaleAbs(frame, dst=frame, alpha=0.3)
            self._draw_results(frame)
        else:
            cv2.convertScaleAbs(frame, dst=frame, alpha=0.32)
            self._draw_select(frame)
        x = self.header.draw(frame)
        J.pill(frame, "Cantus", x, 31, RED)
        J.status_right(frame, [(J.SUCCESS, "OFFLINE"), (None, self.diff.upper()), (None, time.strftime("%H:%M"))], y=25)
        self._draw_hands(frame, view)
        if self.msg and now < self.msg[1]:
            J.toast(frame, self.msg[0], J.CONFIRM, y_bottom=H - 44)
        J.hints(frame, {"select": "1-9 play · D difficulty · R rescan songs · Esc close · Q quit",
                        "play": "Space pause · [ ] audio offset · Esc songs",
                        "paused": "Space resume · Esc songs",
                        "results": "Enter retry · Esc songs"}[self.screen], True)
        return frame

    def _button(self, img, key, x, y, w, h, label, on=False, col=None):
        hov = self.hover == key
        J.panel(img, x, y, w, h, 0.95, J.SURFACE_2 if (hov or on) else J.SURFACE,
                border=(col or J.CYAN) if (hov or on) else J.RULE)
        J.text(img, label, x + w // 2, y + h // 2 + 5, 13, J.TEXT if (hov or on) else J.TEXT_2, "semi", anchor="c")
        self.buttons.append((key, (x, y, w, h)))

    def _draw_select(self, img):
        H, W = img.shape[:2]
        small = H < 600 or W < 900
        m = 16 if small else 40
        x0, y = m, 52 + (30 if small else 44)
        J.label(img, "Slice to the beat", x0, y, RED)
        J.text(img, "Pick a song", x0, y + 30, 22 if small else 28, J.TEXT, "semi")
        bw, bh = (64, 32) if small else (104, 40)
        bx = W - m - 3 * bw - 2 * 6 - (bw + 14)
        for i, d in enumerate(DIFFS):
            self._button(img, f"diff:{d}", bx + i * (bw + 6), y - 4, bw, bh, d.capitalize(), on=d == self.diff, col=RED)
        self._button(img, "close", W - m - bw, y - 4, bw, bh, "Close")
        J.text(img, J.fit("Swing through each block the way its arrow points, as it reaches the frame. "
                          "Red: left hand. Blue: right hand.", W - 2 * m, 12.5 if small else 13),
               x0, y + (50 if small else 60), 12.5 if small else 13, J.TEXT_2)
        cols = 4 if (small or W >= 1000) else 2
        cols = min(cols, max(1, (W - 2 * m) // 140))
        gap = 8 if small else 14
        cw = int((W - 2 * m - gap * (cols - 1)) / cols)
        ch = 70 if small else 108
        top = y + (66 if small else 92)
        n_fit = max(1, ((H - 30 - 70) - top) // (ch + gap)) * cols
        with self.lib._lock:
            entries = list(self.lib.entries)                      # the mapping thread may append meanwhile
        for i, e in enumerate(entries[:n_fit]):
            cx, cy = m + (i % cols) * (cw + gap), top + (i // cols) * (ch + gap)
            self._card(img, i, e, cx, cy, cw, ch, small)
        shown = min(len(entries), n_fit)
        fy = H - 30 - (16 if small else 54)
        where = self.lib.songs_dir
        where = where.relative_to(config.ROOT) if where.is_relative_to(config.ROOT) else where
        more = f"  ·  +{len(entries) - shown} more (keys reach the first nine)" if shown < len(entries) else ""
        J.text(img, J.fit(f"Your songs: drop MP3, M4A or WAV files into {where} and press R. They stay on your Mac.{more}",
                          W - 2 * m, 12), m, fy, 12, J.TEXT_2)

    def _card(self, img, i, e: Entry, x, y, w, h, small=False):
        key = f"song:{i}"
        hov = self.hover == key
        J.panel(img, x, y, w, h, 0.94, J.SURFACE_2 if hov else J.SURFACE, border=RED if hov else J.RULE,
                accent=RED if e.song is not None else BLUE)
        J.text(img, f"{i + 1}" if i < 9 else "", x + w - 14, y + 24, 12, J.MUTED, "mono", anchor="r")
        if e.chart is not None:
            c = e.chart
            ts = 14 if small else 17
            J.text(img, J.fit(c.title, w - 40, ts), x + 14, y + (22 if small else 30), ts, J.TEXT, "semi")
            J.text(img, J.fit(c.artist + (f"  ·  {e.song.year}" if e.song is not None else ""), w - 28, 11.5 if small else 12.5),
                   x + 14, y + (40 if small else 50), 11.5 if small else 12.5, J.TEXT_2)
            n = len(c.blocks.get(self.diff, []))
            meta = f"{int(c.duration // 60)}:{int(c.duration % 60):02d}  ·  {c.bpm:.0f} BPM  ·  {n} blocks"
            if not small:
                J.text(img, meta, x + 18, y + 76, 11, J.MUTED, "mono")
            best = self.lib.best(c.id, self.diff)
            tag = "BUILT IN" if e.song is not None else "YOUR FILE"
            J.text(img, tag, x + 18, y + h - 12, 9.5, RED if e.song is not None else BLUE, "semi", track=1.0)
            if best:
                J.text(img, f"BEST {best['rank']}  {best['score']:,}", x + w - 16, y + h - 12, 11, J.CONFIRM, "mono", anchor="r")
        else:
            J.text(img, J.fit(Path(e.path).stem, w - 60, 16), x + 18, y + 30, 16, J.TEXT_2, "semi")
            if e.status == "analysing":
                J.text(img, "Mapping the beat...", x + 18, y + 52, 12.5, J.CONFIRM)
            else:
                for k, ln in enumerate(J.wrap(e.error, w - 36, 11.5)[:3]):
                    J.text(img, ln, x + 18, y + 52 + 15 * k, 11.5, J.ERROR)
        self.buttons.append((key, (x, y, w, h)))

    def _draw_play(self, img, now):
        H, W = img.shape[:2]
        run = self.run
        cfgd = BM.DIFFICULTY[run.diff]
        g = stage(W, H)
        t = self.song_t(now)
        # lanes: the frame and its lines back to the vanishing point
        vx, vy = g["vp"]
        gx1, gy1 = g["x0"] + 4 * g["cw"], g["y0"] + 2 * g["ch"]
        for cx in range(5):
            px = int(g["x0"] + cx * g["cw"])
            cv2.line(img, (int(vx), int(vy)), (px, int(gy1)), J.RULE, 1, cv2.LINE_AA)
        for k in range(1, 4):
            s = 1.0 / (1.0 + 3.0 * (1 - k / 4))
            yy = int(vy + (gy1 - vy) * s)
            xa, xb = int(vx + (g["x0"] - vx) * s), int(vx + (gx1 - vx) * s)
            cv2.line(img, (xa, yy), (xb, yy), J.RULE, 1, cv2.LINE_AA)
        cv2.rectangle(img, (int(g["x0"]), int(g["y0"])), (int(gx1), int(gy1)), J.MUTED, 1, cv2.LINE_AA)
        for cx in range(1, 4):
            px = int(g["x0"] + cx * g["cw"])
            cv2.line(img, (px, int(g["y0"])), (px, int(g["y0"]) + 8), J.MUTED, 1)
            cv2.line(img, (px, int(gy1)), (px, int(gy1) - 8), J.MUTED, 1)
        # blocks, far first
        live = [lv for lv in run.blocks if lv.state == "live" and lv.b.t - t <= cfgd["approach"]]
        for lv in sorted(live, key=lambda z: -(z.b.t - t)):
            dt = lv.b.t - t
            x, y, half = block_rect(g, lv.b, dt, cfgd["approach"])
            self._draw_block(img, x, y, half, lv.b, dt)
        # effects
        keep = []
        for kind, t0, d in self.fx:
            age = now - t0
            if kind == "cut" and age < 0.4:
                self._draw_halves(img, d, age)
                keep.append((kind, t0, d))
            elif kind == "pop" and age < 0.7:
                x, y, txt, col = d
                J.text(img, txt, x, y - 30 * age, 13, col, "semi", anchor="c", opacity=max(0.0, 1 - age / 0.7))
                keep.append((kind, t0, d))
        self.fx = keep
        for lv in run.blocks:                                      # a missed block flashes grey where it passed
            if lv.state == "miss" and 0 <= t - lv.at < 0.35:
                x, y, half = block_rect(g, lv.b, lv.b.t - t, cfgd["approach"])
                J.text(img, "MISS", x, y + 5, 13, J.MUTED, "semi", anchor="c")
        # countdown
        if t < 0:
            n = int(math.ceil(-t))
            if n <= 3:
                J.text(img, str(n), W // 2, int(H * 0.30), 64, J.TEXT, "semi", anchor="c")
        self._draw_hud(img, t)

    def _draw_block(self, img, x, y, half, b: BM.Block, dt):
        col = HAND_COL[b.hand]
        x0, y0, x1, y1 = int(x - half), int(y - half), int(x + half), int(y + half)
        fill = tuple(int(c * 0.32) for c in col)
        cv2.rectangle(img, (x0, y0), (x1, y1), fill, -1)
        near = abs(dt) <= BM.DIFFICULTY[self.run.diff]["window"]
        cv2.rectangle(img, (x0, y0), (x1, y1), col, 2 if near else 1, cv2.LINE_AA)
        r = half * 0.55
        if b.dir == "A":
            cv2.circle(img, (int(x), int(y)), max(2, int(half * 0.22)), J.TEXT, -1, cv2.LINE_AA)
            return
        ux, uy = BM.DIRS[b.dir]
        px, py = -uy, ux
        tipx, tipy = x + ux * r, y + uy * r
        pts = np.array([[tipx, tipy], [x - ux * r * 0.2 + px * r, y - uy * r * 0.2 + py * r],
                        [x - ux * r * 0.2 - px * r, y - uy * r * 0.2 - py * r]], np.int32)
        cv2.fillConvexPoly(img, pts, J.TEXT, cv2.LINE_AA)

    def _draw_halves(self, img, d, age):
        x, y, half, cut, col, good = d
        ux, uy = cut
        nx, ny = -uy, ux                                           # the halves part across the swing
        k = age / 0.4
        off = half * 1.2 * k
        c = tuple(int(v * (1 - k * 0.7)) for v in (col if good else J.MUTED))
        for sgn in (1, -1):
            cx, cy = x + nx * off * sgn + ux * off * 0.4, y + ny * off * sgn + uy * off * 0.4
            pts = []
            for a, b2 in ((-1, 0), (1, 0), (1, 1), (-1, 1)):       # a half square: along the cut, then outward
                along, out = a * half, b2 * half * sgn
                pts.append([cx + ux * along + nx * out * 0.98, cy + uy * along + ny * out * 0.98])
            cv2.polylines(img, [np.array(pts, np.int32)], True, c, 2, cv2.LINE_AA)

    def _draw_hud(self, img, t):
        H, W = img.shape[:2]
        run = self.run
        y = 52 + 34
        J.text(img, run.chart.title, 40, y, 18, J.TEXT, "semi")
        J.text(img, f"{run.chart.artist}  ·  {run.diff}", 40, y + 20, 12, J.TEXT_2)
        J.text(img, f"{run.score:,}", W // 2, y + 6, 30, J.TEXT, "mono", anchor="c")
        J.text(img, f"{run.live_accuracy * 100:.1f}%", W // 2, y + 26, 12, J.TEXT_2, "mono", anchor="c")
        J.text(img, f"x{run.mult}", W - 40, y, 26, HAND_COL[1] if run.level else J.TEXT_2, "semi", anchor="r")
        J.text(img, f"{run.combo} combo", W - 40, y + 20, 12, J.TEXT_2, anchor="r")
        dur = max(1.0, run.chart.duration)
        bw = W - 80
        cv2.line(img, (40, y + 34), (40 + bw, y + 34), J.RULE, 2)
        cv2.line(img, (40, y + 34), (40 + int(bw * min(1.0, max(0.0, t) / dur)), y + 34), J.TEXT_2, 2)
        ey = H - 30 - 16                                           # energy: left = empty, right = full
        cv2.line(img, (W // 2 - 160, ey), (W // 2 + 160, ey), J.RULE, 4)
        ecol = J.ERROR if run.energy < 0.25 else (J.CONFIRM if run.energy < 0.5 else J.SUCCESS)
        cv2.line(img, (W // 2 - 160, ey), (W // 2 - 160 + int(320 * run.energy), ey), ecol, 4)

    def _draw_pause(self, img):
        H, W = img.shape[:2]
        J.panel(img, W // 2 - 220, H // 2 - 90, 440, 180, 0.95)
        J.text(img, "Paused", W // 2, H // 2 - 40, 26, J.TEXT, "semi", anchor="c")
        self._button(img, "resume", W // 2 - 200, H // 2, 190, 52, "Resume")
        self._button(img, "songs", W // 2 + 10, H // 2, 190, 52, "Songs")

    def _draw_results(self, img):
        H, W = img.shape[:2]
        run = self.run
        acc = run.accuracy
        rk = "FAILED" if run.failed else rank_for(acc)
        cx = W // 2
        small = H < 600
        J.label(img, "Results", 16 if small else 40, 52 + (26 if small else 44), RED)
        J.text(img, J.fit(run.chart.title, W // 2 - 40, 18 if small else 26), 16 if small else 40, 52 + (48 if small else 78),
               18 if small else 26, J.TEXT, "semi")
        if not small:
            J.text(img, f"{run.chart.artist}  ·  {run.diff}", 40, 52 + 100, 13, J.TEXT_2)
        big = 72 if H < 600 else 120
        J.text(img, rk, cx, int(H * 0.46), big if len(rk) <= 2 else big // 2,
               J.CONFIRM if rk in ("SS", "S") else J.TEXT, "semi", anchor="c")
        J.text(img, f"{run.score:,}", cx, int(H * 0.46) + 46, 26, J.TEXT, "mono", anchor="c")
        J.text(img, f"{acc * 100:.1f}% accuracy  ·  best combo {run.max_combo}" + ("  ·  NEW BEST" if self.new_best else ""),
               cx, int(H * 0.46) + 72, 13, J.CONFIRM if self.new_best else J.TEXT_2, anchor="c")
        names = ("PERFECT", "GOOD", "OK", "BAD", "MISS")
        sx = cx - 2 * 110
        for k, nm in enumerate(names):
            J.text(img, str(run.counts[nm]), sx + k * 110, int(H * 0.46) + 116, 20, J.TEXT, "mono", anchor="c")
            J.text(img, nm, sx + k * 110, int(H * 0.46) + 136, 10, J.MUTED, "semi", anchor="c", track=1.0)
        by = int(H * 0.46) + 162
        self._button(img, "retry", cx - 200, by, 190, 52, "Play again")
        self._button(img, "songs", cx + 10, by, 190, 52, "Songs")

    def _draw_hands(self, img, view):
        if view is None:
            return
        for h in [getattr(view, "hand", None)] + list(getattr(view, "others", None) or []):
            if HP.valid(h):
                J.skeleton(img, h, J.HAND_LINKS, J.MUTED)
        if self.screen == "play":
            now = self.blades[0].seen if self.blades[0].hist else 0
            for side, bl in enumerate(self.blades):
                pts = [(int(x), int(y)) for _, x, y in bl.hist]
                if len(pts) >= 2 and bl.hist[-1][0] >= max(self.blades[0].seen, self.blades[1].seen) - 0.2:
                    for k in range(1, len(pts)):
                        cv2.line(img, pts[k - 1], pts[k], HAND_COL[side], 1 + (3 * k) // len(pts), cv2.LINE_AA)
        else:
            tip = getattr(view, "fingertip", None)
            if tip is not None:
                J.cursor(img, tip, "TARGETED" if self.hover else ("TARGETABLE" if getattr(view, "pinching", False) else "TRACKING"), 0.0)


# ---------------- standalone ----------------

def demo(out_dir: Path) -> list[Path]:
    """No camera: the select screen, a moment of play with two hands slicing, the pause and results screens."""
    from agents.lens import LensResult
    out_dir.mkdir(parents=True, exist_ok=True)
    lib = Library(out_dir / "_lib")
    app = CantusApp(lib)
    app.player = _SilentPlayer()
    app.open(0.0)
    bg = np.full((config.FRAME_H, config.FRAME_W, 3), (60, 64, 68), np.uint8)
    paths = []
    p = out_dir / "01_select.png"
    cv2.imwrite(str(p), app.render(bg.copy(), None, 1.0))
    paths.append(p)
    app.play(1, 2.0)                                               # Für Elise, normal
    W, H = config.FRAME_W, config.FRAME_H
    g = stage(W, H)
    run = app.run
    # simulate perfect swings through the first blocks
    t_end = run.blocks[min(5, len(run.blocks) - 1)].b.t + app.start_at + app.offset
    now = 2.0
    while now < t_end:
        now += 1 / 30
        view = _sim_frame(app, g, now)
        last = app.render(bg.copy(), view, now)
    p = out_dir / "02_play.png"
    cv2.imwrite(str(p), last)
    paths.append(p)
    app.pause(True, now)
    p = out_dir / "03_paused.png"
    cv2.imwrite(str(p), app.render(bg.copy(), None, now))
    paths.append(p)
    app.pause(False, now)
    while app.screen == "play":                                    # the rest of the song, without drawing
        now += 1 / 30
        _sim_frame(app, g, now)
    p = out_dir / "04_results.png"
    cv2.imwrite(str(p), app.render(bg.copy(), None, now))
    paths.append(p)
    shutil.rmtree(out_dir / "_lib", ignore_errors=True)
    return paths


def _sim_frame(app, g, now):
    """One frame of a simulated player: a fingertip sweeping through each block the right way, on time."""
    from agents.lens import LensResult
    run = app.run
    t = app.song_t(now)
    hands = []
    for lv in run.blocks:
        if lv.state == "live" and abs(lv.b.t - t) < 0.12:
            x, y, half = block_rect(g, lv.b, lv.b.t - t, BM.DIFFICULTY[run.diff]["approach"])
            d = BM.DIRS[lv.b.dir] or (0.0, 1.0)
            k = (t - lv.b.t) / 0.12                                # sweep across the block over the window
            tip = (int(x + d[0] * half * 2.5 * k), int(y + d[1] * half * 2.5 * k))
            pts = HP.synth("point", (0, 0), 90)
            dx, dy = tip[0] - pts[8][0], tip[1] - pts[8][1]
            hands.append([(a + dx, b + dy) for a, b in pts])
    view = LensResult(hand=hands[0] if hands else None, fingertip=hands[0][8] if hands else None, others=hands[1:])
    app.update(view, now)
    return view


class _SilentPlayer(Player):
    def start(self, path, audio=None, sr=CS.SR):
        return "silent"


def run_camera():
    from agents.lens import Lens
    from app import mirror_result
    appx = CantusApp()
    appx.open()
    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_H)
    if not cap.isOpened():
        sys.exit("Camera not available. Allow camera access for Terminal in System Settings > Privacy & Security.")
    lens = Lens()
    t0 = time.monotonic()
    cv2.namedWindow("JAMES CANTUS", cv2.WINDOW_NORMAL)
    try:
        while appx.active:
            ok, frame = cap.read()
            if not ok:
                break
            r = lens.process(frame, int((time.monotonic() - t0) * 1000))
            if config.MIRROR:
                frame, r = cv2.flip(frame, 1), mirror_result(r, frame.shape[1])
            now = time.monotonic()
            appx.update(r, now)
            cv2.imshow("JAMES CANTUS", appx.render(frame, r, now))
            k = cv2.waitKey(1) & 0xFF
            if k == 255:
                continue
            c = chr(k).lower()
            if c == "q":
                break
            if c == "x" and getattr(lens.owner, "kind", "") == "key":
                off = lens.toggle_pause()
                appx.flash("Owner key off: any hand plays. X turns it back on." if off else
                           "Owner key on: only the hand wearing the key drives the menus.", now, 4)
                continue
            appx.key({27: "esc", 13: "enter", 10: "enter", 32: " "}.get(k, c), now)
    finally:
        appx.close()
        lens.close()
        cap.release()
        cv2.destroyAllWindows()


def main(argv=None):
    ap = argparse.ArgumentParser(description="CANTUS: slice to the beat with your hands")
    ap.add_argument("--demo", action="store_true", help="no camera: save a picture of every screen")
    ap.add_argument("--list", action="store_true", help="list the songs and their maps")
    ap.add_argument("--map", metavar="FILE", help="analyse one audio file now and print its map")
    a = ap.parse_args(argv)
    if a.map:
        ch = BM.chart_for_file(Path(a.map), data_dir() / "maps")
        print(f"{ch.title} ({ch.artist}): {ch.duration:.1f} s, {ch.bpm:.1f} BPM")
        for d in BM.DIFFICULTY:
            print(f"  {d:7s} {len(ch.blocks[d]):4d} blocks")
        return
    if a.list:
        lib = Library()
        lib.scan(background=False)
        for i, e in enumerate(lib.entries, 1):
            if e.chart:
                c = e.chart
                print(f"{i:2d}. {c.title:28s} {c.artist:26s} {c.duration:5.1f} s {c.bpm:6.1f} BPM  "
                      + "  ".join(f"{d} {len(c.blocks[d])}" for d in BM.DIFFICULTY))
            else:
                print(f"{i:2d}. {Path(e.path).name}: {e.error or e.status}")
        return
    if a.demo:
        for p in demo(config.DATA / "cantus_frames"):
            print(p.relative_to(config.ROOT))
        return
    run_camera()


if __name__ == "__main__":
    main()
