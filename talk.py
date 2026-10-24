"""TALK: JAMES speaks, with a butler's manners, for replies, for saying what it is doing, and for reading out
what is on the screen (the problems it found, the fix steps).

Two engines, both offline, both on this laptop:
  kokoro  A neural voice (Kokoro-82M, Apache-2.0, run with onnxruntime through kokoro-onnx). Natural, not robotic.
          Male voices only in JAMES's list; default British 'bm_fable'. Needs `pip install kokoro-onnx==0.6.1`
          and the two files in assets/voice/ (python -m tools.get_voice checks or fetches them).
  say     macOS `say`, the fallback: the best male English voice installed. The text goes in on stdin.
Pick a voice by ear: `python -m talk --audition`, then `python -m talk --use bm_george`. Saved in data/prefs.json
with the talk-back on/off switch. It is a stock synthetic voice with a butler's manners, not a copy of anyone.

JAMES stops talking the moment you start push-to-talk, so it never hears itself."""
from __future__ import annotations

import hashlib
import json
import queue
import random
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from commands import human_query, web_target

ROOT = Path(__file__).resolve().parent
VOICE_DIR = ROOT / "assets" / "voice"
MODEL_FILES = {"kokoro-v1.0.onnx": "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
               "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"}
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
# Male voices only. Grades are the model author's (VOICES.md); pitch is the median measured on JAMES's own lines.
NEURAL_VOICES = {
    "bm_fable": ("British, calm, grade C, ~119 Hz", "en-gb"),
    "bm_george": ("British, classic butler, grade C, ~143 Hz", "en-gb"),
    "bm_lewis": ("British, deep, grade D+, ~99 Hz", "en-gb"),
    "mix_fable_lewis": ("British, blend of fable and lewis (deeper)", "en-gb"),
    "am_michael": ("American, warm, grade C+, ~118 Hz", "en-us"),
    "am_onyx": ("American, very deep, grade D, ~89 Hz", "en-us"),
}
DEFAULT_NEURAL = "bm_fable"
NEURAL_SPEED = 0.95                      # a touch slower than default: calmer, less clipped
# macOS fallback, male voices first (the Premium/Enhanced ones are free downloads in System Settings >
# Accessibility > Spoken Content > System voice > Manage Voices).
PREFERRED = ("Daniel (Premium)", "Daniel (Enhanced)", "Oliver (Enhanced)", "Jamie (Premium)", "Jamie (Enhanced)",
             "Arthur (Enhanced)", "Evan (Enhanced)", "Nathan (Enhanced)", "Tom (Enhanced)", "Daniel", "Oliver",
             "Arthur", "Rishi")


def installed_voices() -> dict[str, str]:
    """{voice name: locale} from `say -v ?`."""
    if sys.platform != "darwin":
        return {}
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    voices = {}
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s{2,}([a-z]{2,3}[_-][A-Za-z]{2,4})\b", line)
        if m:
            voices[m.group(1).strip()] = m.group(2)
    return voices


def pick_voice(voices: dict[str, str], wanted: str = "") -> str:
    if wanted and wanted in voices:
        return wanted
    for v in PREFERRED:
        if v in voices:
            return v
    return next((v for v, loc in voices.items() if loc.replace("-", "_") == "en_GB"), "")


def clean(text: str, limit: int = 600) -> str:
    """What should be spoken: no links, no symbols read out letter by letter, not too long."""
    t = re.sub(r"https?://\S+", "the link", str(text or ""))
    t = re.sub(r"[*_#`>|\[\]{}]", " ", t)
    t = t.replace("·", ",").replace("&", " and ").replace("->", " to ")
    t = re.sub(r"\s+", " ", t).strip().lstrip("-").strip()
    return t[:limit].rsplit(" ", 1)[0] + "." if len(t) > limit else t


