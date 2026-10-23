"""CANTUS built-in songs: public-domain melodies, arranged here as plain note lists, and a small piano synth.

Every built-in is a work whose composer died long ago (the newest is Beethoven, 1827), written out as a simple
melody (plus a quiet bass where it helps) for this game. No recording, score edition or arrangement by anyone
else is copied. Anything else is brought by the player as their own audio file and never ships in the repo.

A melody is a string of tokens: NOTE[:BEATS], with NOTE like C4, F#5, Bb3, or r for a rest. A token without
:BEATS lasts `unit` beats. "|" is a bar line and is ignored.
"""
from __future__ import annotations

import math
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SR = 44100
SYNTH_VERSION = 1                 # bump when render() changes, so cached WAVs are made again
NAMES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def midi(note: str) -> int:
    """'C4' -> 60, 'F#5' -> 78, 'Bb3' -> 58."""
    n = NAMES[note[0].upper()]
    i = 1
    while i < len(note) and note[i] in "#b":
        n += 1 if note[i] == "#" else -1
        i += 1
    return n + 12 * (int(note[i:]) + 1)


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def parse(melody: str, unit: float = 1.0, transpose: int = 0) -> list[tuple[float, float, int | None]]:
    """Melody string -> [(start_beat, beats, midi or None for a rest)]."""
    out, t = [], 0.0
    for tok in melody.replace("|", " ").split():
        name, _, d = tok.partition(":")
        beats = float(d) if d else unit
        out.append((t, beats, None if name.lower() == "r" else midi(name) + transpose))
        t += beats
    return out


@dataclass
class Song:
    id: str
    title: str
    composer: str
    year: str
    bpm: float
    melody: list                  # [(start_beat, beats, midi|None)]
    bass: list = field(default_factory=list)
    note: str = ""

    @property
    def beats(self) -> float:
        ends = [s + d for s, d, _ in self.melody + self.bass]
        return max(ends) if ends else 0.0

    @property
    def seconds(self) -> float:
        return self.beats * 60.0 / self.bpm

    @property
    def fingerprint(self) -> str:
        """Changes whenever the notes, the tempo or the synth change: the cached WAV is named by it."""
        import hashlib
        return hashlib.sha1(repr((SYNTH_VERSION, self.bpm, self.melody, self.bass)).encode()).hexdigest()[:10]

    def notes_seconds(self) -> list[tuple[float, float, int]]:
        """Melody notes as (start_s, duration_s, midi), rests dropped."""
        k = 60.0 / self.bpm
        return [(s * k, d * k, m) for s, d, m in self.melody if m is not None]


def _fur_elise() -> Song:
    a = ("E5 D#5 | E5 D#5 E5 B4 D5 C5 | A4:.5 r C4 E4 A4 | B4:.5 r E4 G#4 B4 | C5:.5 r E4 E5 D#5 | "
         "E5 D#5 E5 B4 D5 C5 | A4:.5 r C4 E4 A4 | B4:.5 r E4 C5 B4 | A4:.5 r ")
    b = "B4 C5 D5 | E5:.75 G4 F5 E5 | D5:.75 F4 E5 D5 | C5:.75 E4 D5 C5 | B4:.5 r E4 E5 r | "
    mel = parse((a + b) * 2 + a, unit=0.25)
    return Song("fur_elise", "Für Elise", "Ludwig van Beethoven", "1810", 66, mel,
                note="The opening theme and its first episode, melody only.")


def _ode_to_joy() -> Song:
    l1 = "E4 E4 F4 G4 | G4 F4 E4 D4 | C4 C4 D4 E4 | E4:1.5 D4:.5 D4:2 | "
    l2 = "E4 E4 F4 G4 | G4 F4 E4 D4 | C4 C4 D4 E4 | D4:1.5 C4:.5 C4:2 | "
    l3 = "D4 D4 E4 C4 | D4 E4:.5 F4:.5 E4 C4 | D4 E4:.5 F4:.5 E4 D4 | C4 D4 G3:2 | "
    mel = parse(l1 + l2 + l3 + l2, transpose=12)
    roots = "C3:4 G2:4 C3:4 G2:4 C3:4 G2:4 C3:4 C3:4 G2:4 C3:4 G2:4 C3:2 G2:2 C3:4 G2:4 C3:4 C3:4"
    return Song("ode_to_joy", "Ode to Joy", "Ludwig van Beethoven", "1824", 104, mel, parse(roots),
                note="The Ninth Symphony's theme, with a simple bass.")


