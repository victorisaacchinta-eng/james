"""JAMES maintenance HUD (app.py), drawn in the JAMES Spatial Interface Language (jsil.py).

The camera is the world stage. The UI is a quiet layer around it: a header, one task card,
the spatial cursor and target brackets. Sourced, uncertain, blocked and human-confirmed
information are drawn differently, and WARDEN blocks stay red.

The old names (AMBER, RED, text(), panel(), chip(), wrap()...) are kept so existing callers
work; they now map to JSIL tokens and PIL-rendered type."""
from __future__ import annotations

import time

import cv2

import jsil as J

# ---- back-compatible names, mapped to JSIL tokens ----
AMBER, RED, GREEN, CYAN = J.CONFIRM, J.ERROR, J.SUCCESS, J.CYAN
WHITE, GREY, INK = J.TEXT, J.TEXT_2, J.BLACK
FONT = cv2.FONT_HERSHEY_SIMPLEX


def ascii_(t: str) -> str:
    return str(t)            # PIL renders Unicode; kept for old callers


def _px(scale: float) -> int:
    return max(9, round(scale * 29))


def text(img, s, org, scale=0.52, color=WHITE, thick=1):
    J.text(img, s, org[0], org[1], _px(scale), color, "semi" if thick >= 2 else "sans")


def wrap(s: str, width_px: int, scale=0.52) -> list[str]:
    return J.wrap(s, width_px, _px(scale))


def panel(img, x, y, w, h, alpha=0.84, color=INK):
    J.panel(img, x, y, w, h, alpha)


def chip(img, label, x, y, fg=INK, bg=AMBER, scale=0.45):
    return J.pill(img, label, x, y, bg) + J.S2


# ---- state presentation ----
STATE_LABEL = {
    "LOCKED": "Locked", "TARGET_PENDING": "Targeting", "TARGET_CONFIRMED": "Target confirmed",
    "INTENT_CAPTURED": "Request captured", "EVIDENCE_GATHERING": "Gathering evidence",
    "AWAITING_CONFIRMATION": "Confirm step", "SAFETY_BLOCKED": "Blocked by WARDEN", "LOGGED": "Logged",
    "COMPLETE": "Job complete", "ESCALATED": "Escalated",
}
STATE_COLOR = {"LOCKED": J.LOCKED, "TARGET_PENDING": J.CYAN, "AWAITING_CONFIRMATION": J.CONFIRM,
               "SAFETY_BLOCKED": J.ERROR, "COMPLETE": J.SUCCESS, "LOGGED": J.SUCCESS, "ESCALATED": J.CONFIRM}


def _state_label(s: str) -> str:
    return STATE_LABEL.get(s, s.replace("_", " ").capitalize())


