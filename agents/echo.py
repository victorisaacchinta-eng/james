"""ECHO: speech to a structured request.

faster-whisper runs locally. The local model on Ollama (config.CHAT_MODEL) turns the text into JSON, validated by
pydantic. If the model is down, a keyword parser takes over and the HUD says so.
ECHO never executes anything: it only produces an Intent."""
from __future__ import annotations

import math
import re
import threading

import numpy as np

import config
from agents import llm
from james_core.schemas import Intent

SAMPLE_RATE = 16000
SYMPTOM_WORDS = {
    "vibration": ["vibrat", "shak", "rattl", "wobbl"],
    "overheating": ["hot", "heat", "temperature", "burning"],
    "noise": ["noise", "noisy", "grind", "squeal", "knock", "gravel"],
    "leak": ["leak", "drip"],
}


class Recorder:
    """Push-to-talk recording: hold the V sign (or press V) to start, close the hand (or V) to stop."""

    def __init__(self):
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self.lock = threading.Lock()
        self.level = 0.0                     # loudness of the latest audio block, 0..1 (for the voice window meter)
        self.started = 0.0

    def _on_audio(self, d, *_):
        self._chunks.append(d.copy())
        rms = float(np.sqrt(np.mean(np.square(d)))) if len(d) else 0.0
        self.level = min(1.0, rms * 12)

    @property
    def active(self) -> bool:
        return self._stream is not None

    stats = None                                  # per-recorder counters, for the MIC log lines and tools/mic_check

    def _count(self, key: str, n: int = 1):
        if self.stats is None:
            self.stats = {"opens": 0, "closes": 0, "errors": 0, "restarts": 0}
        self.stats[key] = self.stats.get(key, 0) + n

    @staticmethod
    def device_name() -> str:
        """The input device PortAudio will use (for the log: a changed device is one suspect for -9986)."""
        try:
            import sounddevice as sd
            return str(sd.query_devices(kind="input").get("name", "?"))
        except Exception as e:  # noqa: BLE001
            return f"unknown ({type(e).__name__})"

    def start(self):
        import sounddevice as sd
        import time
        self._chunks, self.level, self.started = [], 0.0, time.monotonic()
        self.last_error = ""
        try:
            self._stream = self._open(sd)
        except Exception as e:  # noqa: BLE001
            # PortAudio -9986 (log 02:18 to 02:19): CoreAudio's device list went stale, e.g. the default mic or
            # output changed. Restart PortAudio once so it re-reads the devices, then try again.
            self._count("errors")
            self.last_error = str(e)[:120]
            try:
                sd._terminate(); sd._initialize()
                self._count("restarts")
            except Exception:  # noqa: BLE001
                pass
            try:
                self._stream = self._open(sd)
            except Exception as e2:  # noqa: BLE001
                self._count("errors")
                self.last_error = str(e2)[:120]
                raise
        self._count("opens")

    def _open(self, sd):
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=self._on_audio)
        try:
            stream.start()
        except Exception:
            stream.close()                        # never leave a half-open stream: 'active' must stay False
            raise
        return stream

    def stop(self) -> np.ndarray:
        """Stop and release the device. The stream is released even if stopping it fails (iteration 2, P0-F)."""
        if not self._stream:
            return np.zeros(0, np.float32)
        s, self._stream = self._stream, None
        try:
            s.stop()
        except Exception as e:  # noqa: BLE001
            self._count("errors")
            self.last_error = f"stop: {str(e)[:100]}"
        finally:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
            self._count("closes")
        self.level = 0.0
        chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, np.float32)


class Transcriber:
    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        with self._lock:
            return self._load()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> tuple[str, float]:
        """prompt: vocabulary hint (command words, installed app names) so names like 'Asphalt' are heard."""
        if audio.size < SAMPLE_RATE * 0.3:
            return "", 0.0
        segs, _ = self.load().transcribe(audio, language="en", vad_filter=True,
                                         beam_size=getattr(config, "WHISPER_BEAM", 5),
                                         initial_prompt=prompt or None, condition_on_previous_text=False)
        segs = list(segs)
        if not segs:
            return "", 0.0
        text = " ".join(s.text.strip() for s in segs).strip()
        conf = float(np.mean([math.exp(s.avg_logprob) for s in segs]))
        return text, round(min(0.99, conf), 2)


def _rules(text: str, target_asset: str) -> dict:
    t = text.lower()
    symptoms = [k for k, ws in SYMPTOM_WORDS.items() if any(w in t for w in ws)]
    m = re.search(r"pump\s*(?:number\s*)?(\d+|one|two|three|four|five)", t)
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    asset = target_asset
    if m:
        n = m.group(1)
        asset = f"P-{words.get(n, n)}"
    request = "escalate" if re.search(r"escalat|senior|supervisor", t) else "diagnose"
    return {"asset_id": asset, "symptoms": symptoms, "request": request}


SCHEMA = {
    "type": "object",
    "properties": {
        "pump_number": {"type": ["integer", "null"]},
        "symptoms": {"type": "array", "items": {"enum": list(SYMPTOM_WORDS)}, "uniqueItems": True, "maxItems": 3},
        "request": {"enum": ["diagnose", "next_step", "escalate"]},
    },
    "required": ["pump_number", "symptoms", "request"],
}


def parse_intent(text: str, asr_conf: float, target_asset: str) -> tuple[Intent, str]:
    """Return (Intent, backend label)."""
    backend = f"{config.CHAT_MODEL} (local)"
    try:
        out = llm.chat_json(
            "You turn a maintenance technician's words into JSON. Only list a symptom if the words describe it; "
            "if they describe no fault, return an empty list. pump_number is the number they said, or null.", text, SCHEMA)
        n = out.get("pump_number")
        llm_syms = list(dict.fromkeys(s for s in out.get("symptoms", []) if s in SYMPTOM_WORDS))
        heard = set(_rules(text, target_asset)["symptoms"])
        unsupported = [s for s in llm_syms if s not in heard]
        if unsupported:
            # The model listed a symptom nobody said (e.g. "noise" for "uh what"). Keep only what the words support.
            llm_syms = [s for s in llm_syms if s in heard]
            backend = f"{config.CHAT_MODEL} + word check"
        data = {"asset_id": f"P-{n}" if isinstance(n, int) else target_asset,
                "symptoms": llm_syms,
                "request": out.get("request", "diagnose")}
        if data["request"] not in ("diagnose", "next_step", "escalate"):
            raise llm.LLMUnavailable("bad request value")
    except llm.LLMUnavailable:
        data, backend = _rules(text, target_asset), "keyword rules (model offline)"
    conf = asr_conf if data["symptoms"] or data["request"] == "escalate" else min(asr_conf, 0.4)
    return Intent(asset_id=data["asset_id"], request=data["request"], symptoms=tuple(data["symptoms"]),
                  transcript=text, confidence=round(conf, 2)), backend