def sentences(text: str) -> list[str]:
    """Speak sentence by sentence: the first one starts while the next is being made."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text.strip())
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        out.append(p)
    if out and len(out[0]) > FIRST_MAX:                  # a short first piece = a quick first word
        cut = [m for m in re.finditer(r",\s+", out[0]) if 25 <= m.start() <= len(out[0]) - 20]
        if cut:                                          # live 04:21:14: a 104-character first sentence took 2.7 s
            out[0:1] = [out[0][:cut[0].start() + 1], out[0][cut[0].end():]]
    return out


FIRST_MAX = 90


# ---------------- preferences (the on/off switch and the chosen voice) ----------------
def prefs_path() -> Path:
    import config
    return Path(config.DATA) / "prefs.json"


def load_prefs() -> dict:
    try:
        d = json.loads(prefs_path().read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(**kw) -> dict:
    d = load_prefs()
    d.update({k: v for k, v in kw.items() if k in ("talkback", "voice", "speed")})
    try:
        prefs_path().parent.mkdir(parents=True, exist_ok=True)
        prefs_path().write_text(json.dumps(d, indent=1))
    except OSError:
        pass
    return d


# ---------------- the neural engine ----------------
def neural_files_ok(check_hash: bool = False) -> bool:
    for name, digest in MODEL_FILES.items():
        f = VOICE_DIR / name
        if not f.is_file():
            return False
        if check_hash:
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            if h.hexdigest() != digest:
                return False
    return True


def neural_available() -> tuple[bool, str]:
    if not neural_files_ok():
        return False, "voice files missing (python -m tools.get_voice)"
    import importlib.util
    for mod in ("kokoro_onnx", "sounddevice", "onnxruntime"):
        if importlib.util.find_spec(mod) is None:
            return False, f"{mod} not installed: pip install kokoro-onnx==0.6.1"
    return True, ""


def trim_silence(audio, sr: int, rel: float = 0.02, pad_s: float = 0.04):
    import numpy as np
    a = np.nan_to_num(np.asarray(audio, np.float32))
    peak = float(np.abs(a).max()) if a.size else 0.0
    if peak < 1e-4:
        raise ValueError("the voice produced silence")
    loud = np.flatnonzero(np.abs(a) > rel * peak)
    pad = int(pad_s * sr)
    return a[max(0, loud[0] - pad): min(len(a), loud[-1] + pad)]


class Neural:
    """Kokoro through onnxruntime. Loaded once, in the background; synth() returns float32 audio at 24 kHz."""

    def __init__(self, voice: str = DEFAULT_NEURAL, speed: float = NEURAL_SPEED):
        self.voice = voice if voice in NEURAL_VOICES else DEFAULT_NEURAL
        self.speed = float(speed)
        self.sr = 24000
        self._k = None
        self._style = None
        self.error = ""
        self.ready = threading.Event()

    def load(self):
        try:
            import onnxruntime as ort
            ort.set_default_logger_severity(3)          # keep onnxruntime's notes out of the terminal
            from kokoro_onnx import Kokoro
            self._k = Kokoro(str(VOICE_DIR / "kokoro-v1.0.onnx"), str(VOICE_DIR / "voices-v1.0.bin"))
            if self.voice.startswith("mix_"):
                import numpy as np
                a, b = self.voice[4:].split("_")
                pick = {"fable": "bm_fable", "lewis": "bm_lewis", "george": "bm_george"}
                self._style = np.add(self._k.get_voice_style(pick[a]), self._k.get_voice_style(pick[b])) / 2
            else:
                self._style = self.voice
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {str(e)[:80]}"
        self.ready.set()

    def synth(self, text: str):
        self.ready.wait(30)
        if self._k is None:
            raise RuntimeError(self.error or "neural voice not loaded")
        lang = NEURAL_VOICES[self.voice][1]
        # sentence_pause / clause_pause 0: kokoro-onnx 0.6.1 can crash inserting pauses into very short audio;
        # JAMES speaks sentence by sentence and adds its own short gap instead.
        # trim=False: in 1 of 20 runs the library's own trim returned empty audio (measured 2026-09-26); JAMES
        # trims the silence itself and refuses silent output, so a failure falls back instead of saying nothing.
        audio, sr = self._k.create(text, voice=self._style, speed=self.speed, lang=lang,
                                   sentence_pause=0.0, clause_pause=0.0, trim=False)
        self.sr = sr
        return trim_silence(audio, sr)


class Talker:
    """A queue of utterances spoken one after another on a background thread.
    engine: 'auto' (neural if installed, else macOS say), 'kokoro', 'say'."""

    GAP_S = 0.18                                        # pause between sentences

    def __init__(self, enabled: bool = True, voice: str = "", rate: int = 185, log=None, engine: str = "auto",
                 speed: float = NEURAL_SPEED):
        self.log = log or (lambda line: None)
        self.rate = int(rate)
        self.q: queue.Queue = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.last = ""
        self.muted = False
        self._lock = threading.Lock()
        self._gen = 0
        self._busy = False
        self.neural: Neural | None = None
        self.engine, self.why = "off", ""
        self.voices, self.voice = {}, ""
        if enabled:
            ok, why = neural_available() if engine in ("auto", "kokoro") else (False, "not asked for")
            if ok:
                self.engine = "kokoro"
                self.neural = Neural(voice if voice in NEURAL_VOICES else DEFAULT_NEURAL, speed)
                self.voice = self.neural.voice
                threading.Thread(target=self.neural.load, daemon=True).start()
            elif sys.platform == "darwin":
                self.engine, self.why = "say", why
                self.voices = installed_voices()
                self.voice = pick_voice(self.voices, voice if voice not in NEURAL_VOICES else "")
            else:
                self.why = why or "no speech engine on this computer"
        self.enabled = self.engine != "off"
        self._pool = ThreadPoolExecutor(1) if self.engine == "kokoro" else None
        import shutil
        self._afplay = shutil.which("afplay") if sys.platform == "darwin" else None
        if self.enabled:
            threading.Thread(target=self._run, daemon=True).start()

    @property
    def speaking(self) -> bool:
        return self._busy or bool(self.proc and self.proc.poll() is None) or not self.q.empty()

    def describe(self) -> str:
        if self.engine == "kokoro":
            return f"neural voice {self.voice} ({NEURAL_VOICES[self.voice][0]})"
        if self.engine == "say":
            return f"macOS voice {self.voice or 'default'}" + (f" (neural voice off: {self.why})" if self.why else "")
        return f"off ({self.why})" if self.why else "off"

    def say(self, text: str, interrupt: bool = False):
        t = clean(text)
        if not t:
            return
        self.last = t
        self.log(f"SAY {t!r}" + (" (muted)" if self.muted or not self.enabled else ""))
        if self.muted or not self.enabled:
            return
        if interrupt:
            self.stop()
        self.q.put((self._gen, t))

    def stop(self):
        """Stop talking now (push-to-talk started, the switch went off, or something more important came up)."""
        self._gen += 1
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        if self.engine == "kokoro" and not self._afplay:
            try:
                import sounddevice as sd
                sd.stop()                               # (non-Mac dev path only; the Mac plays through afplay)
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()

    def set_muted(self, muted: bool) -> bool:
        self.muted = bool(muted)
        if self.muted:
            self.stop()
        return self.muted

    def toggle_mute(self) -> bool:
        return self.set_muted(not self.muted)

    def _say_cmd(self, t: str):
        args = ["say", "-r", str(self.rate)] + (["-v", self.voice] if self.voice and self.engine == "say" else [])
        try:
            with self._lock:
                self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
            self.proc.communicate(t.encode("utf-8"), timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass

    def _play(self, gen: int, audio) -> None:
        """Play one sentence. On the Mac: a WAV file played by macOS `afplay` in its own process, stopped by
        terminating it. Playing in-process with sounddevice while the microphone opens crashed Python on the
        Mac (tools/mic_check --talk, 2026-09-26 04:14: PortAudio AUHAL err -50, then SIGTRAP): PortAudio output
        from the voice thread and mic input from the main thread must not share the process."""
        dur = len(audio) / self.neural.sr
        if self._afplay:
            import tempfile
            import wave
            import numpy as np
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
            with tempfile.NamedTemporaryFile(prefix="james-voice-", suffix=".wav", delete=False) as f:
                path = f.name
            with wave.open(path, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.neural.sr); w.writeframes(pcm)
            try:
                with self._lock:
                    if gen != self._gen:
                        return
                    self.proc = subprocess.Popen([self._afplay, path], stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL)
                end = time.monotonic() + dur + 5
                while self.proc.poll() is None and time.monotonic() < end:
                    if gen != self._gen:
                        with self._lock:
                            if self.proc.poll() is None:
                                self.proc.terminate()
                        break
                    time.sleep(0.02)
                if gen == self._gen:
                    time.sleep(self.GAP_S)
            finally:
                try:
                    Path(path).unlink()
                except OSError:
                    pass
            return
        import sounddevice as sd                        # other platforms (development only)
        sd.play(audio, self.neural.sr)
        end = time.monotonic() + dur + self.GAP_S
        while time.monotonic() < end:
            if gen != self._gen:
                sd.stop()
                break
            time.sleep(0.02)

    def _speak_neural(self, gen: int, t: str):
        t0 = time.monotonic()
        futures = [self._pool.submit(self.neural.synth, s) for s in sentences(t)]
        for k, f in enumerate(futures):
            if gen != self._gen:
                break
            try:
                audio = f.result(timeout=30)
            except Exception as e:  # noqa: BLE001  one bad sentence: say it with the Mac voice instead
                self.log(f"TALK neural voice failed ({type(e).__name__}: {str(e)[:60]}); macOS say used")
                if sys.platform == "darwin":
                    self._say_cmd(sentences(t)[k])
                continue
            if k == 0 and time.monotonic() - t0 > 1.5:
                self.log(f"TALK slow start: {time.monotonic() - t0:.1f} s to the first word")
            if gen != self._gen:
                break
            self._play(gen, audio)
        for f in futures:
            f.cancel()

    def _run(self):
        while True:
            gen, t = self.q.get()
            if gen != self._gen:
                continue
            self._busy = True
            try:
                if self.engine == "kokoro" and self.neural is not None:
                    if self.neural.ready.wait(30) and self.neural._k is not None:
                        self._speak_neural(gen, t)
                    else:
                        self.log(f"TALK neural voice unavailable ({self.neural.error}); macOS say used")
                        self._say_cmd(t) if sys.platform == "darwin" else None
                else:
                    self._say_cmd(t)
            except Exception as e:  # noqa: BLE001  speech must never take JAMES down
                self.log(f"TALK error {type(e).__name__}: {str(e)[:80]}")
            finally:
                self._busy = False


# ---------------- what JAMES says ----------------
NAMES = r"(?:james|jarvis)"                     # what people call the assistant out loud (Whisper often hears either)


def drop_name(t: str) -> str:
    """'james, fix problem two' / 'problem two james' -> 'fix problem two' / 'problem two'."""
    t = re.sub(rf"^\s*(?:(?:hey|ok|okay)\s+)?{NAMES}\b[\s,]*", "", t, flags=re.I)
    return re.sub(rf"[\s,]+{NAMES}[\s.!?]*$", "", t, flags=re.I)
SIR = "sir"
ACK = ("Right away", "Certainly", "Very good", "Of course")
NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def ack(sir: str = SIR) -> str:
    return f"{random.choice(ACK)}, {sir}." if sir else f"{random.choice(ACK)}."


def part_of_day(hour: int | None = None) -> str:
    h = time.localtime().tm_hour if hour is None else hour
    return "morning" if 5 <= h < 12 else ("afternoon" if 12 <= h < 17 else "evening")


def salute(sir: str = SIR, hour: int | None = None) -> str:
    """'Good morning, sir'; between midnight and 5 am (log 02:19:58 said 'Good evening') 'Working late, sir?'"""
    h = time.localtime().tm_hour if hour is None else hour
    if h < 5:
        return f"Working late{', ' + sir if sir else ''}?"
    return f"Good {part_of_day(h)}{', ' + sir if sir else ''}."


def greeting(sir: str = SIR, hour: int | None = None) -> str:
    return f"{salute(sir, hour)} All systems are online."


SMALLTALK = [
    (r"^(?:hi|hello|hey|hiya|yo|hey there|hello there)(?:\s+(?:james|jarvis|buddy|mate|there))?$",
     lambda s, st: f"Hello{', ' + s if s else ''}. How may I help?"),
    (r"^(?:(?:hi|hello|hey)\s+)?(?:james\s+)?how\s+(?:are|r)\s+(?:you|u)(?:\s+doing)?(?:\s+today)?$|^how(?:'s| is) it going$",
     lambda s, st: "Running smoothly, thank you for asking. And yourself?"),
    (r"^(?:i'?m|i am)\s+(?:good|fine|great|okay|ok|doing (?:good|well|great))(?:.*)$",
     lambda s, st: "Splendid. What shall we do next?"),
    (r"^(?:thanks|thank you|thank you so much|cheers|nice|great job|well done|good job)(?:\s+(?:james|jarvis|buddy|mate))?$",
     lambda s, st: f"My pleasure{', ' + s if s else ''}."),
    (r"^(?:what(?:'s| is) (?:up|happening|going on)|what are you doing|status|status report|what(?:'s| is) on (?:the )?screen|"
     r"read (?:the|my) screen|what do you see)$", lambda s, st: st),
    (r"^(?:who are you|what(?:'s| is) your name|what can you do|help|what do you do)$",
     lambda s, st: ("I'm JAMES. I can open and close apps, play music, search the web and GitHub, save links to "
                    "your Notes, prepare messages, and identify what you hold up and how to fix it.")),
    (r"^(?:what(?:'s| is) the time|what time is it|time)$",
     lambda s, st: time.strftime("It's %-I:%M %p.").replace("AM", "in the morning").replace("PM", "in the evening")
     if sys.platform != "win32" else time.strftime("It's %I:%M.")),
    (r"^(?:good (?:morning|afternoon|evening))(?:\s+(?:james|jarvis))?$",
     lambda s, st: salute(s)),
    (r"^(?:good ?night|bye|goodbye|see you|see ya)(?:\s+(?:james|jarvis))?$",
     lambda s, st: f"Goodbye{', ' + s if s else ''}. I'll be here."),
]


def smalltalk(text: str, status: str, sir: str = SIR) -> str | None:
    """A reply for chit-chat (hi, how are you, what's happening), or None if it is not chit-chat."""
    t = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower())
    t = re.sub(r"^(?:hey|ok|okay)\s+(?:james|jarvis)\s+", "", re.sub(r"\s+", " ", t).strip())
    t = re.sub(r"\s+(?:james|jarvis)$", "", t) if not re.match(r"^(?:hi|hello|hey|thanks|thank you)", t) else t
    for rx, reply in SMALLTALK:
        if re.match(rx, t):
            return reply(sir, status)
    return None