class Hud:
    def __init__(self):
        self.banner: tuple[str, tuple, float] | None = None
        self.header = J.Header()
        self.card_cache = J.LayerCache()
        self.show_hints = True

    def flash(self, msg: str, color=AMBER, secs=3.5):
        self.banner = (msg, color, time.time() + secs)

    # ---------- the task card ----------
    def _card_lines(self, v: dict, width: int) -> list[tuple]:
        """(kind, payload) rows. kinds: label, head, body, kv, mono, gap."""
        rows: list[tuple] = []
        state = v["state"]
        head = v.get("headline", "")
        head_col = v.get("headline_color", AMBER)
        if head_col == AMBER and state != "AWAITING_CONFIRMATION":
            head_col = J.TEXT                                # amber is for confirmation, not for every prompt
        rows.append(("label", "Safety check" if state == "SAFETY_BLOCKED" else "Current task", STATE_COLOR.get(state, J.TEXT_2)))
        for ln in J.wrap(head, int(width * 0.9), 18):            # the bold headline runs wider than measured
            rows.append(("head", ln, head_col))
        if v.get("prompt"):
            rows.append(("gap",))
            for ln in J.wrap(v["prompt"], width - 12, 15):
                rows.append(("prompt", ln))
        for r in v.get("readout", []) or []:
            if r.startswith("STEP"):
                left, _, rest = r.partition(" · ")
                rows.append(("gap",))
                rows.append(("label", left.title(), J.CONFIRM if state == "AWAITING_CONFIRMATION" else J.TEXT_2))
                for ln in J.wrap(rest.capitalize(), width, 19):
                    rows.append(("step", ln))
            elif r.startswith("BLOCKED"):
                rows.append(("gap",))
                rows.append(("label", "Why", J.ERROR))
            elif r.startswith("Rule:"):
                for ln in J.wrap(r, width, 12, "mono"):
                    rows.append(("mono", ln, J.ERROR))
            elif r.startswith("Reason:"):
                for ln in J.wrap(r.split(":", 1)[1].strip(), width, 15):
                    rows.append(("body", ln, J.ERROR))
            else:
                k, _, val = r.partition(": ")
                col = J.TEXT_2
                if k in ("Sources disagree", "Limitation"):
                    col = J.CONFIRM
                elif k == "Human confirmed" and val.startswith("yes"):
                    col = J.SUCCESS
                elif k == "Safety policy" and val.endswith("ALLOW"):
                    col = J.SUCCESS
                vl = J.wrap(val, width - 132, 13)
                for i, ln in enumerate(vl or [""]):
                    rows.append(("kv", k if i == 0 else "", ln, col))
        if v.get("evidence"):
            rows.append(("gap",))
            for e in v["evidence"]:
                for ln in J.wrap(e, width, 13):
                    rows.append(("body", ln, J.TEXT_2))
        if v.get("transcript"):
            rows.append(("gap",))
            heard = v["transcript"].removeprefix("Heard: ").strip()
            for i, ln in enumerate(J.wrap(heard, width - 132, 13)):
                rows.append(("kv", "Heard" if i == 0 else "", ln, J.CYAN_SOFT))
        if v.get("latency"):
            for ln in J.wrap(v["latency"], width, 11, "mono"):
                rows.append(("mono", ln, J.MUTED))
        return rows

    HEIGHT = {"label": 22, "head": 25, "prompt": 21, "step": 26, "body": 19, "kv": 19, "mono": 17, "gap": 8}

    def _card(self, img, v, x, y, w, max_h):
        rows = self._card_lines(v, w - 2 * J.S4)
        key = (w, max_h, v["state"], tuple(tuple(r) for r in rows))
        return self.card_cache.draw(img, key, x, y, w, max_h, lambda c: self._card_draw(c, v, rows, 0, 0, w, max_h))

    def _card_draw(self, img, v, rows, x, y, w, max_h):
        need = J.S4 + sum(self.HEIGHT[r[0]] for r in rows) + J.S3
        h = min(max_h, need)
        blocked = v["state"] == "SAFETY_BLOCKED"
        J.panel(img, x, y, w, h, 0.86, accent=J.ERROR if blocked else STATE_COLOR.get(v["state"], J.TEAL))
        if blocked:
            cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), J.ERROR, 2)
        yy = y + J.S4
        tx = x + J.S4
        for i, r in enumerate(rows):
            rh = self.HEIGHT[r[0]]
            if yy + rh > y + h - 4:                            # never clip: say there is more
                J.text(img, "...", tx, yy + 12, 13, J.MUTED)
                break
            kind = r[0]
            if kind == "label":
                J.label(img, r[1], tx, yy + 14, r[2])
            elif kind == "head":
                J.text(img, r[1], tx, yy + 19, 18, r[2], "semi")
            elif kind == "prompt":
                cv2.rectangle(img, (tx, yy), (tx + 2, yy + rh - 4), J.CONFIRM, -1)
                J.text(img, r[1], tx + 12, yy + 15, 15, J.CONFIRM)
            elif kind == "step":
                J.text(img, r[1], tx, yy + 20, 19, J.TEXT, "semi")
            elif kind == "body":
                J.text(img, r[1], tx, yy + 14, 13 if r[2] != J.ERROR else 15, r[2])
            elif kind == "kv":
                if r[1]:
                    J.text(img, r[1].upper(), tx, yy + 13, 10, J.MUTED, "semi", track=0.8)
                J.text(img, r[2], tx + 132, yy + 14, 13, r[3])
            elif kind == "mono":
                J.text(img, r[1], tx, yy + 13, 11 if r[2] == J.MUTED else 12, r[2], "mono")
            yy += rh
        return y + h

    # ---------- world stage overlays ----------
    def _lens(self, img, v, lens):
        state = v["state"]
        for t in lens.tags:
            pts = t.corners.astype(int)
            x0, y0 = pts[:, 0].min(), pts[:, 1].min()
            x1, y1 = pts[:, 0].max(), pts[:, 1].max()
            pointed = lens.pointed is t
            pad = 10
            J.brackets(img, x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad,
                       J.CYAN if pointed else J.TEXT_2, thick=2 if pointed else 1)
            name = t.asset_id or f"unknown tag {t.id}"
            J.text(img, name, x0 - pad, y0 - pad - 8, 13, J.CYAN if pointed else J.TEXT_2, "semi")
            if pointed:
                J.text(img, f"aim {lens.point_conf:.2f}", x1 + pad, y0 - pad - 8, 11, J.TEXT_2, "mono", anchor="r")
                if lens.fingertip:
                    c = tuple(int(a) for a in t.center)
                    cv2.line(img, lens.fingertip, c, J.TEAL, 1, cv2.LINE_AA)
        if getattr(lens, "hand", None):
            J.skeleton(img, lens.hand, J.HAND_LINKS, J.TEXT_2)
        if lens.fingertip:
            p = v.get("pinch_progress", 0.0)
            if state == "LOCKED":
                cs = "LOCKED"
            elif p >= 1:
                cs = "SELECTED"
            elif p > 0:
                cs = "CONFIRMING"
            elif lens.pointed:
                cs = "TARGETED"
            elif lens.tags:
                cs = "TARGETABLE"
            else:
                cs = "TRACKING"
            J.cursor(img, lens.fingertip, cs, p)

    # ---------- frame ----------
    def draw(self, img, v: dict, lens=None):
        H, W = img.shape[:2]
        state = v["state"]
        if state == "LOCKED":                                   # JSIL: locked interface dims
            img[:] = cv2.addWeighted(img, 0.45, img * 0, 0.55, 0)
        if lens is not None:
            self._lens(img, v, lens)

        # header: identity left, one state pill, real status right
        x = self.header.draw(img)
        x = J.pill(img, _state_label(state), x, 31, STATE_COLOR.get(state, J.TEXT_2), filled=state == "SAFETY_BLOCKED")
        tm = v.get("telemetry")
        fps = tm.stats()["fps"] if tm is not None else None
        J.status_right(img, [(J.SUCCESS, "CLOUD OFF"), (J.CONFIRM, "SAMPLE DATA")]
                       + ([(None, f"{fps:.1f} FPS")] if fps else []) + [(None, time.strftime("%H:%M"))], y=25)
        echo = v.get("echo_backend") or "-"
        sys_line = (f"{v.get('pack', '')}  ·  ECHO {'idle' if echo == '-' else echo}  ·  PAGE {v.get('page_backend', '-')}"
                    f"  ·  FOREMAN {v.get('orchestrator', '-')}")
        room = W - x - 2 * J.S4
        J.text(img, J.fit(sys_line, room, 10, "mono"), W - J.S4, 43, 10, J.MUTED, "mono", anchor="r")

        # task card
        cw = min(500, max(380, int(W * 0.36)))
        self._card(img, v, J.S4, 52 + J.S4, cw, H - 52 - J.S4 - 44 - 90)

        # telemetry (F)
        if tm is not None and v.get("show_telemetry"):
            J.telemetry_card(img, W - 196 - J.S4, 52 + J.S4, tm)

        # feedback
        if self.banner and time.time() < self.banner[2]:
            msg, col, _ = self.banner
            J.toast(img, msg, col, y_bottom=H - 30 - J.S4)
        J.hints(img, v.get("keys", ""), self.show_hints)
        return img
