"""Local model access through Ollama's HTTP API (standard library only).

If the local model is down, the cloud is NOT tried unless config.CLOUD_ENABLED is on,
and even then only through the egress guard. Callers get LLMUnavailable and must fall
back to rules and show that on the HUD."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

import config
from james_core import egress
from james_core.egress import CloudDisabled, EgressGuard


class LLMUnavailable(RuntimeError):
    pass


_status = {"checked": 0.0, "ok": False, "models": []}


def _post(path: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(config.OLLAMA_URL + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with egress.urlopen(req, "local_model", timeout=timeout) as r:
        return json.loads(r.read())


def available(model: str | None = None) -> bool:
    """Is Ollama up (and is this model pulled)? Cached for 10 s."""
    if time.time() - _status["checked"] > 10:
        try:
            with egress.urlopen(config.OLLAMA_URL + "/api/tags", "local_model", timeout=1.5) as r:
                tags = json.loads(r.read()).get("models", [])
                _status["models"] = [m["name"] for m in tags]
                _status["info"] = {m["name"]: {"digest": m.get("digest", ""), **(m.get("details") or {})} for m in tags}
            _status["ok"] = True
        except Exception:
            _status["ok"], _status["models"] = False, []
        _status["checked"] = time.time()
    if not _status["ok"]:
        return False
    if model is None:
        return True
    base = model.split(":")[0]
    return any(m == model or m.startswith(model) or m.split(":")[0] == base for m in _status["models"])


def model_info(model: str | None = None) -> dict:
    """Tag, digest and details of the local model as Ollama reports them (for traces and build records)."""
    model = model or config.CHAT_MODEL
    if not available(model):
        return {"model": model, "running": False}
    info = _status.get("info", {})
    name = next((m for m in info if m == model or m.split(":")[0] == model.split(":")[0]), model)
    d = info.get(name, {})
    return {"model": name, "running": True, "digest": d.get("digest", "")[:16],
            "parameter_size": d.get("parameter_size", ""), "quantization": d.get("quantization_level", ""),
            "settings": "temperature 0, think false, JSON schema output, keep_alive 30m"}


def _cloud_or_raise(payload: dict, why: str):
    try:
        EgressGuard(enabled=config.CLOUD_ENABLED).prepare(payload)
    except CloudDisabled:
        raise LLMUnavailable(f"{why}; cloud fallback is off")
    raise LLMUnavailable(f"{why}; cloud client not implemented in the demo build")


def chat_json(system: str, user: str, schema: dict | None = None, images: list[str] | None = None,
              timeout: float | None = None) -> dict:
    """Ask the local model for JSON. Raises LLMUnavailable on any failure."""
    if not available(config.CHAT_MODEL):
        _cloud_or_raise({"request": user[:200]}, "local model not running")
    body = {"model": config.CHAT_MODEL, "stream": False, "options": {"temperature": 0},
            "format": schema or "json", "keep_alive": "30m",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user, **({"images": images} if images else {})}]}
    if "qwen3" in config.CHAT_MODEL:
        body["think"] = False          # Qwen3 thinking mode is slow; we want a direct JSON answer
    try:
        try:
            out = _post("/api/chat", body, timeout or config.LLM_TIMEOUT_S)
        except urllib.error.HTTPError as e:
            if e.code == 400 and "think" in body:   # model doesn't accept the think flag
                body.pop("think")
                out = _post("/api/chat", body, timeout or config.LLM_TIMEOUT_S)
            else:
                raise
        msg = out["message"]
        text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
        if not text:
            # Qwen3-VL on this Ollama version returns the JSON in "thinking" when think is off
            text = (msg.get("thinking") or "").strip()
        return json.loads(text)
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, OSError) as e:
        raise LLMUnavailable(f"local model error: {e}")


def embed(texts: list[str]) -> list[list[float]]:
    if not available(config.EMBED_MODEL):
        raise LLMUnavailable("embedding model not pulled")
    try:
        out = _post("/api/embed", {"model": config.EMBED_MODEL, "input": texts}, config.LLM_TIMEOUT_S)
        return out["embeddings"]
    except Exception as e:
        raise LLMUnavailable(f"embedding error: {e}")