NICE = {"chatgpt": "ChatGPT", "imdb": "IMDb", "youtube": "YouTube", "github": "GitHub", "linkedin": "LinkedIn",
        "whatsapp": "WhatsApp", "netflix": "Netflix", "instagram": "Instagram", "gmail": "Gmail", "spotify": "Spotify",
        "amazon": "Amazon", "wikipedia": "Wikipedia", "reddit": "Reddit", "notion": "Notion", "claude": "Claude",
        "perplexity": "Perplexity", "gemini": "Gemini", "chrome": "Chrome", "safari": "Safari"}


def _name(st) -> str:
    n = re.sub(r"\s+(?:on|in)\s+(?:my|the|this)\s+(?:laptop|mac|macbook|computer)$", "", st.arg).strip()
    n = NICE.get(n.lower(), n)
    if st.kind == "open" and getattr(st, "browser", ""):
        n += " in " + st.browser.replace("Google ", "").replace(" Browser", "")
    return n


def narrate(steps, sir: str = SIR) -> str:
    """'Right away, sir. Opening Chrome, then searching YouTube for Arduino.' Simple words, what is about to happen."""
    s = plan_words(steps)
    return f"{ack(sir)} {s[0].upper() + s[1:]}." if s else ""


def plan_words(steps) -> str:
    """'opening Chrome, then searching YouTube for Arduino' (no greeting): what the steps will do."""
    parts = []
    for st in steps:
        k = st.kind
        where = {"github": "GitHub", "youtube": "YouTube", "amazon": "Amazon", "spotify": "Spotify"}.get(getattr(st, "site", ""), "the web")
        if k == "open":
            if getattr(st, "browser", "") and web_target(st.arg)[1] == "search":
                parts.append(f"searching the web for {st.arg} in {st.browser.replace('Google ', '').replace(' Browser', '')}")
            else:
                parts.append(f"opening {_name(st)}")
        elif k == "close":
            parts.append(f"closing {_name(st) or 'the app'}")
        elif k == "play":
            parts.append(f"playing {st.arg} on {'Spotify' if st.service == 'spotify' else 'YouTube'}")
        elif k == "search":
            parts.append(f"searching {where} for {human_query(st.arg)}")
        elif k == "collect":
            dest = "your Notes" if st.to == "notes" else "the clipboard"
            parts.append(f"collecting the top {where if where != 'the web' else 'web'} results for {human_query(st.arg)} into {dest}"
                         if st.arg else f"saving those results to {dest}")
        elif k == "message":
            app = "Instagram" if st.service == "instagram" else "WhatsApp"
            parts.append(f"opening your {app} chat with {st.to.title()} and copying the message. You press send")
        elif k == "ask":
            who = {"chatgpt": "ChatGPT", "claude": "Claude", "perplexity": "Perplexity", "gemini": "Gemini"}.get(st.service, st.service)
            words = " ".join(st.arg.split()[:10])
            parts.append(f"asking {who}: {words}")
        elif k == "send":
            parts.append("pressing send")
        elif k == "media":
            parts.append({"pause": "pausing", "play": "resuming", "next": "skipping to the next one",
                          "previous": "going back one"}.get(st.arg, st.arg))
        elif k == "identify":
            parts.append("hold it up in the square, please")
        elif k in ("display", "glass"):
            parts.append(f"opening Universal Display for {st.arg or 'your phone'}")
    return ", then ".join(parts)


