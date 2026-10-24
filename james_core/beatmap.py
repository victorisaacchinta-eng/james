"""CANTUS beat maps: where every block goes, for built-in songs and for audio files the player drops in.

A chart is a list of blocks: (time in seconds, column 0..3, row 0 top / 1 bottom, hand 0 left (red) / 1 right
(blue), direction U D L R or A for any). Charts are made by rules, not by a model, so the same song always
gives the same chart:

- built-in songs: one candidate per melody note, scored by length, beat and leap;
- dropped-in audio: onsets from spectral flux, tempo from the onset autocorrelation, onsets snapped to the
  half-beat grid; louder onsets are kept first;
- then the same placement for both: keep the strongest candidates at least `min_gap` apart, give each hand
  enough time between its own blocks, spread columns by pitch (or brightness), and alternate each hand's
  swings down / up so a swing always leaves the hand ready for the next one.

Audio files are decoded with whatever the machine has: stdlib for WAV, `afconvert` (built into macOS) or
ffmpeg for MP3 / M4A, or the optional `miniaudio` package. A finished map is cached by the file's fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

FORMAT = "cantus/1"
COLS, ROWS = 4, 2
DIRS = {"U": (0.0, -1.0), "D": (0.0, 1.0), "L": (-1.0, 0.0), "R": (1.0, 0.0), "A": None}
DIFFICULTY = {   # min_gap: any two blocks; hand_gap: one hand's own blocks; approach: seconds on screen
    "easy": {"min_gap": 0.95, "hand_gap": 1.5, "approach": 1.9, "window": 0.22},
    "normal": {"min_gap": 0.55, "hand_gap": 0.95, "approach": 1.5, "window": 0.18},
    "hard": {"min_gap": 0.4, "hand_gap": 0.62, "approach": 1.15, "window": 0.15},
}
AUDIO_EXT = (".mp3", ".m4a", ".wav", ".aac", ".flac", ".aif", ".aiff", ".ogg")


@dataclass(frozen=True)
class Block:
    t: float
    col: int
    row: int
    hand: int
    dir: str


@dataclass
class Chart:
    id: str
    title: str
    artist: str
    source: str                      # "builtin" | "import"
    duration: float
    bpm: float
    audio: str = ""                  # path to play; "" until rendered
    blocks: dict = field(default_factory=dict)   # difficulty -> [Block]
    note: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["format"] = FORMAT
        d["blocks"] = {k: [[b.t, b.col, b.row, b.hand, b.dir] for b in v] for k, v in self.blocks.items()}
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Chart":
        if d.get("format") != FORMAT:
            raise ValueError(f"not a {FORMAT} map")
        blocks = {k: [Block(float(t), int(c), int(r), int(h), str(di)) for t, c, r, h, di in v]
                  for k, v in d.get("blocks", {}).items()}
        return cls(d["id"], d["title"], d.get("artist", ""), d["source"], float(d["duration"]), float(d["bpm"]),
                   d.get("audio", ""), blocks, d.get("note", ""))


# ---------------- placement (shared by both sources) ----------------

def place(events: list[dict], difficulty: str = "normal", lead: float = 1.0) -> list[Block]:
    """events: [{t, strength 0..1, height 0..1}] -> playable blocks. Deterministic."""
    cfg = DIFFICULTY[difficulty]
    cands = sorted((e for e in events if e["t"] >= lead), key=lambda e: (-e["strength"], e["t"]))
    picked: list[dict] = []
    times: list[float] = []
    for e in cands:
        i = int(np.searchsorted(times, e["t"]))
        if (i > 0 and e["t"] - times[i - 1] < cfg["min_gap"]) or (i < len(times) and times[i] - e["t"] < cfg["min_gap"]):
            continue
        times.insert(i, e["t"])
        picked.insert(i, e)
    blocks: list[Block] = []
    last_t = [-9.0, -9.0]
    last_dir = ["U", "U"]
    count = [0, 0]
    for e in picked:
        col = min(COLS - 1, max(0, int(e["height"] * COLS)))
        hand = 0 if col < COLS // 2 else 1
        other = 1 - hand
        if e["t"] - last_t[hand] < cfg["hand_gap"] and e["t"] - last_t[other] >= e["t"] - last_t[hand]:
            hand, col = other, COLS - 1 - col                   # give it to the hand that has rested longer
        if e["t"] - last_t[hand] < cfg["min_gap"]:
            continue
        row = 0 if e["height"] >= 0.66 else 1
        gap = e["t"] - last_t[hand]
        count[hand] += 1
        if gap < cfg["hand_gap"]:
            d = "A"                                               # too soon for a full swing: any direction
        elif count[hand] % 5 == 0 and gap >= cfg["hand_gap"] * 1.3:
            d = "L" if hand == 0 else "R"                         # now and then an outward swing
        else:
            d = "U" if last_dir[hand] == "D" else "D"             # down, up, down: each swing sets up the next
        if d in ("U", "D"):
            last_dir[hand] = d
        elif d in ("L", "R"):
            last_dir[hand] = "U"                                  # after a side swing, the next goes down
        last_t[hand] = e["t"]
        blocks.append(Block(round(e["t"], 3), col, row, hand, d))
    return blocks


# ---------------- built-in songs ----------------

def events_from_notes(notes: list[tuple[float, float, int]], bpm: float) -> list[dict]:
    """Melody notes (start_s, dur_s, midi) -> candidates: long notes, notes on the beat and leaps score high."""
    if not notes:
        return []
    lo, hi = min(n[2] for n in notes), max(n[2] for n in notes)
    span = max(1, hi - lo)
    beat = 60.0 / bpm
    dmax = max(n[1] for n in notes)
    out, prev = [], None
    for s, d, m in notes:
        on_beat = abs(s / beat - round(s / beat)) < 0.05
        leap = 0.0 if prev is None else min(1.0, abs(m - prev) / 7.0)
        strength = 0.45 * (d / dmax) + 0.35 * on_beat + 0.2 * leap
        out.append({"t": s, "strength": round(strength, 4), "height": (m - lo) / (span + 1)})
        prev = m
    return out


def chart_for_song(song, audio_path: str = "") -> Chart:
    ev = events_from_notes(song.notes_seconds(), song.bpm)
    return Chart(song.id, song.title, song.composer, "builtin", song.seconds, song.bpm, audio_path,
                 {d: place(ev, d) for d in DIFFICULTY}, song.note)


# ---------------- audio analysis ----------------

def analyse(y: np.ndarray, sr: int) -> dict:
    """Onsets, tempo and beats of a mono signal. Returns {bpm, beats, events, duration}."""
    y = np.asarray(y, np.float32)
    n_fft, hop = 1024, 256
    if len(y) < n_fft * 4:
        return {"bpm": 120.0, "beats": [], "events": [], "duration": len(y) / sr}
    if float(np.sqrt(np.mean(y.astype(np.float64) ** 2))) < 1e-3:
        raise ValueError("this file is silent, so there is no beat to map")
    n = 1 + (len(y) - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr).astype(np.float32)
    flux = np.zeros(n, np.float32)
    centroid = np.zeros(n, np.float32)
    prev = None
    for a0 in range(0, n, 2048):                                    # in chunks: a long track stays small in memory
        a1 = min(n, a0 + 2048)
        idx = np.arange(n_fft)[None, :] + hop * np.arange(a0, a1)[:, None]
        mag = np.abs(np.fft.rfft(y[idx] * win, axis=1)).astype(np.float32)
        logm = np.log1p(10.0 * mag)
        both = logm if prev is None else np.vstack([prev, logm])
        d = np.maximum(0.0, np.diff(both, axis=0)).sum(axis=1)
        flux[a0 + (1 if prev is None else 0):a1] = d
        centroid[a0:a1] = (mag * freqs).sum(axis=1) / (mag.sum(axis=1) + 1e-9)
        prev = logm[-1:]
    env = flux / (flux.max() + 1e-9)
    env = np.convolve(env, np.ones(3) / 3, mode="same")
    fps = sr / hop

    # onsets: local peaks above a moving average
    w = max(3, int(0.4 * fps))
    local = np.convolve(env, np.ones(w) / w, mode="same")
    delta = 0.25 * float(env.std())
    peaks = []
    r = 3
    for i in range(r, len(env) - r):
        if env[i] >= env[i - r:i + r + 1].max() and env[i] > local[i] + delta and env[i] > 0.05:
            if not peaks or i - peaks[-1] >= int(0.07 * fps):
                peaks.append(i)

    # tempo: autocorrelation of the onset envelope, weighted towards 120 BPM
    e = env - env.mean()
    nfft = 1 << int(np.ceil(np.log2(2 * len(e))))
    spec = np.fft.rfft(e, nfft)
    ac = np.fft.irfft(spec * np.conj(spec), nfft)[: len(e)]
    lo_lag, hi_lag = int(fps * 60 / 200), int(fps * 60 / 60)
    best_lag, best_score = None, -1e9
    for L in range(max(1, lo_lag), min(hi_lag, len(ac) - 1)):
        bpm = 60.0 * fps / L
        wgt = np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
        sc = ac[L] * wgt
        if sc > best_score:
            best_lag, best_score = L, sc
    if best_lag is None:
        best_lag = int(fps * 0.5)
    L = float(best_lag)
    if 1 <= best_lag < len(ac) - 1:                                  # parabolic refinement
        a, b, c = ac[best_lag - 1], ac[best_lag], ac[best_lag + 1]
        den = a - 2 * b + c
        if den != 0:
            L += 0.5 * (a - c) / den
    bpm = 60.0 * fps / L
    phase = max(range(best_lag), key=lambda p: env[p::best_lag].sum())
    period = 60.0 / bpm
    dur = len(y) / sr
    first = phase / fps
    beats = list(np.arange(first, dur, period))

    # events: onsets snapped to the quarter-beat grid when close; brightness gives height
    centre = (n_fft / 2) / sr                                     # a frame's time is its centre, not its start
    on_t = np.array([p / fps + centre for p in peaks]) if peaks else np.array([])
    cents = np.array([centroid[p] for p in peaks]) if peaks else np.array([])
    if len(cents):
        c_lo, c_hi = np.percentile(cents, 5), np.percentile(cents, 95)
    events = []
    first += centre
    beats = [b + centre for b in beats]
    q = period / 4
    for k, (t, p) in enumerate(zip(on_t, peaks)):
        g = first + round((t - first) / q) * q
        snapped = abs(g - t) <= 0.035
        tt = g if snapped else t
        on_beat = snapped and abs((tt - first) / period - round((tt - first) / period)) < 0.01
        h = float(np.clip((cents[k] - c_lo) / max(1e-6, c_hi - c_lo), 0, 0.999))
        events.append({"t": round(float(tt), 3), "strength": round(float(env[p]) + 0.2 * on_beat, 4), "height": h})
    if len(events) < dur / 3:                                         # very few onsets: fall back to the beats
        events += [{"t": round(float(b), 3), "strength": 0.3, "height": (i % 4) / 4 + 0.1}
                   for i, b in enumerate(beats)]
    return {"bpm": round(bpm, 2), "beats": [round(float(b), 3) for b in beats], "events": events, "duration": dur}


# ---------------- decoding ----------------

def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
    elif sw == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float32) / (1 << 23)
    elif sw == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / (1 << 31)
    else:
        raise ValueError(f"unsupported WAV sample width {sw}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def resample(x: np.ndarray, sr: int, to: int) -> np.ndarray:
    if sr == to or len(x) == 0:
        return x.astype(np.float32)
    n = int(len(x) * to / sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def decode(path: Path, sr: int = 22050) -> np.ndarray:
    """Any audio file -> mono float32 at `sr`, using what this machine has. Raises with a fix if nothing can."""
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            x, s = read_wav(path)
            return resample(x, s, sr)
        except (wave.Error, ValueError, EOFError):
            pass                                                      # e.g. a float WAV: let a tool convert it
    with tempfile.TemporaryDirectory(prefix="cantus-") as td:
        out = Path(td) / "a.wav"
        tools = (("afconvert", ["afconvert", "-f", "WAVE", "-d", f"LEI16@{sr}", "-c", "1", str(path), str(out)]),
                 ("ffmpeg", ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(sr), str(out)]))
        for name, cmd in tools:                                       # one failing converter is not the end
            if not shutil.which(name):
                continue
            try:
                res = subprocess.run(cmd, capture_output=True, timeout=180)
            except (OSError, subprocess.SubprocessError):
                continue
            if res.returncode == 0 and out.exists() and out.stat().st_size > 44:
                try:
                    x, s = read_wav(out)
                    return resample(x, s, sr)
                except (wave.Error, ValueError, EOFError):
                    continue
    try:
        import miniaudio  # optional
        d = miniaudio.decode_file(str(path), output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1,
                                  sample_rate=sr)
        return np.asarray(d.samples, np.float32)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001  miniaudio could not read it either
        pass
    raise RuntimeError(f"can't read {path.name} on this machine: install ffmpeg (brew install ffmpeg) "
                       "or convert it to WAV")


def fingerprint(path: Path) -> str:
    p = Path(path)
    h = hashlib.sha1(str(p.stat().st_size).encode())
    with open(p, "rb") as f:
        h.update(f.read(1 << 21))
        if p.stat().st_size > 3 << 20:
            f.seek(-(1 << 20), 2)
            h.update(f.read())
    return h.hexdigest()[:16]


def chart_for_file(path: Path, cache_dir: Path) -> Chart:
    """Analyse an audio file once and cache its map; later calls read the cache."""
    path = Path(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = fingerprint(path)
    cached = cache_dir / f"{fp}.json"
    if cached.exists():
        try:
            ch = Chart.from_json(json.loads(cached.read_text()))
            ch.audio = str(path)                                      # the file may have been moved
            return ch
        except (ValueError, KeyError, json.JSONDecodeError):
            pass
    y = decode(path)
    a = analyse(y, 22050)
    stem = path.stem.replace("_", " ").strip()
    title, artist = (stem.split(" - ", 1)[1], stem.split(" - ", 1)[0]) if " - " in stem else (stem, "Your file")
    ch = Chart(f"import-{fp}", title, artist, "import", a["duration"], a["bpm"], str(path),
               {d: place(a["events"], d) for d in DIFFICULTY}, "Mapped by JAMES from your file")
    cached.write_text(json.dumps(ch.to_json()))
    return ch
