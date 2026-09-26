"""JAMES, simple demo: point and pinch to open apps, and IDENTIFY what you hold up.

    python james.py

Open palm (when no task is running) opens the launcher. Point at a tile and pinch to choose.
Choose Identify (or press I) and hold an object in the square: JAMES names it, spots damage,
says how to fix it and searches for the right part. If it can't tell the model, it asks you.

Gesture ownership: while a task is running (framing, identifying, asking for the model,
listening, searching, or showing a result), an open palm does NOT open the launcher. The task
shows its own pinchable actions on the right (Talk, Cancel, Open result, Done) instead.

Drawn in the JAMES Spatial Interface Language (jsil.py). Every value shown is measured or returned.

Voice (push-to-talk): show the V sign to start listening, keep talking while you hold it, then close
your hand into a fist to stop and run the command. Open palm while listening cancels. The voice window
shows what was heard and each step it ran. Commands open apps and web pages, plus fixed media keys
(pause / resume / next / previous, media.py); nothing spoken is ever run as a command:
    "open asphalt", "open Chrome and play Despacito", "play believer on Spotify",
    "search for pump seal kits", "universal display for Redmi Note 10", "tempered glass for iPhone 13",
    "open GitHub and find top repos for IoT", "pause the video", "next song"

Owner hand (config.OWNER_MODE): "key" (default) = wear the printed owner key (ArUco id 3, the owner's printed card;
reprint with tools/make_owner_key.py) on the watch strap or the back of the hand and show it once; that hand drives
JAMES and is followed even when the key turns away. X switches the key requirement off/on.
"ring" = press K and hold up the ring hand for 3 s; X forgets the ring. Other hands are shown faded and
ignored. (A visual filter, not security: a copy of the key or a similar ring passes.)

Keys: I identify | V talk | / type a command (no mic) | O open the top result | L launcher | Esc close launcher or cancel task
      K enrol ring (ring mode) | X key off/on, or forget ring | U lock | G eyes | T telemetry | H hints | Q quit
"""
from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import urllib.parse

import cv2
import numpy as np

import commands as C
import config
from james_core import egress
import jsil as J
import launcher_apps as LA
import collect
import media
import talk as T
from agents import llm, scout, vision
from agents import problems as PR
from agents import planner as PL
from agents.echo import Recorder, Transcriber

PINCH_MS = 600
COOLDOWN_S = 1.2
FRAMING_S = 5            # seconds to place the object inside the square before the snapshot
PALM_HOLD_S = 0.8        # open palm held this long opens the launcher (only when no task is running)
PALM_BLOCK_S = 1.5       # after a choice or a closed task, ignore open palms this long
LAUNCHER_IDLE_S = 7      # launcher closes after this long without a target
TASK_MODES = ("FRAMING", "IDENTIFYING", "ASK_MODEL", "PROBLEMS", "FIXING", "FIXES", "LISTENING", "THINKING",
              "SEARCHING", "DONE")
CHOICE_W = 560            # the problems / fix steps panel (pinchable rows)
APP_W, APP_H = 92, 86    # app hit area: floating logo + name
ACT_W, ACT_H = 104, 44   # action tile in the launcher centre
DOCK_W, DOCK_H = 168, 50 # task action tile on the right
HAND_LINKS = J.HAND_LINKS
PEACE_HOLD_S = 0.35      # V sign held this long starts listening
FIST_HOLD_S = 0.25       # fist held this long stops listening and runs the command
CANCEL_HOLD_S = 0.6      # open palm held this long while listening cancels
VOICE_MAX_S = 25         # listening stops by itself after this long (ChatGPT prompts ran 12 to 13 s)
VOICE_LOST_S = 2.0       # hand gone this long while listening: stop and run what was said
VOICE_SHOW_S = 9         # the voice window stays this long after the last step
VOICE_W = 560
UD_NOTE = "third-party site"
PHONE_WORDS = re.compile(r"\bphone|mobile|smartphone|iphone|galaxy|redmi|pixel|oneplus|vivo|oppo|realme|poco|nokia|"
                         r"motorola|moto |xiaomi|samsung|tecno|infinix|iqoo|nothing phone", re.I)

AMBER, RED, GREEN = J.CONFIRM, J.ERROR, J.SUCCESS


@dataclass
class Button:
    key: str
    label: str
    kind: str = "action"            # action | app | problem | step
    icon: np.ndarray | None = None
    rect: tuple = (0, 0, 0, 0)
    sub: str = ""                   # problem: what the owner notices; step: the detail text
    tag: str = ""                   # problem: "SEEN" when it is visible in the snapshot
    open: bool = False              # step: expanded

    def hit(self, p) -> bool:
        x, y, w, h = self.rect
        return p is not None and x - 6 <= p[0] <= x + w + 6 and y - 6 <= p[1] <= y + h + 6


@dataclass
class State:
    mode: str = "READY"             # READY or one of TASK_MODES
    result: dict | None = None
    thumb: np.ndarray | None = None
    model_text: str = ""
    query: str = ""
    results: list = field(default_factory=list)
    recommendation: str = ""
    heard: str = ""
    msg: tuple | None = None        # (text, color, until)
    locked: bool = False
    t_identify: float = 0.0
    took_s: float | None = None
    identified_at: str = ""
    framing_until: float = 0.0
    problems: list = field(default_factory=list)   # [{title, sign, seen, source}] after IDENTIFY
    choice: int | None = None       # the problem the user pinched
    fix: dict | None = None         # {steps: [{title, detail}], safety, pro_when, part_needed, source}
    expanded: int | None = None     # the open fix step
    t_fix: float = 0.0
    fix_took_s: float | None = None


MODE_LABEL = {"READY": "Ready", "FRAMING": "Framing", "IDENTIFYING": "Identifying", "ASK_MODEL": "Need model",
              "PROBLEMS": "Pick problem", "FIXING": "Finding fixes", "FIXES": "Fix steps",
              "LISTENING": "Listening", "THINKING": "Thinking", "SEARCHING": "Searching", "DONE": "Result ready"}
MODE_COLOR = {"FRAMING": J.CYAN, "IDENTIFYING": J.CYAN, "ASK_MODEL": J.CONFIRM, "LISTENING": J.CYAN,
              "PROBLEMS": J.CONFIRM, "FIXING": J.CYAN, "FIXES": J.SUCCESS,
              "THINKING": J.CYAN, "SEARCHING": J.CYAN, "DONE": J.SUCCESS}