def fail(detail: str) -> str:
    d = str(detail or "that did not work").rstrip(".")
    return f"I'm afraid {d[0].lower() + d[1:] if d else d}."


PAIRS = {"glasses", "sunglasses", "spectacles", "headphones", "earphones", "earbuds", "scissors", "pliers", "shoes",
         "sneakers", "jeans", "trousers", "pants", "shorts", "gloves", "socks", "binoculars", "tongs", "tweezers"}


def article(obj: str) -> str:
    """'a phone', 'an apple', 'a pair of sunglasses' (log 03:26:20 said 'a sunglasses'), 'some cables'."""
    o = str(obj or "object").strip()
    last = o.lower().split()[-1] if o.split() else ""
    if last in PAIRS:
        return f"a pair of {o}"
    if last.endswith("s") and not last.endswith(("ss", "us", "is")) and len(last) > 3:
        return f"some {o}"
    return f"an {o}" if o[:1].lower() in "aeiou" else f"a {o}"


def identified(obj: str, problem: str, problems: list[dict], model: str = "") -> str:
    """The screen after IDENTIFY, read out: what it is, any damage, the four problems, and the question."""
    what = f"a {model}" if model else article(obj)
    dmg = ("I can't see any damage." if not problem or "no visible" in problem.lower()
           else f"I can see {problem.lower().rstrip('.')}.")
    items = "; ".join(f"{NUM.get(i, i)}, {p['title']}" + (", which I can see" if p.get("seen") else "")
                      for i, p in enumerate(problems[:4], 1))
    kind = "The usual problems with these, none of which I can see, are" if not any(p.get("seen") for p in problems) \
        else "The problems to check are"
    return (f"That appears to be {what}. {dmg} {kind}: {items}. "
            "Which one shall we look at? Say, fix problem one, or point and pinch.")


