"""Egress guard for the optional cloud fallback (Gemini).

Off by default. When a plant turns it on, only allowlisted text fields leave,
after redaction, and every request is written to the ledger. Camera frames,
audio and raw machine logs can never be sent, even if someone allowlists them."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Optional

NEVER_SEND = {"image", "frame", "frames", "audio", "raw_log", "video", "face"}
DEFAULT_ALLOWED = {"symptoms", "request", "excerpts"}

REDACTIONS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"), "[phone]"),
    (re.compile(r"\bEMP[-_ ]?\d+\b", re.I), "[employee-id]"),
]


class CloudDisabled(RuntimeError):
    pass


def redact(text: str) -> str:
    for rx, repl in REDACTIONS:
        text = rx.sub(repl, text)
    return text


def _redact_value(v: Any) -> Any:
    if isinstance(v, str):
        return redact(v)
    if isinstance(v, (list, tuple)):
        return [_redact_value(x) for x in v]
    return v


class EgressGuard:
    def __init__(self, enabled: bool = False, allowed_fields: Optional[set[str]] = None,
                 audit: Optional[Callable[[dict], None]] = None):
        self.enabled = enabled
        self.allowed = set(allowed_fields or DEFAULT_ALLOWED) - NEVER_SEND
        self.audit = audit or (lambda _: None)

    def prepare(self, payload: dict) -> dict:
        """Return the exact payload that may leave the device, or raise."""
        if not self.enabled:
            self.audit({"event": "cloud_refused", "reason": "cloud fallback disabled"})
            raise CloudDisabled("Cloud fallback is off. Show the limitation instead of guessing.")
        dropped = sorted(k for k in payload if k not in self.allowed)
        out = {k: _redact_value(v) for k, v in payload.items() if k in self.allowed}
        body = json.dumps(out, sort_keys=True)
        self.audit({"event": "cloud_request", "fields": sorted(out), "dropped": dropped,
                    "sha256": hashlib.sha256(body.encode()).hexdigest(), "bytes": len(body)})
        return out


# ---------- tool families: one gate for everything that leaves the laptop (iteration 2, P0-D / ST-06) ----------
# JAMES has two tool families that must not mix:
#   consumer  (james.py)          web search, YouTube/GitHub text requests, opening web pages, AI chat pages.
#                                 These SEND the words you say to that site (a prompt in a URL is sent when
#                                 the page loads, before anyone presses Send).
#   maintenance (app.py, the Lab) local only. Plant context (machine ids, logs, manual text) may go to the
#                                 local model on this computer and nowhere else.
# Every outbound call names its family; in maintenance mode the consumer web family is refused outright.
import threading
import time
import urllib.parse
from collections import deque

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
FAMILIES = {"consumer_web", "local_model"}


class EgressBlocked(PermissionError):
    pass


_state = {"mode": "consumer"}
_log: deque = deque(maxlen=200)
_lock = threading.Lock()


def set_mode(mode: str) -> None:
    if mode not in ("consumer", "maintenance"):
        raise ValueError(mode)
    _state["mode"] = mode


def mode() -> str:
    return _state["mode"]


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def check(family: str, url: str, sent_chars: int = 0) -> None:
    """Call before any outbound request or browser open. Raises EgressBlocked when the current mode forbids it.
    Records host, family and size (never the text itself)."""
    host = _host(url)
    if family not in FAMILIES:
        allowed, why = False, f"unknown tool family '{family}'"
    elif family == "local_model":
        u = urllib.parse.urlsplit(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        want = local_model_endpoint()
        allowed = host in LOCAL_HOSTS and f"{port}" == want.rsplit(":", 1)[-1] and u.scheme == "http"
        why = f"the model must run on this computer at {want}"
    elif mode() == "maintenance":
        allowed, why = False, "maintenance mode is local only: consumer web tools are switched off"
    else:
        allowed, why = url.startswith(("https://", "http://")) or url.startswith("spotify:"), "not a web address"
        if allowed and host in LOCAL_HOSTS:
            allowed, why = False, "consumer tools never talk to services on this computer"
    with _lock:
        _log.append({"t": time.time(), "mode": mode(), "family": family, "host": host or url.split(":")[0],
                     "chars": int(sent_chars), "allowed": allowed})
    if not allowed:
        raise EgressBlocked(f"{family} to {host or url[:30]} refused: {why}")


def outbound() -> list[dict]:
    with _lock:
        return list(_log)


# ---------- transport: redirects are checked too (iteration 3, item E) ----------
import urllib.request as _ur


class _CheckedRedirect(_ur.HTTPRedirectHandler):
    def __init__(self, family: str):
        super().__init__()
        self.family = family

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check(self.family, newurl)                     # a redirect is a new destination: same rules, or refused
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(req, family: str, timeout: float = 10):
    """urllib's urlopen, but the first URL and every redirect pass `check` for this tool family."""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    check(family, url)
    return _ur.build_opener(_CheckedRedirect(family)).open(req, timeout=timeout)


def local_model_endpoint() -> str:
    """host:port the local model must be at (config.OLLAMA_URL); anything else is refused for local_model."""
    try:
        import config
        u = urllib.parse.urlsplit(config.OLLAMA_URL)
        return f"{(u.hostname or '').lower()}:{u.port or 80}"
    except Exception:  # noqa: BLE001
        return "localhost:11434"