class James:
    def __init__(self):
        self.s = State()
        self.q: queue.Queue = queue.Queue()
        self.rec, self.asr = Recorder(), Transcriber()
        self.apps = LA.resolve(getattr(config, "GOOGLE_ACCOUNT", None))      # allowlist, resolved for this OS
        self.app_by_id = {a.id: a for a in self.apps}
        self.installed = LA.installed_apps()                   # any installed app can be opened by voice
        self.model_ok = llm.available(config.CHAT_MODEL)
        self.launcher = None          # {"c": (x, y), "t": opened_at, "last_hover": t}
        self.palm_block_until = 0.0
        self.eyes_on = False
        self.show_hints = True
        self.show_telemetry = False
        self.timer = J.FrameTimer()
        self.events: deque = deque(maxlen=4)       # (clock, text, monotonic)
        self.selected: tuple | None = None         # (tip, until) brief success feedback at the cursor
        self.header = J.Header()
        self.card_cache = J.LayerCache()
        self._dock: list[Button] = []
        self.voice: dict | None = None             # the voice context window: listening, heard, plan steps
        self.voice_block_until = 0.0
        self.pending: dict | None = None            # a low-confidence plan waiting for 'yes' (iteration 2, P0-A)
        self.typing: str | None = None              # typed command in progress ('/' key), None when not typing
        self.owner_epoch = None                     # which hand-takeover is in control (owner.KeyFilter.epoch)
        self.owner_status = ""
        self._listen_epoch = None                   # the owner who gave the command now being processed
        self.enrolling = False
        self.whisper_prompt = C.whisper_prompt(self.installed.keys())
        self.log = open(config.DATA / "james_demo_log.txt", "a")
        self._voice_top = 0                         # the choices panel pushes the voice window below it
        self.last_media = ""                        # spotify | youtube: what JAMES last started (for pause / next)
        self.last_chat = ""                         # chatgpt | claude ...: the AI chat JAMES last opened (for "send it")
        self.sir = getattr(config, "TALK_SIR", "sir")
        prefs = T.load_prefs() if getattr(config, "TALKBACK", True) else {}
        self.talker = T.Talker(getattr(config, "TALKBACK", True),
                               prefs.get("voice") or getattr(config, "TALK_VOICE", ""),
                               getattr(config, "TALK_RATE", 185), log=self.write,
                               engine=getattr(config, "TALK_ENGINE", "auto"), speed=prefs.get("speed", T.NEURAL_SPEED))
        if prefs.get("talkback") is False:
            self.talker.set_muted(True)                 # the switch was off last time
        self.write(f"TALK engine: {self.talker.describe()}; talk-back {'off' if self.talker.muted else 'on'}")
        self.fix_pos = 0                            # the fix step JAMES read out last (for "next step")
        self.ctx: dict = {}                         # what the last commands did, so the next one can refer to it:
                                                    # app (name), search (site, query, top), chat, contact

    # ---------- helpers ----------
    @property
    def busy(self) -> bool:
        """A task owns the screen: the launcher gesture is off until it ends."""
        return self.s.mode in TASK_MODES

    def palm_allowed(self, now: float) -> bool:
        """The open-palm launcher gesture is live only when JAMES is idle."""
        return not self.launcher and not self.busy and not self.s.locked and now >= self.palm_block_until

    def flash(self, m, color=AMBER, secs=3.0):
        self.s.msg = (m, color, time.time() + secs)
        self.write(f"MSG {m}")

    def write(self, line):
        self.log.write(time.strftime("%H:%M:%S ") + line + "\n"); self.log.flush()

    def event(self, text):
        """A user-facing event for the compact event stream (real actions only)."""
        self.events.appendleft((time.strftime("%H:%M"), text, time.monotonic()))

    def end_task(self, why="Task closed"):
        if self.rec.active:
            self.rec.stop()
            if self.voice:
                self.voice.update(state="cancelled", until=time.time() + 2.5)
        self.s = State(locked=self.s.locked)
        self.palm_block_until = time.monotonic() + PALM_BLOCK_S
        self.event(why)
        self.write(f"TASK_END {why}")

    # ---------- actions ----------
    def press(self, key: str, frame=None):
        if key != "voice_toggle" and self.pending:                # a new task voids a waiting 'yes'
            self.pending = None
            self.write("LOWCONF discarded: another action started")
        if key == "voice_toggle":                      # the switch works even when locked: it only affects speech
            on = self.talker.muted
            self.set_talkback(on, "switch")
            self.flash("Voice on." if on else "Voice off. The switch (or M) turns it back on.", GREEN if on else AMBER)
            if on:
                self.speak("Voice on.")
            return
        if self.s.locked and key != "unlock":
            self.flash("Locked. Press U to unlock.", RED); return
        if key == "identify":
            self.identify(frame)
        elif key == "talk":
            self.talk()
        elif key == "open":
            self.open_top()
        elif key in ("cancel", "done"):
            if key == "cancel" and self.rec.active:
                self.stop_listen(run=False)
                return
            self.end_task("Task cancelled" if key == "cancel" else "Task done")
        elif key.startswith("prob:"):
            self.choose(int(key[5:]))
        elif key.startswith("step:"):
            i = int(key[5:])
            if self.s.mode == "FIXES":
                self.s.expanded = None if self.s.expanded == i else i
                self.write(f"STEP {'open' if self.s.expanded == i else 'close'} {i + 1}")
                steps = (self.s.fix or {}).get("steps") or []
                if self.s.expanded == i and i < len(steps):
                    self.fix_pos = i
                    self.speak(T.step(i + 1, steps[i]), interrupt=True)
        elif key == "back":
            if self.s.problems:
                self.s.mode, self.s.fix, self.s.choice, self.s.expanded = "PROBLEMS", None, None, None
        elif key == "fixes":
            if self.s.fix:
                self.s.mode = "FIXES"
        elif key == "parts":
            self.search_parts()
        elif key in ("ud_display", "ud_glass"):
            model = self.phone_model()
            url = C.ud_url(model, "glass" if key == "ud_glass" else "display")
            LA.open_url(url)
            what = "Tempered glass" if key == "ud_glass" else "Compatible displays"
            self.flash(f"{what} for {model or 'this phone'} on Universal Display ({UD_NOTE})", GREEN, 4)
            self.event(f"Universal Display: {what.lower()}")
            self.write(f"OPEN_URL {url} (universaldisplay.in, public page)")
        elif key.startswith("app:"):
            app = self.app_by_id.get(key[4:])
            if app is None:                                  # allowlist: only resolved targets can open
                self.flash("That is not on the allowed list", RED); return
            LA.open_target(app)
            if app.how == "app":
                self.ctx["app"] = app.label
            self.flash(f"Opening {app.label}", GREEN)
            self.event(f"{app.label} opened")
            self.write(f"OPEN {app.id} {app.how} {app.where}")

    def identify(self, frame=None):
        """Start a 5-second countdown with a square on screen. Pressing again snaps immediately."""
        if self.s.mode in ("IDENTIFYING", "SEARCHING"):
            return
        if not llm.available(config.CHAT_MODEL):
            self.flash("Vision model not running. Start: ollama serve", RED, 5); return
        if self.s.mode == "FRAMING":
            self.s.framing_until = time.time()          # second press: capture now
            return
        self.s = State(mode="FRAMING", locked=self.s.locked, framing_until=time.time() + FRAMING_S)
        self.event("Identify requested")
        self.write("IDENTIFY countdown")

    @staticmethod
    def square(W, H):
        """The capture square in DISPLAY coordinates: between the context card and the task actions."""
        left, right = int(W * 0.40), W - DOCK_W - 40
        side = int(min(right - left - 20, H - 200))
        cx, cy = (left + right) // 2, H // 2 + 10
        return cx - side // 2, cy - side // 2, side

    def capture(self, raw):
        """Crop exactly what is inside the square (mapping back from the mirrored view) and identify it."""
        H, W = raw.shape[:2]
        x, y, side = self.square(W, H)
        if config.MIRROR:
            x = W - x - side
        crop = raw[max(0, y):y + side, max(0, x):x + side].copy()
        self.s.mode, self.s.thumb, self.s.t_identify = "IDENTIFYING", crop, time.time()
        self.write(f"IDENTIFY snapshot {crop.shape[1]}x{crop.shape[0]} (only the square is sent to the local model)")
        threading.Thread(target=self._identify, args=(crop,), daemon=True).start()

    def _identify(self, frame):
        try:
            self.q.put(("identified", vision.identify(frame)))
        except llm.LLMUnavailable as e:
            self.q.put(("error", f"Couldn't identify: {e}"))

    def talk(self):
        """V key or the Talk tile: toggle listening."""
        if self.rec.active:
            self.stop_listen(run=True)
        else:
            self.start_listen("key")

    def _mic_dev(self) -> str:
        f = getattr(self.rec, "device_name", None)
        return f() if callable(f) else "?"

    def set_owner(self, status: str, epoch):
        """Called every frame with the owner filter's state. If ANOTHER hand takes control while a command is
        being recorded, the recording is dropped (iteration 2, P1-B / ST-14). The owner stepping out of view is
        not a change: a command they finished still runs (hand gone 2 s stops and runs it; control is held 4 s)."""
        prev, self.owner_epoch, self.owner_status = self.owner_epoch, epoch, status
        if prev is None or epoch == prev or status != "owner":
            return
        if self.rec.active:
            self.stop_listen(run=False)
            self.write("LISTEN discarded: another hand took control while recording")
            self.voice_step("Discarded: control passed to another hand while you were speaking.", "fail")
        if self.pending:
            self.pending = None
            self.write("LOWCONF discarded: control passed to another hand")

    def stale_owner(self) -> bool:
        """A result (transcript, model plan) belongs to a command from a hand that no longer has control."""
        return (self._listen_epoch is not None and self.owner_status == "owner"
                and self.owner_epoch is not None and self.owner_epoch != self._listen_epoch)

    def set_talkback(self, on: bool, via: str = "switch"):
        """One place for every way of turning JAMES's voice on or off (switch, M key, 'be quiet'). Remembered."""
        was_on = not self.talker.muted
        self.talker.set_muted(not on)
        if getattr(config, "TALKBACK", True):
            T.save_prefs(talkback=bool(on))
        if was_on != bool(on):
            self.write(f"TALK {'unmuted' if on else 'muted'} ({via})")
            self.event("Voice on" if on else "Voice off")

    def talker_on(self) -> bool:
        return bool(getattr(self.talker, "enabled", False)) and not getattr(self.talker, "muted", False)

    # ---------- typed commands (iteration 2, P0-F: a disclosed fallback when the mic fails) ----------
    def start_typing(self):
        if self.s.locked:
            self.flash("Unlock first (U).", AMBER)
            return
        if self.rec.active:
            self.stop_listen(run=False)
        self.typing = ""
        self.voice = {"state": "typing", "t0": time.monotonic(), "heard": "", "conf": None, "steps": [],
                      "via": "typed", "until": None, "level": 0.0}
        self.write("TYPE start")

    def type_key(self, k: int):
        """One key while typing: Enter runs it, Esc cancels, Backspace deletes."""
        if self.typing is None:
            return
        if k in (13, 10):
            txt, self.typing = self.typing.strip(), None
            if not txt:
                self.voice = None
                return
            before = self.s.mode if self.s.mode in ("READY", "ASK_MODEL", "PROBLEMS", "FIXES", "DONE") else "READY"
            self._before_listen = before
            self.write(f"TYPED {txt!r} (typed input, not the microphone)")
            self.voice = {"state": "done", "t0": time.monotonic(), "heard": "", "steps": [], "via": "typed", "level": 0.0}
            self.on_heard(txt, 1.0)
        elif k == 27:
            self.typing = None
            self.voice = None
            self.write("TYPE cancelled")
        elif k in (8, 127):
            self.typing = self.typing[:-1]
        elif 32 <= k <= 126 and len(self.typing) < 200:
            self.typing += chr(k)
        if self.typing is not None and self.voice is not None:
            self.voice["heard"] = self.typing + "_"

    def voice_allowed(self, now: float | None = None) -> bool:
        """Push-to-talk may start when idle, when JAMES is asking for the model, or on a result."""
        now = time.monotonic() if now is None else now
        return (not self.s.locked and not self.rec.active and self.s.mode in ("READY", "ASK_MODEL", "PROBLEMS", "FIXES", "DONE")
                and now >= self.voice_block_until)

    def start_listen(self, via="key"):
        if self.rec.active:
            return
        self.talker.stop()                                  # never record JAMES's own voice
        if self.launcher:
            self.close_launcher()
        sp = getattr(self.talker, "speaking", False)
        speaking = bool(sp() if callable(sp) else sp)
        try:
            self._before_listen = self.s.mode
            self.rec.start()
        except Exception as e:  # noqa: BLE001
            self.write(f"MIC error device={self._mic_dev()!r} talkback={'on' if self.talker_on() else 'off'} "
                       f"speaking={speaking} stats={getattr(self.rec, 'stats', None)} err={str(e)[:100]!r}")
            self.flash(f"Microphone unavailable: {e}. Press / to type the command instead.", RED, 5)
            return
        self._listen_epoch = self.owner_epoch
        self.write(f"MIC open device={self._mic_dev()!r} talkback={'on' if self.talker_on() else 'off'} "
                   f"speaking={speaking} stats={getattr(self.rec, 'stats', None)}")
        self.s.mode = "LISTENING"
        self.voice = {"state": "listening", "t0": time.monotonic(), "heard": "", "conf": None, "steps": [],
                      "via": via, "until": None, "level": 0.0}
        self.event("Listening")
        self.write(f"LISTEN start ({via})")

    def stop_listen(self, run=True, departed: bool = False):
        """Stop recording. run=True transcribes and runs the command; run=False discards it.
        departed=True: the hand left view (not a closed fist): the command is shown and needs a 'yes' first
        (iteration 3, item I: declared policy, no action runs on an accidental departure)."""
        if not self.rec.active:
            return
        self._departed = bool(run and departed)
        audio = self.rec.stop()
        self.write(f"MIC close {audio.size / 16000:.1f}s run={run} stats={getattr(self.rec, 'stats', None)}"
                   + (f" err={self.rec.last_error!r}" if getattr(self.rec, "last_error", "") else ""))
        self.voice_block_until = time.monotonic() + 0.8
        self.palm_block_until = max(self.palm_block_until, time.monotonic() + PALM_BLOCK_S)
        before = getattr(self, "_before_listen", "READY")
        if not run:
            self.s.mode = before
            if self.voice:
                self.voice.update(state="cancelled", until=time.time() + 2.5)
            self.event("Listening cancelled")
            self.write("LISTEN cancelled")
            return
        self.s.mode = "THINKING"
        if self.voice:
            self.voice.update(state="transcribing", secs=time.monotonic() - self.voice["t0"])
        self.write(f"LISTEN stop, {audio.size / 16000:.1f} s of audio")
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    def _transcribe(self, audio):
        try:
            txt, conf = self.asr.transcribe(audio, self.whisper_prompt)
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", f"Speech to text failed: {e}")); return
        self.q.put(("heard", txt, conf))

    def on_heard(self, txt, conf):
        self.s.heard = f'"{txt}"' if txt else ""
        before = getattr(self, "_before_listen", "READY")
        self.write(f"HEARD {txt!r} {conf:.2f}")
        if self.voice is None:
            self.voice = {"state": "done", "t0": time.monotonic(), "steps": [], "via": "key", "level": 0.0}
        self.voice.update(state="done", heard=txt, conf=conf, until=time.time() + VOICE_SHOW_S)
        if not txt:
            self.s.mode = before
            self.voice_step("Didn't catch that. Show the V sign and try again.", "fail")
            self.flash("Didn't catch that. Show the V sign (or press V) and try again.", AMBER); return
        if self.answer_pending(txt, before, conf):
            return
        if self.conversation(txt, conf, before):
            return
        rule_steps = C.plan(txt)
        steps, dropped = C.guard(txt, C.drop_answer_searches(rule_steps))
        for why_not in dropped:
            self.write(f"GUARD {why_not}")
        neg = C.negated_kinds(txt)
        if neg and not steps:                                 # "Do not send it." (ST-02): nothing to plan
            self.s.mode = before
            verb = sorted(neg)[0]
            self.voice_step(f"Understood. Not going to {verb} anything.", "ok")
            self.speak(f"Understood{', ' + self.sir if self.sir else ''}. I won't {verb} anything.", interrupt=True)
            self.write(f"NEGATED {sorted(neg)}: nothing run")
            return
        task_reply = before in ("PROBLEMS", "FIXING", "FIXES", "ASK_MODEL", "DONE") and not steps   # a number or a model name
        why = "" if task_reply or conf < 0.5 else C.complexity(txt, rule_steps)   # routing as before the guard
        if why and llm.available(config.CHAT_MODEL):
            self.s.mode = before                                   # complex: the local model writes the plan
            i = self.voice_step("Working out the steps", detail=f"local model ({why})")
            self.speak(f"One moment{', ' + self.sir if self.sir else ''}.")
            self.write(f"ROUTE model ({why})")
            threading.Thread(target=self._model_plan, args=(i, txt, steps), daemon=True).start()
            return
        departed, self._departed = getattr(self, "_departed", False), False
        if steps and (conf < 0.5 or departed) and not C.immediate_ok(steps):
            self.s.mode = before                                   # iteration 3: every uncertain action is confirmed
            self.ask_confirm(txt, conf, steps, "you stepped away before closing your hand" if departed and conf >= 0.5
                             else "")
            return
        if steps:
            self.s.mode = before
            self.write("ROUTE rules" + (f" (model offline; {why})" if why else ""))
            self.run_steps(steps, "")
            self.report_leftovers(txt, steps)
            return
        n = pick_number(txt)
        if before in ("PROBLEMS", "FIXES") and n is not None and n <= len(self.s.problems):
            self.s.mode = before
            self.voice_step(f"Problem {n}: {self.s.problems[n - 1]['title']}", "ok")
            self.choose(n - 1)
            return
        if before in ("PROBLEMS", "FIXING", "FIXES") and self.s.result:
            model = re.sub(r"^(it'?s|it is|this is|the model is|model is|model)\s+(an?\s+)?", "", txt.strip(), flags=re.I)
            self.s.model_text, self.s.mode = model.strip(" .!?"), before
            self.voice_step(f"Model noted: {self.s.model_text}", "ok")
            self.flash(f"Model: {self.s.model_text}", GREEN)
            return
        if before in ("ASK_MODEL", "DONE") and self.s.result:
            model = re.sub(r"^(it'?s|it is|this is|the model is|model is|model)\s+(an?\s+)?", "", txt.strip(), flags=re.I)
            model = model.strip(" .!?")
            self.s.model_text = model
            self.voice_step(f"Search parts for {model}", "ok")
            self.search(model)
            return
        self.s.mode = before
        if conf < 0.5:                                         # log 00:32:48 "I'll leave." 0.33: noise, not a command
            self.voice_step("Sorry, I didn't catch that. Try again, a little closer to the mic.", "fail")
            self.flash("Didn't catch that clearly. Show the V sign and say it again.", AMBER, 4)
            return
        self.voice_step("I'm not sure what you mean. Try: open an app, play a song, search, or identify.", "fail")
        self.flash("Say 'open' and an app, 'play' and a song, or 'search' and some words", AMBER, 4)

    YES = re.compile(r"^(?:yes|yeah|yep|yup|yes please|do it|go ahead|confirm|correct|that'?s right|right|sure|ok(?:ay)?)"
                     r"(?:\s+(?:please|james|sir))?$")
    NO = re.compile(r"^(?:no|nope|cancel|don'?t|stop|never mind|nevermind|wrong|no thanks)(?:\s+.*)?$")
    CONFIRM_S = 12.0

    YES_MIN_CONF = 0.4                                  # a 'yes' heard less clearly than this is not a yes

    def _pending(self, what: dict) -> dict:
        """A confirmation is only valid for this owner, this screen and 12 seconds (iteration 3, item A)."""
        return {**what, "until": time.monotonic() + self.CONFIRM_S, "epoch": self.owner_epoch,
                "mode": getattr(self, "_before_listen", "READY")}

    def ask_confirm(self, txt: str, conf: float, steps: list, why: str = ""):
        """Heard, but not clearly (or the speaker stepped away): show what was heard and what would run, and
        wait for a clear 'yes'. Nothing has happened yet."""
        what = T.plan_words(steps) or "that"
        self.pending = self._pending({"steps": steps, "txt": txt})
        self.write(f"LOWCONF {conf:.2f} waiting for yes{' (' + why + ')' if why else ''}: "
                   + " | ".join(st.describe() for st in steps))
        self.pending["row"] = self.voice_step("Not sure I heard that. Say yes to run it, or no." if not why
                                              else "You stepped away. Say yes to run it, or no.", "running", what)
        opener = f"I may have misheard{', ' + self.sir if self.sir else ''}." if not why else \
            f"You stepped away before closing your hand{', ' + self.sir if self.sir else ''}."
        self.speak(f"{opener} Did you mean {what}? Say yes or no.", interrupt=True)
        self.flash(f'Not sure about: "{txt}". Say YES to run it, NO to cancel.', AMBER, 6)

    def answer_pending(self, txt: str, before: str, conf: float = 1.0) -> bool:
        """The reply to ask_confirm. Only a short, clear 'yes' from the same owner on the same screen within
        12 s runs it; anything else cancels. 'Yes, but do not run it' is not a yes."""
        p, self.pending = self.pending, None
        if not p:
            return False
        low = re.sub(r"[^a-z' ]+", " ", txt.lower()).strip()
        low = re.sub(r"\s+", " ", low)
        if time.monotonic() > p["until"]:
            self.write("LOWCONF expired: nothing run")
            return False                                       # too late: treat the words as a new command
        self.s.mode = before
        stale = p.get("epoch") != self.owner_epoch or p.get("mode") != before
        if self.YES.match(low) and not stale and conf >= self.YES_MIN_CONF:
            self.write("LOWCONF confirmed by voice")
            if "row" in p:
                self.set_step(p["row"], "ok", "confirmed")
            else:
                self.voice_step("Confirmed", "ok")
            if p.get("fn"):
                p["fn"]()
            else:
                self.run_steps(p["steps"], "(confirmed)")
                self.report_leftovers(p["txt"], p["steps"])
            return True
        if "row" in p:
            self.set_step(p["row"], "ok", "cancelled")
        self.write(f"LOWCONF cancelled ({txt!r})" + (" (stale: other owner or screen)" if stale else "")
                   + (f" (yes heard at {conf:.2f})" if self.YES.match(low) and conf < self.YES_MIN_CONF else ""))
        self.voice_step("Cancelled. Nothing was run.", "ok")
        self.speak(f"Very good{', ' + self.sir if self.sir else ''}. Cancelled.", interrupt=True)
        return True

    def report_leftovers(self, txt: str, steps: list):
        """Say which part of the request no step covers, instead of letting a partial plan look complete (ST-03)."""
        for part in C.leftovers(txt, steps):
            self.write(f"NOT_DONE {part!r}")
            self.voice_step(f"Not done: \"{part}\". I can't do that part yet.", "fail")

    MUTE = re.compile(r"^(?:mute|be quiet|quiet|stop talking|shut up|silence|no voice|voice off)(?: please)?$")
    UNMUTE = re.compile(r"^(?:unmute|talk to me|speak(?: to me)?|voice on|you can talk|talk back)(?: please)?$")

    def conversation(self, txt: str, conf: float, before: str) -> bool:
        """Talking with JAMES rather than giving an app command: picking a problem, walking through fix steps,
        chit-chat, mute. Returns True when handled."""
        low = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", txt.lower())).strip()
        if before in ("PROBLEMS", "FIXING", "FIXES") and self.s.problems:
            n = pick_number(txt)
            if n is not None and n <= len(self.s.problems):
                self.s.mode = before
                title = self.s.problems[n - 1]["title"]
                if conf < 0.5:                                  # heard unclearly: confirm before acting (P0-A)
                    self.pending = self._pending({"fn": lambda: self.choose(n - 1), "txt": txt})
                    self.write(f"LOWCONF {conf:.2f} waiting for yes: problem {n}")
                    self.voice_step(f"Problem {n}, {title}? Say yes, or no.", "running")
                    self.speak(f"Did you mean problem {T.NUM.get(n, n)}, {title}? Say yes or no.", interrupt=True)
                    return True
                self.voice_step(f"Problem {n}: {title}", "ok")
                self.choose(n - 1)
                return True
        if before == "FIXES" and self.s.fix:
            action = T.nav(txt)
            if action:
                self.s.mode = before
                self.fix_nav(action)
                return True
        if self.MUTE.match(low):
            self.s.mode = before
            self.set_talkback(False, "voice")
            self.voice_step("Voice off (the switch, M or 'talk to me' turns it back on)", "ok")
            return True
        if self.UNMUTE.match(low):
            self.s.mode = before
            self.set_talkback(True, "voice")
            self.speak(f"I'm here{', ' + self.sir if self.sir else ''}.")
            return True
        if conf >= 0.5:
            reply = T.smalltalk(txt, self.status_line(), self.sir)
            if reply:
                self.s.mode = before
                self.speak(reply, interrupt=True)
                self.write(f"TALK {reply!r}")
                return True
        return False

    def fix_nav(self, action: tuple):
        """Walk through the fix out loud: next / previous / step N / repeat / all / nature / screen."""
        steps = (self.s.fix or {}).get("steps") or []
        prob = self.s.problems[self.s.choice] if self.s.choice is not None and self.s.choice < len(self.s.problems) else {}
        kind = action[0]
        if kind in ("next", "prev", "step") and steps:
            if kind == "next" and self.fix_pos >= len(steps) - 1:
                self.speak(("That was the last step. " + T.pro(self.s.fix) + " Shall I search the web for more?").strip(), True)
                return
            target = {"next": self.fix_pos + 1, "prev": max(0, self.fix_pos - 1)}.get(kind)
            if kind == "step":
                target = action[1] - 1
                if not 0 <= target < len(steps):
                    self.speak(f"There are only {T.NUM.get(len(steps), len(steps))} steps.", True)
                    return
            self.fix_pos = target
            self.s.expanded = self.fix_pos
            self.speak(T.step(self.fix_pos + 1, steps[self.fix_pos]), True)
        elif kind == "repeat":
            self.speak(self.talker.last or self.status_line(), True)
        elif kind == "all" and steps:
            self.speak(" ".join(T.step(i, st) for i, st in enumerate(steps, 1)), True)
        elif kind == "nature" and prob:
            fix = self.s.fix or {}
            sign = str(prob.get("sign") or "").rstrip(".")
            words = [f"{prob['title']}."]
            if sign:
                words.append(f"The usual sign is {sign[0].lower() + sign[1:]}.")
            if fix.get("cause"):
                words.append(str(fix["cause"]).rstrip(".") + ".")
            words.append("I can see it in the snapshot." if prob.get("seen") else "I can't confirm it from the snapshot.")
            self.speak(" ".join(words), True)
        else:
            self.speak(self.status_line(), True)
        self.write(f"FIXNAV {action}")

    # ---------- voice plan ----------
    def speak(self, text: str, interrupt: bool = False):
        """JAMES says it out loud, and the voice window shows it."""
        if not text:
            return
        self.talker.say(text, interrupt)
        if self.voice is None:
            self.voice = {"state": "done", "t0": time.monotonic(), "heard": "", "steps": [], "via": "key", "level": 0.0}
        self.voice["said"] = T.clean(text)
        self.voice["until"] = time.time() + max(VOICE_SHOW_S, 2 + len(T.clean(text)) / 14)

    def status_line(self) -> str:
        """What is on the screen, in a sentence or two (for 'what's happening' and 'read the screen')."""
        s = self.s
        if s.mode in ("PROBLEMS", "FIXING") and s.result and s.problems:
            return T.identified(str(s.result.get("object") or "object"), str(s.result.get("problem") or ""),
                                s.problems, s.model_text)
        if s.mode == "FIXES" and s.fix and s.choice is not None:
            steps = s.fix.get("steps") or []
            p = s.problems[s.choice]["title"]
            return (f"We're on {p}, step {T.NUM.get(self.fix_pos + 1, self.fix_pos + 1)} of {len(steps)}. "
                    + (T.step(self.fix_pos + 1, steps[self.fix_pos]) if steps else ""))
        if s.mode in ("FRAMING", "IDENTIFYING"):
            return "I'm looking at what you're holding up."
        if s.mode == "SEARCHING":
            return f"I'm searching the web for {s.query}."
        if s.mode == "DONE" and s.results:
            return f"The web found {len(s.results)} results. The top one is {s.results[0]['title']}."
        if self.launcher:
            return "The launcher is open. Point at an app and pinch."
        return f"All quiet{', ' + self.sir if self.sir else ''}. Nothing is running. Show the V sign and tell me what you need."

    def voice_step(self, label: str, status="running", detail="") -> int:
        """Add a row to the voice window. status: running | ok | fail."""
        if self.voice is None:
            self.voice = {"state": "done", "t0": time.monotonic(), "heard": "", "steps": [], "via": "key", "level": 0.0}
        self.voice["steps"].append([label, status, detail])
        self.voice["until"] = time.time() + VOICE_SHOW_S
        if status == "fail":
            self.talker.say(label)
        return len(self.voice["steps"]) - 1

    def set_step(self, i: int, status: str, detail=""):
        if self.voice and 0 <= i < len(self.voice["steps"]):
            before = self.voice["steps"][i][1]
            self.voice["steps"][i][1:] = [status, detail]
            self.voice["until"] = time.time() + VOICE_SHOW_S
            if status == "fail" and before != "fail":
                self.talker.say(T.fail(detail))

    def browser_where(self, name: str) -> str | None:
        if not name:
            return None
        hit = LA.find_app(name, self.installed)
        return hit[1] if hit else None

    def run_plan(self, steps: list):
        for st in steps:
            if st.kind == "open":
                i = self.voice_step(st.describe() if st.browser else f"Open {LA.clean_query(st.arg) or st.arg}")
                ok, msg = self.open_in_browser(st.arg, st.browser) if st.browser else self.open_by_name(st.arg)
                self.set_step(i, "ok" if ok else "fail", msg)
            elif st.kind == "play" and st.service == "spotify":
                i = self.voice_step(st.describe(), detail="finding the track")
                self.last_media = "spotify"
                threading.Thread(target=self._play_spotify, args=(i, st.arg), daemon=True).start()
                self.event(f"Spotify: {st.arg}")
            elif st.kind == "play":
                i = self.voice_step(st.describe(), detail="finding the top video")
                self.last_media = "youtube"
                threading.Thread(target=self._play_youtube, args=(i, st.arg, st.browser), daemon=True).start()
            elif st.kind == "ask":
                i = self.voice_step(st.describe())
                url = C.ask_url(st.service, st.arg)
                where = self.browser_where(st.browser)
                LA.open_url(url, where)
                copied = LA.copy_text(st.arg)
                self.last_chat = st.service
                self.ctx["chat"] = st.service
                self.ctx["chat_prompt"], self.ctx["chat_t"] = st.arg, time.monotonic()
                name = C.ASK_NAME.get(st.service, st.service)
                detail = (f"{name} opened with your prompt" if C.ASK_URL.get(st.service) else f"{name} opened")
                detail += "; also copied (Cmd+V if the box is empty)" if copied else ""
                self.set_step(i, "ok", detail + (f" in {st.browser}" if where else ""))
                self.write(f"ASK {st.service} {st.arg!r} -> {url.split('?')[0]}{' +clipboard' if copied else ''}")
                self.event(f"Asked {name}")
                if st.service in media.SEND_JS and getattr(config, "ASK_AUTO_SEND", False):
                    j = self.voice_step(f"Press send in {name}", detail="waiting for the page")
                    threading.Thread(target=self._send, args=(j, st.service, st.arg, 10.0), daemon=True).start()
                elif st.service in media.SEND_JS:                   # iteration 2 P0-C: filled, not sent
                    self.voice_step(f"Your prompt is ready in {name}. Say 'send it', or press Enter.", "ok")
            elif st.kind == "send":
                who = st.service or self.last_chat
                fresh = time.monotonic() - self.ctx.get("chat_t", -1e9) < 600
                prompt = self.ctx.get("chat_prompt", "") if self.ctx.get("chat") == who and fresh else ""
                i = self.voice_step(st.describe())
                threading.Thread(target=self._send, args=(i, who, prompt, 0.0), daemon=True).start()
            elif st.kind == "close":
                i = self.voice_step(st.describe())
                found = LA.find_app(st.arg, self.installed)
                if found is None:
                    self.set_step(i, "fail", f'no app called "{LA.said_name(st.arg)}" to close')
                else:
                    threading.Thread(target=self._close, args=(i, found[0], found[1]), daemon=True).start()
            elif st.kind == "collect":
                i = self.voice_step(st.describe(), detail="fetching the links (text only)")
                self.ctx["search"] = (st.site, st.arg, st.top)
                threading.Thread(target=self._collect, args=(i, st), daemon=True).start()
            elif st.kind == "message":
                i = self.voice_step(st.describe())
                handle = self.contact(st.to, st.service)
                url = C.message_url(st.service, handle, st.arg)
                where = self.browser_where(st.browser or "chrome")
                LA.open_url(url, where)
                copied = LA.copy_text(st.arg)
                self.ctx["contact"] = st.to
                app = "Instagram" if st.service == "instagram" else "WhatsApp"
                if handle:
                    detail = (f"chat with {st.to.title()} opened; message " + ("prefilled and copied" if st.service == "whatsapp"
                              else "copied: Cmd+V") + ", check it and press Enter yourself")
                else:
                    detail = (f"{app} inbox opened (no {app} handle for {st.to.title()} in data/contacts.json); "
                              "message copied: open the chat, Cmd+V, Enter")
                self.set_step(i, "ok" if copied or handle else "fail", detail)
                self.write(f"MESSAGE {st.service} to={st.to!r} handle={'yes' if handle else 'no'} -> {url.split('?')[0]}"
                           f"{' +clipboard' if copied else ''} (never auto-sent)")
                self.event(f"{app}: message for {st.to.title()} ready")
            elif st.kind == "media":
                i = self.voice_step(st.describe())
                threading.Thread(target=self._media, args=(i, st.arg, st.service or self.last_media), daemon=True).start()
            elif st.kind == "search":
                i = self.voice_step(st.describe())
                where = self.browser_where(st.browser)
                url = C.site_search_url(st.site, st.arg, st.top) if st.site else C.web_search_url(st.arg)
                LA.open_url(url, where)
                self.set_step(i, "ok", f"opened in {st.browser}" if where else "opened in the browser")
                self.write(f"OPEN_URL {url}")
                self.event(f"Searched: {st.arg}")
                self.ctx["search"] = (st.site, st.arg, st.top)
            elif st.kind in ("display", "glass"):
                i = self.voice_step(st.describe())
                url = C.ud_url(st.arg, st.kind)
                LA.open_url(url, self.browser_where(st.browser))
                self.set_step(i, "ok", f"universaldisplay.in ({UD_NOTE}), public page")
                self.write(f"OPEN_URL {url} (universaldisplay.in, public page)")
                self.event("Universal Display opened")
            elif st.kind == "identify":
                i = self.voice_step(st.describe())
                self.press("identify")
                self.set_step(i, "ok" if self.s.mode == "FRAMING" else "fail",
                              "hold it in the square" if self.s.mode == "FRAMING" else "vision model not running")

    def _model_plan(self, i: int, txt: str, fallback: list):
        """Complex request: ask the local model for the steps (validated in agents/planner.py)."""
        t0 = time.time()
        try:
            steps, said = PL.plan_full(txt, self.ctx_line())
            how = "model"
        except llm.LLMUnavailable as e:
            steps, said, how = [], "", f"model failed: {e}"
        if not steps and fallback:
            steps, how = fallback, how + "; used the rule plan"
        self.q.put(("model_plan", i, steps, how, time.time() - t0, said, txt))

    def _close(self, i: int, name: str, where: str):
        ok, detail = media.quit_app(where, name)
        if ok and self.ctx.get("app") == name:
            self.ctx.pop("app", None)
        self.q.put(("step", i, "ok" if ok else "fail", detail))
        self.q.put(("log", f"CLOSE {name} -> {'ok' if ok else 'fail'} {detail}"))

    def _collect(self, i: int, st):
        """Fetch the links (text), copy them, and write them into a new note."""
        src = {"github": "GitHub", "youtube": "YouTube"}.get(st.site, "Web")
        title = f"{src}: {C.human_query(st.arg)}"
        try:
            items = collect.gather(st.site, st.arg, top=st.top)
        except collect.CollectError as e:
            self.q.put(("step", i, "fail", str(e)))
            self.q.put(("log", f"COLLECT {st.site or 'web'} {st.arg!r} -> fail {e}"))
            return
        if not items:
            self.q.put(("step", i, "fail", "no results found"))
            self.q.put(("log", f"COLLECT {st.site or 'web'} {st.arg!r} -> 0 results"))
            return
        copied = LA.copy_text(collect.as_text(title, items))
        if st.to == "notes":
            ok, where = collect.to_notes(title, items)
        else:
            ok, where = copied, "clipboard"
        detail = (f"{len(items)} links saved: {where}" + (" and copied" if copied and st.to == "notes" else "")
                  if ok else f"{len(items)} links found; Notes failed ({where})" + ("; they are on the clipboard" if copied else ""))
        self.q.put(("step", i, "ok" if ok or copied else "fail", detail))
        self.q.put(("log", f"COLLECT {st.site or 'web'} {st.arg!r} -> {len(items)} links, notes={'ok' if ok else 'fail'}, "
                           f"clipboard={'ok' if copied else 'no'}"))

    def ctx_line(self) -> str:
        """What happened just before, for the planner ('close it' needs to know what 'it' is)."""
        c, parts = self.ctx, []
        if c.get("app"):
            parts.append(f"last app opened: {c['app']}")
        if c.get("search"):
            site, q, _ = c["search"]
            parts.append(f"last search: {q!r} on {site or 'the web'}")
        if c.get("chat"):
            parts.append(f"AI chat open: {c['chat']}")
        if c.get("contact"):
            parts.append(f"last person messaged: {c['contact']}")
        return "; ".join(parts)

    def contact(self, name: str, service: str) -> str:
        """The saved handle for a person (data/contacts.json: {"sam": {"instagram": "user.name", "whatsapp": "+91..."}})."""
        import json
        try:
            book = json.loads((config.DATA / "contacts.json").read_text())
        except (OSError, ValueError):
            return ""
        entry = book.get((name or "").lower()) or {}
        return str(entry.get(service) or "").strip() if isinstance(entry, dict) else ""

    def run_steps(self, steps: list, source: str):
        """Tie the steps to the earlier commands (close it, those links, send it) and run them."""
        steps, problems = C.resolve(steps, self.ctx)
        for msg in problems:
            self.voice_step(msg, "fail")
            self.flash(msg, AMBER, 4)
        if steps:
            self.write(f"PLAN{source} " + " | ".join(st.describe() for st in steps))
            self.speak(T.narrate(steps, self.sir), interrupt=True)
            self.run_plan(steps)

    def _send(self, i: int, who: str, prompt: str, wait_s: float):
        ok, detail = media.chat_send(who, prompt, wait_s)
        self.q.put(("step", i, "ok" if ok else "fail", detail))
        self.q.put(("log", f"SEND {who or '-'} -> {'ok' if ok else 'fail'} {detail}"))

    def _media(self, i: int, action: str, prefer: str):
        ok, detail, where = media.control(action, prefer)
        if ok and where:
            self.last_media = where
        self.q.put(("step", i, "ok" if ok else "fail", f"{where.title() if where else ''} {detail}".strip()))
        self.q.put(("log", f"MEDIA {action} prefer={prefer or '-'} -> {'ok' if ok else 'fail'} {where} {detail}"))

    def _play_spotify(self, i: int, q: str):
        """Find the track (one text web search: '<song> spotify track'), then play it in the Spotify app.
        Falls back to Spotify's search page when no track is found."""
        tid = C.spotify_track_id(scout.search(f"{q} spotify track", n=6))
        app = self.browser_where("spotify")
        if tid and app and sys.platform == "darwin":
            ok, detail = media.spotify_play_track(tid)
        elif tid:
            LA.open_url(f"https://open.spotify.com/track/{tid}")
            ok, detail = True, "track opened in the Spotify web player (press play)"
        elif app or sys.platform.startswith("win"):
            LA.open_url(C.spotify_search_uri(q))
            ok, detail = True, "track not found; Spotify search opened"
        else:
            LA.open_url("https://open.spotify.com/search/" + urllib.parse.quote(q))
            ok, detail = True, "track not found; Spotify web search opened"
        self.q.put(("step", i, "ok" if ok else "fail", detail))
        self.q.put(("log", f"PLAY spotify {q!r} -> {('spotify:track:' + tid) if tid else 'search'} ({detail})"))

    def _play_youtube(self, i: int, q: str, browser: str):
        """Resolve the top YouTube video (one text request) and open it; fall back to the results page."""
        url, how = None, "top video"
        try:
            import urllib.request
            egress.check("consumer_web", C.youtube_search_url(q), len(q))
            req = urllib.request.Request(C.youtube_search_url(q), headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                              "Version/17.0 Safari/605.1.15", "Accept-Language": "en"})
            with egress.urlopen(req, "consumer_web", timeout=5) as r:
                url = C.first_youtube_video(r.read(2_000_000).decode("utf-8", "ignore"))
        except Exception:  # noqa: BLE001
            url = None
        if not url:
            url, how = C.youtube_search_url(q), "results page"
        where = self.browser_where(browser)
        LA.open_url(url, where)
        self.q.put(("step", i, "ok", f"{how} opened" + (f" in {browser}" if where else "")
                    + ("" if where or not browser else f" ({browser} not found, default browser)")))
        self.q.put(("log", f"PLAY youtube {q!r} -> {url}"))

    def can_open(self, spoken: str) -> bool:
        """Would 'open <spoken>' find something real (launcher tile, installed app, known website)? Opens nothing."""
        q = LA.clean_query(spoken)
        if any(q and (q == LA._norm(a.label) or q == a.id) for a in self.apps):
            return True
        return bool(LA.find_app(spoken, self.installed) or C.WEB_APPS.get(q) or C.WEB_APPS.get(LA.said_name(spoken)))

    def open_in_browser(self, name: str, browser: str):
        """'Open ChatGPT on Chrome': the website in that browser, never the desktop app (live 04:21:34). A name
        with no known site becomes a web search in that browser, and JAMES says so (live 04:23:26 'IMDB')."""
        url, how = C.web_target(name)
        where = self.browser_where(browser)
        short = browser.replace("Google ", "").replace(" Browser", "")
        LA.open_url(url, where)
        nice = T.NICE.get(name.lower(), name)
        self.write(f"OPEN_WEB {name!r} in {browser if where else 'the default browser'} -> {url} ({how})")
        self.ctx["app"] = browser if where else self.ctx.get("app")
        self.flash(f"Opening {nice} in {short if where else 'the browser'}", GREEN)
        note = "" if where else f" ({short} isn't installed, so the default browser)"
        if how == "search":
            return True, f'no site I know is called "{name}", so I searched for it{note}'
        return True, f"{nice} opened{note}"

    def open_by_name(self, spoken: str):
        """Voice 'open <name>': a launcher target first, else any installed app (launch-only)."""
        q = LA.clean_query(spoken)
        hit = next((a for a in self.apps if q and (q == LA._norm(a.label) or q == a.id)), None)
        if hit:
            self.press(f"app:{hit.id}")
            return True, f"{hit.label} opened"
        found = LA.find_app(spoken, self.installed)
        if found is None:                                      # maybe installed since start-up: rescan once
            self.installed = LA.installed_apps()
            found = LA.find_app(spoken, self.installed)
        web = C.WEB_APPS.get(q) or C.WEB_APPS.get(LA.said_name(spoken))
        if found is None and web:                              # log 01:36:24: no Instagram app -> instagram.com
            LA.open_url(web)
            self.flash(f"Opening {LA.said_name(spoken).title()} in the browser", GREEN)
            self.write(f"OPEN_WEB {spoken!r} -> {web}")
            return True, "opened the website (no app installed)"
        dom = spoken.strip().lower().rstrip(". ")
        if found is None and not web and C.web_target(dom)[1] == "domain":   # 'open asphalt.com'
            url = C.web_target(dom)[0]
            LA.open_url(url)
            self.flash(f"Opening {dom}", GREEN)
            self.write(f"OPEN_WEB {spoken!r} -> {url} (domain)")
            return True, f"{dom} opened in the browser"
        if found is None:
            name = LA.said_name(spoken)                        # log 01:12:09: name only the app part, not the sentence
            self.flash(f'No app called "{name}" on this computer', RED, 4)
            self.write(f"OPEN_BY_NAME no match for {spoken!r} (cleaned {q!r})")
            return False, f'no app called "{name}" found'
        name, where, score = found
        LA.open_installed(where)
        self.flash(f"Opening {name}", GREEN)
        self.event(f"{name} opened")
        self.write(f"OPEN_BY_NAME {spoken!r} -> {name} ({score:.2f}) {where}")
        self.ctx["app"] = name
        rest = LA.leftover(spoken, name)                      # "open X and <request>": the request goes to the clipboard
        if rest and LA.copy_text(rest):
            self.write(f"CLIPBOARD {rest!r}")
            self.flash(f"Opening {name}. Your request is copied: paste it with Cmd+V", GREEN, 5)
            return True, f"{name} opened; request copied (Cmd+V)"
        return True, f"{name} opened"

    # ---------- problems -> fixes ----------
    def choose(self, i: int):
        """Pinched problem i: ask for its fix steps (local model, text only; built-in guide if it is down)."""
        s = self.s
        if s.mode not in ("PROBLEMS", "FIXES", "FIXING") or not (0 <= i < len(s.problems)) or not s.result:
            return
        s.choice, s.fix, s.expanded, s.mode, s.t_fix = i, None, None, "FIXING", time.time()
        prob = s.problems[i]
        obj = str(s.result.get("object") or "object")
        self.event(f"Problem: {prob['title']}")
        self.write(f"PROBLEM chosen {i + 1} {prob['title']!r}")
        self.speak(T.chosen(prob), interrupt=True)
        threading.Thread(target=self._fix, args=(i, obj, self.device_name(), prob), daemon=True).start()

    def _fix(self, i, obj, name, prob):
        self.q.put(("fix", i, vision.fix_for(obj, name, prob)))

    def device_name(self) -> str:
        r = self.s.result or {}
        if self.s.model_text:
            return self.s.model_text
        return " ".join(x for x in (r.get("brand"), r.get("model")) if x) if not vision.needs_model(r) else ""

    def search_parts(self):
        """Find part (if the fix names one) or fixes for the chosen problem on the web, text only."""
        s, r = self.s, self.s.result or {}
        prob = s.problems[s.choice]["title"] if s.choice is not None and s.choice < len(s.problems) else None
        part = (s.fix or {}).get("part_needed")
        self.run_search(scout.query_for(part, None, self.device_name(), prob, str(r.get("object") or "")))

    def search(self, model: str):
        """Voice gave a model name: search for the chosen problem's part / fix with it."""
        self.s.model_text = model
        self.search_parts()

    def run_search(self, q: str):
        self.s.query = q
        self.s.mode = "SEARCHING"
        self.write(f"WEB_QUERY {self.s.query!r} (text only; no image sent)")
        threading.Thread(target=self._search, args=(self.s.query,), daemon=True).start()

    def _search(self, q):
        res = scout.search(q)
        self.q.put(("results", res, scout.recommend(q, res)))

    def open_top(self):
        url = self.s.results[0]["url"] if self.s.results else (scout.browser_url(self.s.query) if self.s.query else None)
        if not url:
            self.flash("Nothing to open yet", AMBER); return
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
        elif sys.platform.startswith("win"):
            import os
            os.startfile(url)                                  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", url])
        self.flash("Opening in the browser", GREEN)
        self.event("Result opened in the browser")
        self.write(f"OPEN_URL {url}")

    # ---------- universal display (phones) ----------
    def is_phone(self) -> bool:
        r = self.s.result or {}
        return bool(PHONE_WORDS.search(" ".join(str(r.get(k) or "") for k in ("object", "brand", "model"))
                                       + " " + self.s.model_text))

    def phone_model(self) -> str:
        """The model name Universal Display pages are named after, e.g. 'Redmi Note 10'."""
        r = self.s.result or {}
        return (self.s.model_text or str(r.get("model") or "")).strip()

    # ---------- task actions (right side, pinchable, replace the launcher while a task runs) ----------
    def task_actions(self) -> list[Button]:
        m = self.s.mode
        acts = {
            "FRAMING": [("identify", "Snap now"), ("cancel", "Cancel")],
            "IDENTIFYING": [("cancel", "Cancel")],
            "ASK_MODEL": [("talk", "Talk"), ("cancel", "Cancel")],
            "LISTENING": [("talk", "Stop"), ("cancel", "Cancel")],
            "THINKING": [("cancel", "Cancel")],
            "SEARCHING": [("cancel", "Cancel")],
            "DONE": [("open", "Open result"), ("done", "Done")],
            "PROBLEMS": [("talk", "Say model"), ("cancel", "Cancel")],
            "FIXING": [("back", "Other problems"), ("cancel", "Cancel")],
            "FIXES": [("parts", "Find part" if (self.s.fix or {}).get("part_needed") else "Search web"),
                      ("back", "Other problems"), ("done", "Done")],
        }.get(m, [])
        if m == "PROBLEMS" and not vision.needs_model(self.s.result or {}):
            acts = acts[1:]                                      # model already known
        if m == "DONE" and self.s.fix:
            acts = [acts[0], ("fixes", "Fix steps"), acts[1]]
        if m in ("DONE", "FIXES") and self.is_phone():           # phone identified: compatible parts on Universal Display
            acts = acts[:-1] + [("ud_display", "Displays"), ("ud_glass", "Glass")] + acts[-1:]
        return [Button(k, l) for k, l in acts]

    # ---------- radial launcher ----------
    def launcher_actions(self) -> list[Button]:
        return [Button("identify", "Identify"), Button("talk", "Talk")]

    def launcher_apps(self) -> list[Button]:
        return [Button(f"app:{a.id}", a.label, "app", a.icon) for a in self.apps]

    def open_launcher(self, c, W, H):
        R = self.radius(len(self.apps))
        cx = int(min(max(c[0], R + APP_W // 2 + 20), W - R - APP_W // 2 - 20))
        cy = int(min(max(c[1], 52 + R + APP_H // 2 + 10), H - 30 - R - APP_H // 2 - 10))
        now = time.time()
        self.launcher = {"c": (cx, cy), "t": now, "last_hover": now}
        self.event("Launcher opened")
        self.write("LAUNCHER open")

    def close_launcher(self):
        self.launcher = None
        self.palm_block_until = time.monotonic() + PALM_BLOCK_S

    @staticmethod
    def radius(n):
        return int(max(210, n * (APP_W + 20) / (2 * np.pi)))

    def launcher_layout(self) -> list[Button]:
        cx, cy = self.launcher["c"]
        k = min(1.0, (time.time() - self.launcher["t"]) / 0.18)      # short open motion
        k = 1 - (1 - k) ** 3
        apps = self.launcher_apps()
        R = self.radius(len(apps))
        for i, b in enumerate(apps):
            a = -np.pi / 2 + 2 * np.pi * i / max(1, len(apps))
            x, y = int(cx + k * R * np.cos(a)), int(cy + k * R * np.sin(a))
            b.rect = (x - APP_W // 2, y - APP_H // 2, APP_W, APP_H)
        acts = self.launcher_actions()
        total = len(acts) * ACT_W + (len(acts) - 1) * J.S2
        for i, b in enumerate(acts):
            b.rect = (cx - total // 2 + i * (ACT_W + J.S2), cy + 4, ACT_W, ACT_H)
        return acts + apps

    def launcher_hit(self, tip):
        if not self.launcher or tip is None:
            return None
        for b in self.launcher_layout():
            if b.hit(tip):
                return b.key
        return None

    # ---------- drawing: pieces ----------
    @staticmethod
    def glyph(img, key, x, y, col):
        """Small geometric glyph per action (no decorative icons)."""
        if key == "identify":
            J.brackets(img, x - 9, y - 9, 18, 18, col, L=5, thick=1)
            cv2.circle(img, (x, y), 2, col, -1, cv2.LINE_AA)
        elif key == "talk":
            cv2.rectangle(img, (x - 3, y - 9), (x + 3, y + 3), col, 1, cv2.LINE_AA)
            cv2.ellipse(img, (x, y), (7, 7), 0, 0, 180, col, 1, cv2.LINE_AA)
            cv2.line(img, (x, y + 7), (x, y + 10), col, 1, cv2.LINE_AA)
        elif key in ("ud_display", "ud_glass"):
            cv2.rectangle(img, (x - 5, y - 9), (x + 5, y + 9), col, 1, cv2.LINE_AA)       # a phone outline
            if key == "ud_glass":
                cv2.line(img, (x - 2, y - 5), (x + 3, y - 1), col, 1, cv2.LINE_AA)
            else:
                cv2.line(img, (x - 2, y + 6), (x + 2, y + 6), col, 1, cv2.LINE_AA)
        elif key == "open":
            cv2.line(img, (x - 6, y + 6), (x + 6, y - 6), col, 1, cv2.LINE_AA)
            cv2.line(img, (x + 6, y - 6), (x, y - 6), col, 1, cv2.LINE_AA)
            cv2.line(img, (x + 6, y - 6), (x + 6, y), col, 1, cv2.LINE_AA)
        elif key == "parts":                                     # magnifier
            cv2.circle(img, (x - 2, y - 2), 6, col, 1, cv2.LINE_AA)
            cv2.line(img, (x + 3, y + 3), (x + 8, y + 8), col, 2, cv2.LINE_AA)
        elif key == "back":                                      # arrow left
            cv2.line(img, (x - 7, y), (x + 7, y), col, 1, cv2.LINE_AA)
            cv2.line(img, (x - 7, y), (x - 2, y - 5), col, 1, cv2.LINE_AA)
            cv2.line(img, (x - 7, y), (x - 2, y + 5), col, 1, cv2.LINE_AA)
        elif key == "fixes":                                     # a short list
            for dy in (-5, 0, 5):
                cv2.line(img, (x - 6, y + dy), (x + 6, y + dy), col, 1, cv2.LINE_AA)
        elif key in ("cancel", "done"):
            if key == "cancel":
                cv2.line(img, (x - 6, y - 6), (x + 6, y + 6), col, 1, cv2.LINE_AA)
                cv2.line(img, (x - 6, y + 6), (x + 6, y - 6), col, 1, cv2.LINE_AA)
            else:
                cv2.line(img, (x - 7, y), (x - 2, y + 5), col, 2, cv2.LINE_AA)
                cv2.line(img, (x - 2, y + 5), (x + 7, y - 6), col, 2, cv2.LINE_AA)

    @staticmethod
    def _icon(size: int, bgra: np.ndarray) -> np.ndarray:
        key = (id(bgra), size)
        cache = James._icon_cache
        if key not in cache:
            u8 = np.clip(bgra, 0, 255).astype(np.uint8)
            cache[key] = cv2.resize(u8, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32)
        return cache[key]
    _icon_cache: dict = {}

    def tile(self, img, b: Button, state: str, progress=0.0):
        """state: normal | targeted | confirming.
        App: a floating logo (no panel) with its name; targeting draws a cyan ring, confirming an amber ring
        that fills with the pinch. Action: a small surface with a glyph and label."""
        x, y, w, h = b.rect
        if b.kind == "app":
            s = 50 if state == "normal" else 56                    # a slight lift when targeted
            cx, cy = x + w // 2, y + 34
            ring = 36
            if state == "targeted":
                cv2.circle(img, (cx, cy), ring, J.CYAN, 2, cv2.LINE_AA)
            elif state == "confirming":
                cv2.circle(img, (cx, cy), ring, J.MUTED, 1, cv2.LINE_AA)
                cv2.ellipse(img, (cx, cy), (ring, ring), -90, 0, int(360 * min(1, progress)), J.CONFIRM, 3, cv2.LINE_AA)
            if b.icon is not None:
                J.blit_rgba(img, self._icon(s, b.icon), cx - s // 2, cy - s // 2)
            else:                                                  # monogram only if no icon exists at all
                cv2.circle(img, (cx, cy), s // 2, J.TEXT_2, 1, cv2.LINE_AA)
                J.text(img, b.label[:1], cx, cy + 7, 20, J.TEXT, "semi", anchor="c")
            label = J.fit(b.label, w, 13)
            col = J.CYAN if state == "targeted" else (J.CONFIRM if state == "confirming" else J.TEXT)
            kind = "semi"
            J.text(img, label, cx + 1, y + h - 5, 13, J.BLACK, kind, anchor="c", opacity=0.85)   # soft shadow for legibility
            J.text(img, label, cx, y + h - 6, 13, col, kind, anchor="c")
            return
        border = {"targeted": J.CYAN, "confirming": J.CONFIRM}.get(state, J.RULE)
        J.panel(img, x, y, w, h, 0.9 if state == "normal" else 0.95, J.SURFACE_2 if state == "normal" else J.SURFACE,
                border=border)
        if state != "normal":
            cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), border, 2)
        col = J.TEXT if state != "normal" else J.TEXT_2
        gc = J.CYAN if state == "targeted" else (J.CONFIRM if state == "confirming" else J.TEXT_2)
        self.glyph(img, b.key, x + 22, y + h // 2, gc)
        J.text(img, J.fit(b.label, w - 46, 14), x + 40, y + h // 2 + 5, 14, col, "semi" if state != "normal" else "sans")
        if state == "confirming" and progress > 0:
            cv2.rectangle(img, (x + 2, y + h - 4), (x + 2 + int((w - 4) * min(1, progress)), y + h - 2), J.CONFIRM, -1)

    # ---------- problems / fix steps panel (pinchable rows, middle) ----------
    def choice_box(self, W, H) -> tuple[int, int, int, int]:
        """x, y, w, max_h of the panel: right of the context card and snapshot, left of the task actions."""
        cw = min(460, max(360, int(W * 0.33)))
        x = J.S4 + cw + J.S2 + 112 + J.S3
        w = min(CHOICE_W, W - x - DOCK_W - 2 * J.S4)
        y = 52 + J.S4
        return x, y, w, H - y - 170

    def choice_rows(self, w) -> list[Button]:
        s = self.s
        if s.mode == "PROBLEMS":
            return [Button(f"prob:{i}", p["title"], "problem", sub=p.get("sign", ""), tag="SEEN" if p.get("seen") else "")
                    for i, p in enumerate(s.problems)]
        if s.mode == "FIXES" and s.fix:
            return [Button(f"step:{i}", st["title"], "step", sub=st.get("detail", ""), open=(s.expanded == i))
                    for i, st in enumerate(s.fix.get("steps", []))]
        return []

    def draw_choices(self, img, hover, progress) -> list[Button]:
        """PROBLEMS: the 4 common problems, pinch one. FIXING: a spinner. FIXES: steps, pinch to expand.
        Returns the pinchable rows (their rects are what the next frame hit-tests)."""
        s = self.s
        if s.mode not in ("PROBLEMS", "FIXING", "FIXES") or self.launcher or s.locked:
            self._voice_top = 0
            return []
        H, W = img.shape[:2]
        x, y, w, max_h = self.choice_box(W, H)
        if w < 260:
            return []
        iw = w - 2 * J.S4
        obj = str((s.result or {}).get("object") or "object")
        chosen = s.problems[s.choice]["title"] if s.choice is not None and s.choice < len(s.problems) else ""
        if s.mode == "PROBLEMS":
            seen = any(p.get("seen") for p in s.problems)
            title = f"{'Seen damage + possible issues' if seen else 'Possible issues'}  ·  {obj}"
            sub = ("Only SEEN is visible in the snapshot; the rest are common for this kind of object." if seen
                   else "Nothing damaged is visible. These are common for this kind of object. Pinch the one you have.")
        elif s.mode == "FIXING":
            title, sub = "Finding fixes", chosen
        else:
            title, sub = "Fix steps", chosen
        rows = self.choice_rows(w)
        # heights: fixed rows; the open step grows with its detail
        GAP, head_h = 8, 58
        details = {}
        for b in rows:
            if b.kind == "problem":
                b.rect = (0, 0, iw, 56)
            else:
                lines = J.wrap(b.sub, iw - 56, 13)[:7] if b.open else []
                details[b.key] = lines
                b.rect = (0, 0, iw, 40 + (18 * len(lines) + 10 if lines else 0))
        foot = []
        if s.mode == "FIXES" and s.fix:
            if s.fix.get("safety"):
                foot += [("Safety" if k == 0 else "", ln, J.CONFIRM, 1)
                         for k, ln in enumerate(J.wrap(s.fix["safety"], iw - 80, 12)[:2])]
            if s.fix.get("pro_when"):
                foot += [("Get a pro" if k == 0 else "", ln, J.TEXT_2, 1)
                         for k, ln in enumerate(J.wrap(s.fix["pro_when"], iw - 80, 12)[:2])]
            src = {"model": "Generic guidance from the local model. Not an approved or OEM procedure"
                            + (f"  ·  {s.fix_took_s:.1f} s" if s.fix_took_s else ""),
                   "catalog": "Built-in generic guide (local model not running). Not an approved or OEM procedure",
                   "none": "No steps available"}.get(s.fix.get("source"), "")
            foot.append(("", src, J.MUTED, 0))
        elif s.mode == "PROBLEMS":
            src = ("Suggested by the local model for this kind of object" if any(p.get("source") == "model" for p in s.problems)
                   else "Built-in list for this kind of object")
            foot.append(("", src + ", not a diagnosis", J.MUTED, 0))
        body = sum(b.rect[3] + GAP for b in rows) if rows else (44 if s.mode == "FIXING" else 0)
        h = head_h + body + 18 * len(foot) + J.S3
        while h > max_h and rows and s.mode == "FIXES" and any(details.values()):   # too tall: shorten the open step
            k = next(k for k, v in details.items() if v)
            details[k] = details[k][:-1]
            b = next(b for b in rows if b.key == k)
            b.rect = (0, 0, iw, 40 + (18 * len(details[k]) + 10 if details[k] else 0))
            h -= 18
        h = min(h, max_h)
        J.panel(img, x, y, w, h, 0.9, accent=J.CONFIRM if s.mode == "PROBLEMS" else J.CYAN)
        J.label(img, title, x + J.S4, y + 24, J.CONFIRM if s.mode == "PROBLEMS" else J.CYAN)
        J.text(img, J.fit(sub, iw, 13), x + J.S4, y + 44, 13, J.TEXT_2)
        yy = y + head_h
        if s.mode == "FIXING":
            a = int(time.monotonic() * 360) % 360
            cv2.ellipse(img, (x + J.S4 + 8, yy + 16), (8, 8), a, 0, 270, J.CYAN, 2, cv2.LINE_AA)
            J.text(img, "Asking the local model (text only, no image)", x + J.S4 + 26, yy + 21, 13, J.TEXT)
            yy += 44
        for n, b in enumerate(rows, 1):
            bw, bh = b.rect[2], b.rect[3]
            if yy + bh > y + h - 18 * len(foot) - 4:
                break
            b.rect = (x + J.S4, yy, bw, bh)
            state = ("confirming" if progress > 0 else "targeted") if b.key == hover else "normal"
            self.draw_row(img, b, n, state, progress, details.get(b.key, []))
            yy += bh + GAP
        fy = y + h - 18 * len(foot) - 6
        for lab, ln, col, indent in foot:
            if lab:
                J.text(img, lab.upper(), x + J.S4, fy + 12, 10, J.MUTED, "semi", track=0.8)
            J.text(img, ln, x + J.S4 + 80 * indent, fy + 13, 12, col)
            fy += 18
        self._voice_top = y + h + J.S3
        return [b for b in rows if b.rect[0] > 0]

    def draw_row(self, img, b: Button, n: int, state: str, progress: float, lines: list):
        x, y, w, h = b.rect
        border = {"targeted": J.CYAN, "confirming": J.CONFIRM}.get(state, J.RULE)
        J.panel(img, x, y, w, h, 0.92 if state == "normal" else 0.97, J.SURFACE_2 if state == "normal" else J.SURFACE,
                border=border)
        if state != "normal":
            cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), border, 2)
        numc = J.CYAN if state == "targeted" else (J.CONFIRM if state == "confirming" else J.TEXT_2)
        cy = y + (28 if b.kind == "problem" else 20)
        cv2.circle(img, (x + 20, cy), 12, numc, 1, cv2.LINE_AA)
        J.text(img, str(n), x + 20, cy + 5, 13, numc, "mono", anchor="c")
        right = 0
        if b.tag:
            tw = int(J.measure(b.tag, 10)) + 14
            cv2.rectangle(img, (x + w - tw - 10, cy - 10), (x + w - 10, cy + 8), J.ERROR, 1)
            J.text(img, b.tag, x + w - 10 - tw // 2, cy + 3, 10, J.ERROR, "semi", anchor="c", track=0.8)
            right = tw + 16
        tc = J.TEXT if state != "normal" or b.open else J.TEXT
        if b.kind == "problem":
            J.text(img, J.fit(b.label, w - 56 - right, 15), x + 44, y + 25, 15, tc, "semi")
            J.text(img, J.fit(b.sub, w - 56, 12), x + 44, y + 44, 12, J.TEXT_2)
        else:
            J.text(img, J.fit(b.label, w - 80, 14), x + 44, y + 25, 14, tc, "semi")
            mark = "-" if b.open else "+"
            J.text(img, mark, x + w - 18, y + 26, 18, J.CYAN if b.open else J.TEXT_2, "mono", anchor="c")
            for k, ln in enumerate(lines):
                J.text(img, ln, x + 44, y + 44 + 18 * k + 12, 13, J.TEXT_2)
        if state == "confirming" and progress > 0:
            cv2.rectangle(img, (x + 2, y + h - 4), (x + 2 + int((w - 4) * min(1, progress)), y + h - 2), J.CONFIRM, -1)

    def draw_launcher(self, img, hover, progress):
        cx, cy = self.launcher["c"]
        lay = self.launcher_layout()
        J.blit_rgba(img, J.mark(28), cx - 14, cy - 58, 0.9)
        J.label(img, "Launcher", cx, cy - 16, J.TEXT_2, anchor="c")
        for b in lay:
            st = "normal"
            if b.key == hover:
                st = "confirming" if progress > 0 else "targeted"
            self.tile(img, b, st, progress)

    def context_rows(self, width):
        """The left context card: guidance, then the one-shot identification result."""
        s = self.s
        rows = []
        head = {
            "READY": "Open palm opens the launcher. Show the V sign to talk, close your hand to run it.",
            "FRAMING": "Hold the object inside the square. Pinch Snap now to capture early.",
            "IDENTIFYING": "Identifying the snapshot on this laptop.",
            "ASK_MODEL": "I can't tell the exact model. Show the V sign (or press V), say the model, close your hand.",
            "LISTENING": "Listening. Close your hand (or press V) to run it. Open palm cancels.",
            "THINKING": "Got it, one moment.",
            "SEARCHING": f"Searching the web for: {s.query}",
            "DONE": ("Here's what the web found. Pinch Open result, Displays or Glass (Universal Display), or Done."
                     if self.is_phone() else "Here's what the web found. Pinch Open result, Fix steps, or Done."),
            "PROBLEMS": "Which problem does it have? Point at one and pinch." + (
                " Know the model? Show the V sign and say it." if vision.needs_model(s.result or {}) and not s.model_text
                else ""),
            "FIXING": "Finding the usual fixes for that problem on this laptop.",
            "FIXES": "Pinch a step to open it. Find part or Search web looks it up (text only).",
        }.get(s.mode, "")
        if s.locked:
            head = "Locked. Press U to unlock."
        rows.append(("label", "JAMES", J.CYAN if not s.locked else J.LOCKED))
        rows += [("head", ln) for ln in J.wrap(head, width, 16)]
        r = s.result
        if r:
            rows.append(("gap",))
            rows.append(("label", "Object identifier", J.TEXT_2))
            rows += [("title", ln) for ln in J.wrap(str(r.get("object", "unknown")).capitalize(), width, 22)[:2]]
            when = f"Identified on request · {s.identified_at}" + (f" · {s.took_s:.1f} s" if s.took_s else "")
            rows.append(("meta", when))
            name = s.model_text or " ".join(x for x in (r.get("brand"), r.get("model")) if x)
            if name and not (vision.needs_model(r) and not s.model_text):
                rows.append(("kv", "Model", name, J.TEXT))
            else:
                rows.append(("kv", "Model", "not sure; pinch Say model", J.CONFIRM))
            prob = r.get("problem", "")
            ok = "no visible" in prob.lower()
            for i, ln in enumerate(J.wrap(prob or "not stated", width - 110, 14)):
                rows.append(("kv", "Seen" if i == 0 else "", ln, J.SUCCESS if ok else J.ERROR))
            if s.choice is not None and s.choice < len(s.problems):
                for i, ln in enumerate(J.wrap(s.problems[s.choice]["title"], width - 110, 14)):
                    rows.append(("kv", "Chosen" if i == 0 else "", ln, J.CONFIRM))
            part = (s.fix or {}).get("part_needed")
            if part:
                for i, ln in enumerate(J.wrap(part, width - 110, 14)):
                    rows.append(("kv", "Part" if i == 0 else "", ln, J.CYAN_SOFT))
        if s.recommendation:
            rows.append(("gap",))
            for i, ln in enumerate(J.wrap(s.recommendation, width - 110, 14)):
                rows.append(("kv", "Buy" if i == 0 else "", ln, J.SUCCESS))
        for i, res in enumerate(s.results[:3], 1):
            rows.append(("meta", J.fit(f"{i}. {res['title']}  ·  {res['site']}", width, 12)))
        if s.heard:
            rows.append(("gap",))
            rows.append(("kv", "Heard", J.fit(s.heard, width - 110, 14), J.CYAN_SOFT))
        return rows

    ROW_H = {"label": 22, "head": 22, "title": 28, "meta": 18, "kv": 20, "gap": 8}

    def draw_card(self, c, rows, w, max_h):
        need = J.S4 + sum(self.ROW_H[r[0]] for r in rows) + J.S3
        h = min(max_h, need)
        J.panel(c, 0, 0, w, h, 0.86, accent=J.CYAN if self.s.result else J.TEAL)
        yy, tx = J.S4, J.S4
        for r in rows:
            rh = self.ROW_H[r[0]]
            if yy + rh > h - 4:
                J.text(c, "...", tx, yy + 12, 13, J.MUTED)
                break
            k = r[0]
            if k == "label":
                J.label(c, r[1], tx, yy + 14, r[2])
            elif k == "head":
                J.text(c, r[1], tx, yy + 16, 16, J.TEXT)
            elif k == "title":
                J.text(c, r[1], tx, yy + 22, 22, J.TEXT, "semi")
            elif k == "meta":
                J.text(c, r[1], tx, yy + 13, 12, J.TEXT_2)
            elif k == "kv":
                if r[1]:
                    J.text(c, r[1].upper(), tx, yy + 14, 10, J.MUTED, "semi", track=0.8)
                J.text(c, r[2], tx + 110, yy + 15, 14, r[3])
            yy += rh
        return h

    # ---------- voice context window (bottom centre) ----------
    def draw_voice(self, img):
        v = self.voice
        if not v:
            return
        if v.get("until") and time.time() > v["until"]:
            self.voice = None
            return
        if self.s.mode in ("FRAMING", "IDENTIFYING") or self.enrolling:   # the square / enrolment own this slot
            return
        H, W = img.shape[:2]
        # top, beside the context card and clear of the task actions: hands are usually lower in the frame
        left = J.S4 + min(460, max(360, int(W * 0.33))) + J.S4 if not self.launcher else J.S4
        if self.s.thumb is not None and self.s.mode not in ("READY", "FRAMING") and not self.launcher:
            left += 112 + J.S2                                        # keep the snapshot visible
        w = min(VOICE_W, W - left - DOCK_W - 2 * J.S4)
        iw = w - 2 * J.S4
        state = v["state"]
        rows = []                                                    # (kind, payload)
        if state == "typing":
            rows.append(("hint", "Typing a command  ·  Enter runs it  ·  Esc cancels"))
        if state == "listening":
            rows.append(("meter",))
            hint = ("Close your hand to run it  ·  open palm cancels" if v.get("via") == "gesture"
                    else "Close your hand, press V or pinch Stop to run it  ·  Esc cancels")
            rows.append(("hint", hint))
        if v.get("heard"):
            for k, ln in enumerate(J.wrap(f'"{v["heard"]}"', iw - 70, 15)[:2]):
                rows.append(("heard", ("Typed" if v.get("via") == "typed" else "Heard") if k == 0 else "", ln))
        elif state == "transcribing":
            rows.append(("hint", "Turning speech into text on this laptop"))
        for label, status, detail in v.get("steps", [])[-4:]:
            rows.append(("step", label, status, detail))
        if v.get("said") and state != "listening":
            lines = J.wrap(v["said"], iw - 70, 14)
            if len(lines) > 3:
                lines = lines[:2] + [J.fit(lines[2] + " ...", iw - 70, 14)]
            for k, ln in enumerate(lines):
                rows.append(("said", "James" if k == 0 else "", ln))
        hmap = {"meter": 22, "hint": 20, "heard": 22, "step": 38, "said": 20}
        h = J.S3 + 24 + sum(hmap[r[0]] for r in rows) + J.S2
        x, y = left, 52 + J.S4
        if self._voice_top:                                          # below the problems / fix steps panel
            y = max(y, min(self._voice_top, H - 30 - J.S3 - h))
        accent = {"listening": J.CYAN, "transcribing": J.CYAN_SOFT, "cancelled": J.MUTED}.get(state, J.TEAL)
        J.panel(img, x, y, w, h, 0.9, accent=accent)
        tx, yy = x + J.S4, y + J.S3
        if state == "listening":
            on = int(time.monotonic() * 2) % 2 == 0
            cv2.circle(img, (tx + 4, yy + 9), 5, J.ERROR if on else J.MUTED, -1, cv2.LINE_AA)
            J.label(img, "Voice  ·  listening", tx + 16, yy + 13, J.CYAN)
            J.text(img, f"{time.monotonic() - v['t0']:.1f} s", x + w - J.S4, yy + 13, 12, J.TEXT_2, "mono", anchor="r")
        else:
            words = {"transcribing": "Voice  ·  transcribing", "cancelled": "Voice  ·  cancelled", "done": "Voice",
                     "typing": "Typed command"}
            J.label(img, words.get(state, "Voice"), tx, yy + 13, J.CYAN_SOFT if state == "transcribing" else J.TEXT_2)
            if v.get("conf") is not None and v.get("heard"):
                J.text(img, f"heard {v['conf']:.2f}", x + w - J.S4, yy + 13, 11, J.MUTED, "mono", anchor="r")
        yy += 24
        for r in rows:
            k = r[0]
            if k == "meter":
                lvl = v["level"] = 0.6 * v.get("level", 0.0) + 0.4 * float(getattr(self.rec, "level", 0.0))
                n = 32
                for b in range(n):
                    bx = tx + b * (iw // n)
                    on = b / n < lvl
                    cv2.rectangle(img, (bx, yy + 6), (bx + iw // n - 3, yy + 14), J.CYAN if on else J.RULE, -1)
            elif k == "hint":
                J.text(img, r[1], tx, yy + 14, 12, J.TEXT_2)
            elif k == "heard":
                if r[1]:
                    J.text(img, r[1].upper(), tx, yy + 15, 10, J.MUTED, "semi", track=0.8)
                J.text(img, r[2], tx + 70, yy + 16, 15, J.TEXT)
            elif k == "said":
                if r[1]:
                    J.text(img, r[1].upper(), tx, yy + 14, 10, J.CYAN, "semi", track=0.8)
                J.text(img, r[2], tx + 70, yy + 15, 14, J.CYAN_SOFT)
            elif k == "step":
                _, label, status, detail = r
                col = {"ok": J.SUCCESS, "fail": J.ERROR}.get(status, J.CYAN)
                cx, cy = tx + 7, yy + 12
                if status == "ok":
                    cv2.line(img, (cx - 5, cy), (cx - 1, cy + 4), col, 2, cv2.LINE_AA)
                    cv2.line(img, (cx - 1, cy + 4), (cx + 6, cy - 5), col, 2, cv2.LINE_AA)
                elif status == "fail":
                    cv2.line(img, (cx - 5, cy - 5), (cx + 5, cy + 5), col, 2, cv2.LINE_AA)
                    cv2.line(img, (cx - 5, cy + 5), (cx + 5, cy - 5), col, 2, cv2.LINE_AA)
                else:
                    a = int(time.monotonic() * 360) % 360
                    cv2.ellipse(img, (cx, cy), (6, 6), a, 0, 270, col, 2, cv2.LINE_AA)
                J.text(img, J.fit(label, iw - 24, 14, "semi"), tx + 22, yy + 16, 14, J.TEXT, "semi")
                if detail:
                    J.text(img, J.fit(detail, iw - 24, 12), tx + 22, yy + 32, 12, J.TEXT_2)
            yy += hmap[k]

    def draw_enroll(self, img, lens):
        """K: enrolling the owner ring. Progress, and where to look."""
        H, W = img.shape[:2]
        p = lens.enroll or 0.0
        x = J.S4 + min(460, max(360, int(W * 0.33))) + J.S4 if not self.launcher else J.S4
        w = min(VOICE_W, W - x - DOCK_W - 2 * J.S4)
        lines = J.wrap("Hold up the hand with your ring, fingers spread, the ring facing the camera. "
                       "Keep other hands out of view.", w - 2 * J.S4, 13)[:3]
        h = 40 + 18 * len(lines) + 20
        y = 52 + J.S4
        J.panel(img, x, y, w, h, 0.92, accent=J.CYAN)
        J.label(img, "Owner ring  ·  enrolling", x + J.S4, y + 26, J.CYAN)
        J.text(img, f"{p * 100:.0f}%", x + w - J.S4, y + 26, 12, J.TEXT_2, "mono", anchor="r")
        for i, ln in enumerate(lines):
            J.text(img, ln, x + J.S4, y + 48 + 18 * i, 13, J.TEXT)
        by = y + h - 16
        cv2.rectangle(img, (x + J.S4, by), (x + w - J.S4, by + 5), J.RULE, -1)
        cv2.rectangle(img, (x + J.S4, by), (x + J.S4 + int((w - 2 * J.S4) * p), by + 5), J.CYAN, -1)
        cands = ([lens.hand] if lens.hand else []) + list(getattr(lens, "others", []) or [])
        if cands:
            big = max(cands, key=lambda hd: np.ptp(np.array(hd)[:, 0]) * np.ptp(np.array(hd)[:, 1]))   # the one enrolled
            a, b = np.array(big[13], float), np.array(big[14], float)
            c = tuple(int(v) for v in a + (b - a) * 0.36)
            cv2.circle(img, c, 14, J.CYAN, 2, cv2.LINE_AA)                     # where the ring is sampled

    # ---------- queue ----------
    def drain(self):
        while not self.q.empty():
            kind, *a = self.q.get()
            if kind == "identified":
                if self.s.mode != "IDENTIFYING":           # cancelled meanwhile: drop the late result
                    self.write("IDENTIFIED (ignored: task was cancelled)"); continue
                r = a[0]
                self.s.result = r
                self.s.took_s = time.time() - self.s.t_identify
                self.s.identified_at = time.strftime("%H:%M")
                self.write(f"IDENTIFIED in {self.s.took_s:.1f}s {r}")
                self.event(f"Identified: {r.get('object', 'object')}")
                if r.get("model") and not vision.needs_model(r):
                    self.s.model_text = f"{r.get('brand') or ''} {r['model']}".strip()
                self.s.problems = r.get("problems") or PR.merge(str(r.get("object") or ""), r.get("common_problems"),
                                                                r.get("problem"))
                self.s.mode = "PROBLEMS"
                self.write("PROBLEMS " + " | ".join(f"{p['title']}{' (seen)' if p['seen'] else ''} [{p['source']}]"
                                                     for p in self.s.problems))
                self.speak(T.identified(str(r.get("object") or "object"), str(r.get("problem") or ""), self.s.problems,
                                        self.s.model_text), interrupt=True)
            elif kind == "model_plan":
                i, steps, how, secs, said, heard = a
                if self.stale_owner():
                    self.write(f"IGNORED model plan for {heard!r}: another hand has control now")
                    self.set_step(i, "fail", "not run: control passed to another hand")
                    continue
                steps, dropped = C.guard(heard, steps)
                steps, off_site = C.ungrounded(heard, steps)
                dropped += off_site
                for why_not in dropped:
                    self.write(f"GUARD {why_not}")
                self.write(f"PLAN(model {secs:.1f}s, {how}) " + (" | ".join(st.describe() for st in steps) or "no steps")
                           + (f" SAY {said!r}" if said else ""))
                if steps:
                    self.set_step(i, "ok", f"{len(steps)} step{'s' if len(steps) > 1 else ''} in {secs:.1f} s"
                                  + ("; the local model failed, so only the simple part" if "failed" in how else ""))
                    self.run_steps(steps, "(model, resolved)")
                    self.report_leftovers(heard, steps)
                elif off_site:                                      # never speak a reply that describes the dropped plan
                    self.set_step(i, "fail", off_site[0].split("': ", 1)[1] + ", so nothing was run")
                    self.report_leftovers(heard, [])
                elif said:                                          # conversation, not a task: JAMES answers
                    self.set_step(i, "ok", "a reply, no task")
                    self.speak(said, interrupt=True)
                else:
                    self.set_step(i, "fail", "no clear request found; try saying it shorter")
                    self.flash("I couldn't turn that into steps. Try it in shorter pieces.", AMBER, 4)
            elif kind == "fix":
                i, fix = a
                if self.s.mode != "FIXING" or self.s.choice != i:    # cancelled or another problem chosen meanwhile
                    self.write(f"FIX (ignored: problem {i + 1} no longer chosen)"); continue
                self.s.fix, self.s.fix_took_s = fix, time.time() - self.s.t_fix
                self.s.mode, self.s.expanded = "FIXES", (0 if fix.get("steps") else None)
                self.fix_pos = 0
                self.speak(T.fix_intro(self.s.problems[i], fix), interrupt=True)
                self.write(f"FIX {len(fix.get('steps', []))} steps from {fix.get('source')} in {self.s.fix_took_s:.1f}s")
                if not fix.get("steps"):
                    self.flash("No steps for that one here. Pinch Search web.", AMBER, 5)
            elif kind == "heard":
                if self.s.mode != "THINKING":
                    continue
                if self.stale_owner():
                    self.s.mode = getattr(self, "_before_listen", "READY")
                    self.write(f"IGNORED heard {a[0]!r}: another hand has control now")
                    continue
                self.on_heard(*a)
            elif kind == "results":
                if self.s.mode != "SEARCHING":
                    continue
                self.s.results, self.s.recommendation = a
                self.s.mode = "DONE"
                n = len(a[0])
                self.write(f"RESULTS {n} | {a[1]}")
                self.event(f"Search returned {n} result{'' if n == 1 else 's'}")
                if not a[0]:
                    self.flash("Web search unavailable here. Pinch Open result to search in the browser.", AMBER, 5)
            elif kind == "step":
                self.set_step(*a)
                label = self.voice["steps"][a[0]][0] if self.voice and 0 <= a[0] < len(self.voice["steps"]) else ""
                if a[1] == "ok" and label.startswith("Play"):
                    self.event(label[:40])
                if a[1] == "ok" and label.startswith("Save"):
                    self.speak(f"Done. {a[2][0].upper() + a[2][1:]}." if a[2] else "Done.")
            elif kind == "log":
                self.write(a[0])
            elif kind == "error":
                self.s.mode = "READY"
                if self.voice and self.voice.get("state") == "transcribing":
                    self.voice.update(state="done", until=time.time() + VOICE_SHOW_S)
                    self.voice_step(a[0], "fail")
                self.flash(a[0], RED, 5)

    # ---------- drawing: frame ----------
    def draw(self, img, lens, hover_key, progress):
        H, W = img.shape[:2]
        s = self.s
        if s.locked:
            img[:] = cv2.addWeighted(img, 0.45, img, 0, 0)

        # capture square (world stage)
        if s.mode in ("FRAMING", "IDENTIFYING"):
            x, y, side = self.square(W, H)
            col = J.CYAN if s.mode == "FRAMING" else J.CYAN_SOFT
            J.brackets(img, x, y, side, side, col, L=side // 6, thick=2)
            if s.mode == "FRAMING":
                left = max(0.0, s.framing_until - time.time())
                J.panel(img, x + side // 2 - 44, y + side // 2 - 44, 88, 88, 0.88)
                J.text(img, str(int(left) + 1) if left > 0 else "0", x + side // 2, y + side // 2 + 20, 54, J.TEXT, "semi",
                       anchor="c")
                cap = "Place the object inside the square"
            else:
                cap = "Identifying. Only this square is analysed, on this laptop."
            cw = int(J.measure(cap, 14)) + 32
            J.panel(img, x + (side - cw) // 2, y - 44, cw, 30, 0.9)
            J.text(img, cap, x + side // 2, y - 24, 14, J.TEXT, anchor="c")

        # task actions (right): only while a task runs; they own the pinch instead of the launcher
        acts = self.task_actions() if not s.locked else []
        for i, b in enumerate(acts):
            b.rect = (W - DOCK_W - J.S4, 52 + J.S4 + i * (DOCK_H + J.S2), DOCK_W, DOCK_H)
            self.tile(img, b, ("confirming" if progress > 0 else "targeted") if b.key == hover_key else "normal", progress)
        if acts:
            J.label(img, "Task actions", W - DOCK_W - J.S4, 52 + J.S4 + len(acts) * (DOCK_H + J.S2) + 12, J.MUTED)
        self._dock = acts + self.draw_choices(img, hover_key, progress)

        # context card (left) and snapshot; hidden while the launcher is open so nothing overlaps
        if not self.launcher:
            cw = min(460, max(360, int(W * 0.33)))
            max_h = H - 52 - J.S4 - 30 - 150
            rows = self.context_rows(cw - 2 * J.S4)
            key = (cw, max_h, s.mode, s.locked, tuple(tuple(r) for r in rows))
            self.card_cache.draw(img, key, J.S4, 52 + J.S4, cw, max_h, lambda c: self.draw_card(c, rows, cw, max_h))
            if s.thumb is not None and s.mode not in ("READY", "FRAMING"):
                th = cv2.resize(cv2.flip(s.thumb, 1) if config.MIRROR else s.thumb, (112, 112))
                tx, ty = J.S4 + cw + J.S2, 52 + J.S4
                img[ty:ty + 112, tx:tx + 112] = th
                cv2.rectangle(img, (tx, ty), (tx + 111, ty + 111), J.RULE, 1)
                J.panel(img, tx, ty + 112, 112, 22, 0.9)
                J.text(img, "only this was sent", tx + 56, ty + 128, 11, J.TEXT_2, anchor="c")
        else:
            self.draw_launcher(img, hover_key, progress)

        # other hands: shown faded and labelled, never used for input
        owner_on = lens is not None and getattr(lens, "owner", "off") != "off"
        for kt in (getattr(lens, "keys", None) or []) if lens is not None else []:    # the owner key, where it was seen
            cv2.polylines(img, [np.round(kt.corners).astype(np.int32)], True, J.SUCCESS, 2, cv2.LINE_AA)
            J.text(img, "owner key", int(kt.center[0]), int(kt.corners[:, 1].max()) + 16, 11, J.SUCCESS, "semi", anchor="c")
        for oh in (getattr(lens, "others", None) or []) if lens is not None else []:
            J.skeleton(img, oh, HAND_LINKS, J.RULE)
            J.text(img, "not owner" if owner_on else "ignored", oh[0][0], oh[0][1] + 18, 11, J.MUTED, "semi", anchor="c")
        # hand + spatial cursor (on top)
        if lens is not None and lens.hand:
            palm_live = lens.open_palm and not self.busy and not s.locked
            talk_live = getattr(lens, "peace", False) and self.voice_allowed()
            J.skeleton(img, lens.hand, HAND_LINKS, J.CYAN_SOFT if (palm_live or talk_live or self.rec.active) else J.TEXT_2)
        tip = lens.fingertip if lens is not None else None
        if self.selected and time.monotonic() < self.selected[1]:
            J.cursor(img, tip or self.selected[0], "SELECTED")
        elif tip:
            if s.locked:
                cs = "LOCKED"
            elif hover_key and progress > 0:
                cs = "CONFIRMING"
            elif hover_key:
                cs = "TARGETED"
            elif self.launcher or acts:
                cs = "TARGETABLE"
            else:
                cs = "TRACKING"
            J.cursor(img, tip, cs, progress)

        # perception + event stream (bottom left, compact, only when there is something real)
        y = H - 30 - J.S3
        if lens is not None and not lens.hand and getattr(lens, "others", None) and getattr(lens, "owner", "") == "searching":
            g = (wrong_key_hint(lens) or
                 "Looking for the owner key. Show the key to the camera, or press X so any hand can drive."
                 if getattr(lens, "owner_kind", "") == "key" else
                 "Looking for the ring hand. Show the ring, or press X so any hand can drive.")
            J.panel(img, J.S4, y - 26, int(J.measure(g, 13)) + 108, 26, 0.86)
            J.text(img, "GESTURE", J.S4 + 10, y - 9, 10, J.MUTED, "semi", track=0.8)
            J.text(img, g, J.S4 + 84, y - 8, 13, J.CONFIRM)
            y -= 26 + J.S2
        if lens is not None and lens.hand:
            if self.rec.active:
                g = ("Fist, stopping" if getattr(lens, "fist", False) else
                     "Listening  ·  close your hand to run" if self.voice and self.voice.get("via") == "gesture" else "Listening")
            elif getattr(lens, "peace", False):
                g = "V sign, hold to talk" if self.voice_allowed() else "V sign (talk is off right now)"
            elif lens.pinching:
                g = "Pinch"
            elif lens.open_palm:
                g = ("Open palm, hold to open the launcher" if not self.busy and not self.launcher and not s.locked
                     else "Open palm (launcher is off during a task)" if self.busy else "Open palm")
            else:
                g = "Index point"
            if hover_key:
                g += "  ·  targeting"
            J.panel(img, J.S4, y - 26, int(J.measure(g, 13)) + 108, 26, 0.86)
            J.text(img, "GESTURE", J.S4 + 10, y - 9, 10, J.MUTED, "semi", track=0.8)
            J.text(img, g, J.S4 + 84, y - 8, 13, J.TEXT)
            y -= 26 + J.S2
        recent = [e for e in self.events if time.monotonic() - e[2] < 20]
        for clock, t, _ in recent[:3]:
            J.text(img, clock, J.S4 + 2, y - 4, 11, J.MUTED, "mono")
            J.text(img, t, J.S4 + 48, y - 4, 12, J.TEXT_2)
            y -= 18

        # header: identity, mode, real status
        x = self.header.draw(img)
        px = J.pill(img, "Locked" if s.locked else MODE_LABEL.get(s.mode, s.mode.title()), x, 31,
                    J.LOCKED if s.locked else MODE_COLOR.get(s.mode, J.TEXT_2))
        # talk-back switch (pinch it, click it, or press M); remembered in data/prefs.json
        vstate = ("confirming" if progress > 0 else "targeted") if hover_key == "voice_toggle" else "normal"
        on = self.talker.enabled and not self.talker.muted
        vrect = J.switch(img, px + J.S4, 26, on, "VOICE" if self.talker.enabled else "VOICE N/A", vstate, progress)
        self._dock = [b for b in self._dock if b.key != "voice_toggle"] + [Button("voice_toggle", "Voice", rect=vrect)]
        st = self.timer.stats()
        items = [(J.SUCCESS, f"VISION {config.CHAT_MODEL} LOCAL") if self.model_ok else (J.ERROR, "VISION OFFLINE"),
                 (None, "WEB TEXT ONLY")]
        own = getattr(lens, "owner", "off") if lens is not None else "off"
        kind = getattr(lens, "owner_kind", "ring") if lens is not None else "ring"
        if own == "owner":
            items.append((J.SUCCESS, "KEY OWNER" if kind == "key" else "RING OWNER"))
        elif own == "searching":
            items.append((J.CONFIRM, "SHOW OWNER KEY" if kind == "key" else "LOOKING FOR RING HAND"))
        elif lens is not None and getattr(lens, "owner_paused", False):
            items.append((J.CONFIRM, "KEY OFF, ANY HAND"))
        if self.eyes_on:
            items.append((J.CYAN, "EYES ON, NOTHING STORED"))
        if st["fps"]:
            items.append((None, f"{st['fps']:.1f} FPS"))
        items.append((None, time.strftime("%H:%M")))
        J.status_right(img, items, y=25)
        J.text(img, "Identify sends only the square to the local model. Web search sends text only.",
               W - J.S4, 43, 10, J.MUTED, "mono", anchor="r")

        if self.show_telemetry:
            ty = 52 + J.S4 + (len(acts) * (DOCK_H + J.S2) + 24 if acts else 0)
            J.telemetry_card(img, W - 196 - J.S4, ty, self.timer)
        self.enrolling = lens is not None and getattr(lens, "enroll", None) is not None
        self.draw_voice(img)
        if self.enrolling:
            self.draw_enroll(img, lens)
        if s.msg and time.time() < s.msg[2]:
            J.toast(img, s.msg[0], s.msg[1], y_bottom=H - 30 - J.S4)
        J.hints(img, "Open palm = launcher  |  V sign = talk, fist = run  |  point + pinch = choose  |  I identify  |  "
                     "V talk  |  / type  |  O open  |  L launcher  |  " + ("X key off/on" if getattr(lens, "owner_kind", "") == "key"
                     else "K enrol ring  |  X forget ring") + "  |  M mute  |  Esc cancel  |  U lock  |  "
                     "T telemetry  |  H hints  |  Q quit", self.show_hints)
        return img


NUMBER_WORDS = {"one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3, "four": 4, "fourth": 4,
                "1": 1, "2": 2, "3": 3, "4": 4}


def pick_number(txt: str) -> int | None:
    """'problem two', 'number 3', 'the first one', 'option 4' -> 2, 3, 1, 4. Plain model names -> None."""
    t = (txt or "").lower().strip(" .!?")
    t = re.sub(r"^(?:(?:ok|okay|please|now|let's|lets)\s+)+|\s+please$", "", re.sub(r"[,]", " ", T.drop_name(t))).strip()
    m = re.fullmatch(r"(?:(?:fix|open|show(?:\s+me)?|select|choose|pick|go\s+to|tell\s+me\s+about|explain|look\s+at|"
                     r"let's\s+(?:do|fix|look\s+at)|i\s+(?:have|want|choose)|it's)\s+)?(?:the\s+)?"
                     r"(?:(?:problem|number|option|issue)\s+(?:number\s+)?)?(one|two|three|four|first|second|third|fourth|[1-4])"
                     r"(?:\s+one)?(?:\s+(?:problem|option|issue))?", t)
    return NUMBER_WORDS[m.group(1)] if m else None


def wrong_key_hint(lens) -> str | None:
    """Key mode, no key seen, but another ArUco marker is: say which one it is (log 2026-09-26 00:31: the
    pump tag P-3, id 3, was held up as the key)."""
    if getattr(lens, "owner_kind", "") != "key" or getattr(lens, "keys", None) or not getattr(lens, "tags", None):
        return None
    t = lens.tags[0]
    what = f"asset tag {t.asset_id}" if t.asset_id else "a marker"
    return (f"That is {what} (id {t.id}), not the owner key. Show key id {config.OWNER_KEY_ID} "
            f"(assets/owner_key_{config.OWNER_KEY_ID}.png).")


def lens_owner_enrolled() -> bool:
    import owner as OW
    return OW.OwnerFilter.load(config.OWNER_RING).enrolled


def voice_gestures(j: "James", view, now: float, g: dict):
    """Push-to-talk by gesture. V sign held -> listen. Fist -> stop and run. Open palm -> cancel.
    Hand gone for VOICE_LOST_S, or VOICE_MAX_S reached -> stop and run what was said."""
    def held(key, cond, secs):
        if not cond:
            g[key] = None
            return False
        g[key] = g[key] or now
        return now - g[key] >= secs
    if not j.rec.active:
        g["fist"] = g["cancel"] = g["lost"] = None
        if held("peace", bool(view.hand) and view.peace and j.voice_allowed(now), PEACE_HOLD_S):
            g["peace"] = None
            j.start_listen("gesture")
        return
    g["peace"] = None
    if j.voice and now - j.voice["t0"] >= VOICE_MAX_S:
        j.stop_listen(run=True)
        return
    if held("fist", bool(view.hand) and view.fist, FIST_HOLD_S):
        j.stop_listen(run=True)
    elif held("cancel", bool(view.hand) and view.open_palm, CANCEL_HOLD_S):
        j.stop_listen(run=False)
    elif held("lost", not view.hand and bool(j.voice) and j.voice.get("via") == "gesture", VOICE_LOST_S):
        j.stop_listen(run=True, departed=True)                     # started by V and the hand left: confirm, then run


def main():
    from agents.lens import Lens
    from app import mirror_result
    config.DATA.mkdir(exist_ok=True)
    print("JAMES simple demo | resolving the launcher apps (first run caches their icons)...")
    j = James()
    from tools.build_id import build_id
    bid, nfiles = build_id()
    j.write(f"BUILD {bid} ({nfiles} files) pack {config.PACK.label} snapshot {config.PACK.snapshot}")
    print(f"  build {bid} ({nfiles} files)")
    print(f"  model {config.CHAT_MODEL}: {'OK' if j.model_ok else 'NOT RUNNING (start: ollama serve)'}")
    print("  launcher: " + ", ".join(f"{a.label} ({a.how}{', icon' if a.icon is not None else ''})" for a in j.apps))
    threading.Thread(target=j.asr.load, daemon=True).start()
    cap = cv2.VideoCapture(config.CAMERA_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_H)
    if not cap.isOpened():
        sys.exit("Camera not available. Allow camera access for Terminal in System Settings > Privacy & Security.")
    lens = Lens()
    eyes = None
    cv2.namedWindow("JAMES", cv2.WINDOW_NORMAL)

    def on_mouse(event, mx, my, flags, param):          # a click on the voice switch toggles it
        if event == cv2.EVENT_LBUTTONUP:
            b = next((b for b in j._dock if b.key == "voice_toggle"), None)
            if b and b.hit((mx, my)):
                j.press("voice_toggle")
    cv2.setMouseCallback("JAMES", on_mouse)
    t0, pinch_start, fired, cool, hover = time.monotonic(), None, False, 0.0, None
    palm_since = None
    g = {"peace": None, "fist": None, "cancel": None, "lost": None}     # gesture hold timers (monotonic)
    mode = getattr(config, "OWNER_MODE", "ring")
    if mode == "key":
        print(f"  owner      : KEY, ArUco 4x4 id {config.OWNER_KEY_ID} (print it: python tools/make_owner_key.py). X = any hand")
    elif mode == "ring":
        print(f"  owner ring: {'enrolled, only the ring hand drives JAMES' if lens_owner_enrolled() else 'not enrolled (press K)'}")
    else:
        print("  owner      : off, the largest hand drives")
    prev_owner = None
    j.speak(T.greeting(j.sir))
    print(f"  talkback   : {j.talker.describe()}; {'off' if j.talker.muted else 'on'} (switch at the top, or M)")
    tm = j.timer
    try:
        while True:
            tm.start()
            ok, raw = cap.read()
            if not ok:
                break
            tm.mark("capture")
            r = lens.process(raw, int((time.monotonic() - t0) * 1000))
            tm.mark("perception")
            j.drain()
            if j.s.mode == "FRAMING" and time.time() >= j.s.framing_until:
                j.capture(raw)
            frame, view = raw, r
            if config.MIRROR:
                frame, view = cv2.flip(raw, 1), mirror_result(r, raw.shape[1])
            tip = view.fingertip
            now = time.monotonic()
            W, H = frame.shape[1], frame.shape[0]
            # open palm -> launcher, ONLY when idle (no task, not locked, not just after a choice); time-based hold
            palm_ok = view.open_palm and j.palm_allowed(now)
            if palm_ok:
                palm_since = palm_since or now
                if now - palm_since >= PALM_HOLD_S:
                    c = np.mean([view.hand[i] for i in (0, 5, 9, 13, 17)], axis=0)
                    j.open_launcher(c, W, H)
                    palm_since = None
            else:
                palm_since = None
            voice_gestures(j, view, now, g)
            j.set_owner(view.owner, getattr(lens.owner, "epoch", None))
            if view.owner != prev_owner:                  # owner found / lost, for tuning from the log
                if prev_owner is not None:
                    j.write(f"OWNER {view.owner_kind} {prev_owner} -> {view.owner} (hands {len(view.others) + (1 if view.hand else 0)}, keys {len(view.keys)})")
                prev_owner = view.owner
            if view.enroll_msg:
                ok_, msg_ = view.enroll_msg
                j.flash(msg_, GREEN if ok_ else AMBER, 6)
                j.event("Owner ring enrolled" if ok_ else "Ring enrolment failed")
            if j.launcher:
                if j.busy:                              # a task started (e.g. key I): the task owns the screen
                    j.close_launcher()
                    hover = None
                else:
                    hover = j.launcher_hit(tip)
                    if hover:
                        j.launcher["last_hover"] = time.time()
                    elif time.time() - j.launcher["last_hover"] > LAUNCHER_IDLE_S:
                        j.close_launcher()
            else:
                hover = next((b.key for b in j._dock if b.hit(tip)), None)
            progress = 0.0
            if view.pinching and hover and now >= cool and not fired:
                pinch_start = pinch_start or now
                progress = (now - pinch_start) * 1000 / PINCH_MS
                if progress >= 1:
                    fired, cool = True, now + COOLDOWN_S
                    j.selected = (tip, now + 0.4)
                    if j.launcher:
                        j.close_launcher()              # choosing closes the launcher
                    j.palm_block_until = now + PALM_BLOCK_S
                    j.press(hover, raw)
            elif not view.pinching:
                pinch_start, fired = None, False
            out = j.draw(frame, view, hover, progress)
            if j.eyes_on and eyes is not None:
                from agents.eyes import draw as draw_eyes, mirror as mirror_eyes
                er = eyes.process(raw, int((time.monotonic() - t0) * 1000))
                er = mirror_eyes(er, W) if config.MIRROR else er
                draw_eyes(out, er)
                if er.gaze:
                    J.text(out, f"gaze {er.gaze}", W - J.S4, 76, 12, J.CYAN_SOFT, "mono", anchor="r")
            J.splash(out, time.monotonic() - t0)          # brand splash for the first ~1.6 s only
            tm.mark("render")
            cv2.imshow("JAMES", out)
            k = cv2.waitKey(1) & 0xFF
            tm.mark("display")
            tm.end()
            if k == 255:
                continue
            if j.typing is not None:                   # typing a command: every key goes into the text
                j.type_key(k)
                continue
            c = chr(k).lower()
            if c == "/":
                j.start_typing()
                continue
            if c == "q":
                break
            if c == "u":
                j.s.locked = not j.s.locked
                if j.launcher:
                    j.close_launcher()
                j.event("Locked" if j.s.locked else "Unlocked")
            elif c == "i":
                j.press("identify", raw)
            elif c == "v":
                j.press("talk")
            elif c == "o":
                j.open_top()
            elif c == "l":
                if j.launcher:
                    j.close_launcher()
                elif j.busy:
                    j.flash("Finish or cancel the current task first (Esc cancels).", AMBER)
                elif not j.s.locked:
                    j.open_launcher((W // 2, H // 2), W, H)
            elif c == "k":
                if getattr(lens.owner, "kind", "") == "key":
                    j.flash(f"Key mode: nothing to enrol. Show the owner key (id {config.OWNER_KEY_ID}) to the camera.", AMBER, 4)
                else:
                    lens.start_enroll(int((time.monotonic() - t0) * 1000))
                    j.event("Enrolling owner ring")
                    j.write("OWNER enrol start")
            elif c == "x":
                if getattr(lens.owner, "kind", "") == "key":
                    off = lens.toggle_pause()
                    lost_ms = int(time.monotonic() * 1000 - t0 * 1000 - lens.owner.lock["seen"]) \
                        if getattr(lens.owner, "lock", None) else -1
                    j.write(f"OWNER bypass {'on' if off else 'off'} (X) status={view.owner} "
                            f"hands={len(view.others) + (1 if view.hand else 0)} keys={len(view.keys)} "
                            f"owner_last_seen_ms={lost_ms}")
                    j.flash("Owner key off: any hand drives JAMES (the largest in view). X turns it back on." if off
                            else "Owner key on: only the hand wearing the key drives JAMES.", AMBER, 4)
                    j.event("Owner key off" if off else "Owner key on")
                    j.write(f"OWNER key {'paused' if off else 'required'}")
                else:
                    lens.forget_owner()
                    j.flash("Owner ring forgotten. Any hand drives JAMES again (the largest in view).", AMBER, 4)
                    j.event("Owner ring forgotten")
                    j.write("OWNER forgotten")
            elif c == "m":
                j.press("voice_toggle")
            elif c == "t":
                j.show_telemetry = not j.show_telemetry
            elif c == "h":
                j.show_hints = not j.show_hints
            elif c == "g":
                if eyes is None:
                    from agents.eyes import Eyes
                    eyes = Eyes()
                j.eyes_on = not j.eyes_on
            elif k == 27:
                if j.rec.active:
                    j.stop_listen(run=False)
                elif j.launcher:
                    j.close_launcher()
                elif j.busy:
                    j.end_task("Task cancelled")
    finally:
        lens.close(); cap.release(); cv2.destroyAllWindows()
        if eyes is not None:
            eyes.close()


if __name__ == "__main__":
    main()