_SIGN_LEAD = re.compile(r"^(?:the\s+)?(?:users?|people|owners?|you|customers?)\s+(?:may\s+|might\s+|will\s+|often\s+|"
                        r"usually\s+|typically\s+)?(?:notice|see|find|experience|report|hear|feel)s?\s+(?:that\s+)?", re.I)


def chosen(p: dict) -> str:
    """Log 02:17:13: sign 'Users notice cracks...' was read as 'You would usually notice users notice cracks'."""
    sign = _SIGN_LEAD.sub("", str(p.get("sign") or "").strip()).rstrip(". ")
    if len(sign) > 1 and sign[1].islower():
        sign = sign[0].lower() + sign[1:]
    return f"{p['title']}. " + (f"The usual sign: {sign}. " if sign else "") + "Finding the usual fixes."


def fix_intro(p: dict, fix: dict) -> str:
    steps = fix.get("steps") or []
    if not steps:
        return f"I'm afraid I have no steps for {p['title']}. I can search the web for it."
    out = []
    if fix.get("cause"):
        out.append(str(fix["cause"]).rstrip(".") + ".")
    safety = str(fix.get("safety") or "").strip().rstrip(".")
    if safety and not re.fullmatch(r"(?i)(none|n/?a|null|nil|not applicable|no (specific )?(safety )?(concerns?|precautions?|risks?|hazards?))", safety):
        out.append("A word of caution: " + safety + ".")               # log 03:26:47 said "caution: None."
    out.append("There is one step." if len(steps) == 1 else f"There are {NUM.get(len(steps), len(steps))} steps.")
    out.append(step(1, steps[0]))
    if len(steps) > 1:
        out.append("Say next step when you're ready.")
    return " ".join(out)