def _canon() -> Song:
    v1 = "F#5:2 E5:2 D5:2 C#5:2 B4:2 A4:2 B4:2 C#5:2 | "
    v2 = "F#4 A4 D5 C#5 B4 D5 F#5 A5 G5 B5 A5 G5 F#5 E5 D5 C#5 | "
    v3 = "D5:.5 F#5:.5 A5:.5 G5:.5 F#5:.5 D5:.5 F#5:.5 E5:.5 D5:.5 B4:.5 D5:.5 A5:.5 G5:.5 B5:.5 A5:.5 G5:.5 | "
    v4 = "F#5:.5 D5:.5 E5:.5 C#5:.5 D5:.5 F#5:.5 A5:.5 A4:.5 B4:.5 G4:.5 A4:.5 F#4:.5 D4:.5 D5:1.5 | "
    mel = parse(v1 + v2 + v3 + v4 + v1)
    ground = "D3:2 A2:2 B2:2 F#2:2 G2:2 D2:2 G2:2 A2:2 "
    return Song("canon_in_d", "Canon in D", "Johann Pachelbel", "c. 1680", 72, mel, parse(ground * 4),
                note="Over the famous eight-chord ground bass; the variations are arranged for this game.")


def _turkish_march() -> Song:
    p1 = "B4:.25 A4:.25 G#4:.25 A4:.25 C5:1 | D5:.25 C5:.25 B4:.25 C5:.25 E5:1 | "
    p2 = ("F5:.25 E5:.25 D#5:.25 E5:.25 B5:.25 A5:.25 G#5:.25 A5:.25 B5:.25 A5:.25 G#5:.25 A5:.25 C6:1 | "
          "A5:.5 C6:.5 B5:.5 A5:.5 G5:.5 A5:.5 B5:.5 A5:.5 G5:.5 A5:.5 B5:.5 A5:.5 G5:.5 F#5:.5 E5:1 | ")
    end = "B4:.25 A4:.25 G#4:.25 A4:.25 C5:1 | B4:.5 G#4:.5 A4:2 | "
    mel = parse((p1 + p2) * 4 + end)
    return Song("turkish_march", "Turkish March", "Wolfgang Amadeus Mozart", "1783", 116, mel,
                note="Rondo alla Turca's opening theme, four times round.")


def builtin_songs() -> list[Song]:
    return [_ode_to_joy(), _fur_elise(), _canon(), _turkish_march()]


# synth

def _tone(f0: float, dur: float, vel: float = 1.0, bass: bool = False) -> np.ndarray:
    """A soft piano-like note: a few harmonics, a fast attack and an exponential decay."""
    tail = 0.35 if not bass else 0.2
    n = int((dur + tail) * SR)
    t = np.arange(n, dtype=np.float32) / SR
    tau = (0.9 if not bass else 1.4) * (220.0 / f0) ** 0.35
    env = np.exp(-t / tau) * np.minimum(1.0, t / 0.004)
    rel = np.clip((dur + tail - t) / tail, 0.0, 1.0)                     # fade out after the note ends
    env *= np.where(t > dur, rel, 1.0)
    out = np.zeros(n, np.float32)
    parts = ((1, 1.0), (2, 0.45), (3, 0.22), (4, 0.12), (5, 0.06)) if not bass else ((1, 1.0), (2, 0.3), (3, 0.08))
    for k, a in parts:
        if f0 * k < SR / 2.2:
            out += a * np.sin(2 * math.pi * f0 * k * t * (1 + 0.0004 * k)).astype(np.float32)
    return (out * env * vel).astype(np.float32)


def render(song: Song) -> np.ndarray:
    """The whole song as mono float32 at SR, peak-normalised to 0.85."""
    k = 60.0 / song.bpm
    total = int((song.beats * k + 1.0) * SR)
    buf = np.zeros(total, np.float32)
    for voice, vel, is_bass in ((song.melody, 0.8, False), (song.bass, 0.38, True)):
        for s, d, m in voice:
            if m is None:
                continue
            w = _tone(hz(m), d * k, vel, is_bass)
            i = int(s * k * SR)
            j = min(total, i + len(w))
            buf[i:j] += w[: j - i]
    peak = float(np.abs(buf).max()) or 1.0
    return buf * (0.85 / peak)


def write_wav(path: Path, audio: np.ndarray, sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return path
