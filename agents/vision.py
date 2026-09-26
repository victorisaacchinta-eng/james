"""IDENTIFY: what is this object, what's wrong with it, how to fix it.
Runs Qwen3-VL on the laptop. The image never leaves the machine."""
from __future__ import annotations

import base64
import sys

import cv2

from agents import llm
from agents import problems as PR

SCHEMA = {
    "type": "object",
    "properties": {
        "object": {"type": "string"},
        "brand": {"type": ["string", "null"]},
        "model": {"type": ["string", "null"]},
        "model_confidence": {"type": "number"},
        "problem": {"type": "string"},
        "problem_confidence": {"type": "number"},
        "common_problems": {"type": "array", "maxItems": 4, "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "sign": {"type": "string"}, "seen": {"type": "boolean"}},
            "required": ["title", "sign", "seen"]}},
        "part_needed": {"type": ["string", "null"]},
    },
    "required": ["object", "brand", "model", "model_confidence", "problem", "problem_confidence", "common_problems",
                 "part_needed"],
}

PROMPT = (
    "You are JAMES, a repair assistant. The image is a close crop of one object a person is holding up to a "
    "camera. Describe only that object; ignore hands, faces and background.\n"
    "1. object: what the object is, in 1 to 3 words.\n"
    "2. brand and model: only if you can actually see a logo, text or an unmistakable design. Otherwise null. "
    "model_confidence from 0 to 1. Never guess a specific model from a generic look.\n"
    "3. problem: visible damage only, for example a cracked screen, a dent, a scratched lens. "
    "If nothing is visibly wrong, say 'no visible damage'. problem_confidence from 0 to 1.\n"
    "4. common_problems: the 4 problems owners of THIS kind of object most often have, most common first. "
    "title: at most 6 words. sign: what the owner notices, at most 12 words. seen: true ONLY if that problem "
    "is actually visible in this image, otherwise false.\n"
    "5. part_needed: the part to buy for a visible problem, else null.\n"
    "Reply in JSON only."
)

FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "minItems": 3, "maxItems": 6, "items": {
            "type": "object", "properties": {"title": {"type": "string"}, "detail": {"type": "string"}},
            "required": ["title", "detail"]}},
        "cause": {"type": ["string", "null"]},
        "safety": {"type": ["string", "null"]},
        "pro_when": {"type": ["string", "null"]},
        "part_needed": {"type": ["string", "null"]},
    },
    "required": ["steps", "cause", "safety", "pro_when", "part_needed"],
}

FIX_PROMPT = (
    "You are JAMES, a careful repair assistant. Give the fixes people most commonly use for this problem, "
    "as 3 to 6 steps, easiest and cheapest checks first, the actual repair last. "
    "Each step: title at most 7 words; detail 1 to 3 plain sentences a non-expert can follow, with where to look "
    "in settings or which tool to use. Only well-known, safe practice; do not invent part numbers or prices. "
    "cause: one plain sentence on why this problem usually happens. "
    "safety: one sentence if there is a real risk (batteries, mains power, sharp glass, chemicals), else null. "
    "pro_when: one sentence on when to take it to a professional, else null. "
    "part_needed: the part to buy if the fix needs one, else null. Reply in JSON only."
)


def encode(frame_bgr, max_w: int = 1024) -> str:
    h, w = frame_bgr.shape[:2]
    if w > max_w:
        frame_bgr = cv2.resize(frame_bgr, (max_w, int(h * max_w / w)))
    ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode()


def identify(frame_bgr) -> dict:
    """Return the parsed result, or raise llm.LLMUnavailable."""
    out = llm.chat_json(PROMPT, "What is this, and what is wrong with it?", SCHEMA,
                        images=[encode(frame_bgr)], timeout=90)
    out["fix"] = []                                   # fixes now come per chosen problem (fix_for)
    out["problems"] = PR.merge(str(out.get("object") or ""), out.get("common_problems"), out.get("problem"))
    for k in ("brand", "model", "part_needed"):          # live 04:20:42: the model wrote the string 'null'
        if str(out.get(k) or "").strip().lower() in ("", "null", "none", "unknown", "n/a", "not visible"):
            out[k] = None
    for k in ("model_confidence", "problem_confidence"):
        try:
            out[k] = max(0.0, min(1.0, float(out.get(k) or 0)))
        except (TypeError, ValueError):
            out[k] = 0.0
    return out


def needs_model(res: dict) -> bool:
    return not res.get("model") or res.get("model_confidence", 0) < 0.6


def _clean_fix(out: dict) -> dict | None:
    steps = []
    for st in out.get("steps") or []:
        if isinstance(st, dict) and str(st.get("title") or "").strip():
            steps.append({"title": str(st["title"]).strip()[:70], "detail": str(st.get("detail") or "").strip()[:420]})
    if len(steps) < 2:
        return None
    txt = lambda k: (str(out.get(k)).strip() or None) if out.get(k) else None
    return {"steps": steps[:6], "cause": txt("cause"), "safety": txt("safety"), "pro_when": txt("pro_when"),
            "part_needed": txt("part_needed")}


def fix_for(obj: str, name: str, problem: dict) -> dict:
    """Steps to fix one chosen problem: the local model (text only, no image), else the built-in catalog.
    Returns {steps: [{title, detail}], safety, pro_when, part_needed, source: model | catalog | none}."""
    ask = (f"Object: {obj}" + (f" ({name})" if name else "") + f"\nProblem: {problem.get('title')}"
           + (f"\nWhat the owner notices: {problem['sign']}" if problem.get("sign") else "")
           + ("\nThe problem is visible on the object." if problem.get("seen") else "")
           + ("\nThe owner's computer is a Mac: any computer steps must be macOS steps (System Settings, Bluetooth), "
              "never Windows ones like Device Manager." if sys.platform == "darwin" else ""))
    try:
        fixed = _clean_fix(llm.chat_json(FIX_PROMPT, ask, FIX_SCHEMA, timeout=60))
        if fixed:
            return dict(fixed, source="model")
    except llm.LLMUnavailable:
        pass
    cat = PR.catalog_fix(obj, problem.get("title", ""))
    if cat:
        return dict(cat, source="catalog")
    return {"steps": [], "safety": None, "pro_when": None, "part_needed": None, "source": "none"}