def step(n: int, st: dict) -> str:
    return f"Step {NUM.get(n, n)}: {str(st.get('title', '')).rstrip('.')}. {st.get('detail', '')}".strip()


def pro(fix: dict) -> str:
    w = str(fix.get("pro_when") or "").rstrip(".")
    return f"Best left to a professional if: {w[0].lower() + w[1:]}." if w else ""


NAV = [
    (r"^(?:next|next step|continue|go on|carry on|and then|what(?:'s| is) next)$", ("next",)),
    (r"^(?:previous|previous step|back a step|go back a step|last step|step back)$", ("prev",)),
    (r"^(?:repeat|repeat that|say that again|again|pardon|come again|what)$", ("repeat",)),
    (r"^(?:read (?:all|all the|every|the) steps|read them all|all steps)$", ("all",)),
    (r"^(?:(?:what(?:'s| is) )?the nature of (?:the|this|that) problem|what(?:'s| is) (?:the|this) problem|"
     r"what(?:'s| is) wrong|why does (?:this|it) happen|explain (?:the|this) problem)$", ("nature",)),
    (r"^(?:read (?:the )?screen|what(?:'s| is) on (?:the )?screen|read (?:the )?problems|what are the problems|"
     r"list (?:the )?problems)$", ("screen",)),
]
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10, "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
           "eighth": 8, "ninth": 9, "tenth": 10}


