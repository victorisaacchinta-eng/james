"""Owner-key usability numbers from the JAMES log (iteration 2, P1-B). Measures, does not guess reasons.

    python -m tools.owner_metrics                     # reads data/james_demo_log.txt

Counts: control acquired, control lost, bypasses (X), and for each bypass how long after control was lost it
came. Times are the log's HH:MM:SS (IST), so values are whole seconds. Why a bypass happened is NOT in the log:
ask the user and write the answer down next to these numbers."""
from __future__ import annotations

import re
import sys
from pathlib import Path

GAP_S = 300
LINE = re.compile(r"^(\d\d):(\d\d):(\d\d) OWNER (.*)$")


def _secs(h, m, s):
    return int(h) * 3600 + int(m) * 60 + int(s)


def metrics(lines) -> dict:
    acquired = lost = bypass = 0
    last_lost = None
    after_loss, acquire_waits = [], []
    searching_since = None
    for raw in lines:
        m = LINE.match(raw.strip())
        if not m:
            continue
        t, rest = _secs(*m.groups()[:3]), m.group(4)
        if "-> owner" in rest:
            acquired += 1
            if searching_since is not None:
                acquire_waits.append(t - searching_since)
            searching_since = None
        elif "owner -> searching" in rest:
            lost += 1
            last_lost = searching_since = t
        elif rest.startswith("key paused") or rest.startswith("bypass on"):
            bypass += 1
            if last_lost is not None:
                after_loss.append(t - last_lost)
        if rest.startswith("key required") or rest.startswith("bypass off"):
            searching_since = t
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    near = lambda xs: [x for x in xs if x <= GAP_S]           # longer gaps: app restarts or idle, not usability
    return {"acquired": acquired, "lost": lost, "bypasses": bypass,
            "bypass_seconds_after_loss": near(after_loss), "median_bypass_after_loss_s": med(near(after_loss)),
            "reacquire_seconds": near(acquire_waits), "median_reacquire_s": med(near(acquire_waits)),
            f"gaps_over_{GAP_S}s_left_out": len(after_loss) - len(near(after_loss))
            + len(acquire_waits) - len(near(acquire_waits))}


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "james_demo_log.txt"
    r = metrics(path.read_text(encoding="utf-8", errors="replace").splitlines())
    for k, v in r.items():
        print(f"{k}: {v}")