def nav(text: str):
    """Talking through the fix: next, previous, repeat, read step three, read all, the nature of the problem."""
    t = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower())
    t = re.sub(r"^(?:(?:ok|okay|james|jarvis|please|now)\s+)+|\s+please$", "", re.sub(r"\s+", " ", t).strip())
    m = re.fullmatch(r"(?:read|go to|show|tell me|what(?:'s| is))?\s*(?:the\s+)?(?:step\s+(?:number\s+)?"
                     r"(one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})|"
                     r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s+step)", t)
    if m:
        w = m.group(1) or m.group(2)
        return ("step", WORDNUM.get(w) or int(w))
    for rx, out in NAV:
        if re.match(rx, t):
            return out
    return None


AUDITION_LINE = "Good evening, sir. Opening Chrome, then searching YouTube for Arduino. Shall I read the next step?"


def _cli(argv: list[str]) -> None:
    import config
    if "--use" in argv:
        v = argv[argv.index("--use") + 1] if argv.index("--use") + 1 < len(argv) else ""
        if v not in NEURAL_VOICES and v not in installed_voices():
            print("Unknown voice. Neural voices:", ", ".join(NEURAL_VOICES))
            return
        print("Saved. JAMES will use", v, "->", save_prefs(voice=v))
        return
    ok, why = neural_available()
    print("Neural voice:", "ready" if ok else f"not available ({why})")
    if "--audition" in argv:
        if not ok:
            print("Install it first: pip install kokoro-onnx==0.6.1 ; python -m tools.get_voice")
            return
        for v, (label, _) in NEURAL_VOICES.items():
            print(f"  {v:16s} {label}")
            t = Talker(True, v, engine="kokoro")
            t.say(AUDITION_LINE)
            time.sleep(0.5)
            while t.speaking:
                time.sleep(0.1)
            time.sleep(0.6)
        print("Pick one:  python -m talk --use <name>")
        return
    p = load_prefs()
    t = Talker(True, p.get("voice", getattr(config, "TALK_VOICE", "")), getattr(config, "TALK_RATE", 185),
               engine=getattr(config, "TALK_ENGINE", "auto"), speed=p.get("speed", NEURAL_SPEED))
    print("JAMES uses:", t.describe())
    t.say(greeting(getattr(config, "TALK_SIR", "sir")) + " Shall I open the launcher?")
    time.sleep(0.5)
    while t.speaking:
        time.sleep(0.2)


if __name__ == "__main__":                        # python -m talk [--audition | --use NAME]
    _cli(sys.argv[1:])
