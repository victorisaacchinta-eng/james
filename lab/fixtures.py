"""Deterministic synthetic sensor fixtures for the Workflow Lab.

    python -m lab.fixtures            # (re)write lab/demo_data/sensors/*.csv

Same seed, same bytes, every run and every machine (random.Random with uniform() only).
All values are INVENTED for software testing. They are not engineering limits,
OEM recommendations or diagnostic evidence for real equipment."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260920
START = datetime(2026, 9, 20, 6, 0, tzinfo=timezone.utc)
N = 120                                   # one sample a minute, 06:00 to 07:59 UTC
COLUMNS = ["timestamp_utc", "asset_id", "vibration_mm_s", "bearing_temp_c",
           "motor_current_a", "flow_l_min", "quality_flag"]
OUT = Path(__file__).parent / "demo_data" / "sensors"

# (early low, early high, late low, late high) per channel
STABLE = {"vibration_mm_s": (1.8, 2.2), "bearing_temp_c": (44, 47),
          "motor_current_a": (7.8, 8.2), "flow_l_min": (98, 102)}
ANOMALY = {"vibration_mm_s": (5.5, 6.5), "bearing_temp_c": (64, 70),
           "motor_current_a": (8.8, 9.6), "flow_l_min": (60, 70)}
BORDERLINE = {"vibration_mm_s": (2.6, 2.9)}                       # about +35%: below the 50% flag
FAN = {"vibration_mm_s": (2.5, 3.0), "bearing_temp_c": (38, 41), "motor_current_a": (4.0, 4.4)}
DECIMALS = {"vibration_mm_s": 2, "bearing_temp_c": 1, "motor_current_a": 2, "flow_l_min": 1}


def _series(rng: random.Random, early, late=None) -> list[float]:
    """Stable first hour; if `late` is given, a progressive ramp that reaches the late range
    at minute 100 and stays there."""
    out = []
    for i in range(N):
        lo, hi = early
        if late and i >= 60:
            k = min(1.0, (i - 60) / 40)
            lo = early[0] + (late[0] - early[0]) * k
            hi = early[1] + (late[1] - early[1]) * k
        out.append(rng.uniform(lo, hi))
    return out


def _pump(rng, anomaly: dict | None = None) -> dict[str, list]:
    anomaly = anomaly or {}
    return {ch: _series(rng, STABLE[ch], anomaly.get(ch)) for ch in STABLE}


def _rows(asset: str, series: dict, blank: tuple[str, ...] = (), quality="ok") -> list[list[str]]:
    rows = []
    for i in range(N):
        t = (START + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        vals = []
        for ch in COLUMNS[2:6]:
            if ch in blank or ch not in series:
                vals.append("")
            else:
                vals.append(f"{series[ch][i]:.{DECIMALS[ch]}f}")
        rows.append([t, asset] + vals + [quality])
    return rows


def build() -> dict[str, str]:
    """Return {filename: csv text} for every variant."""
    files = {}
    for variant in ("healthy", "abnormal", "missing", "ambiguous", "invalid"):
        rng = random.Random(f"{SEED}-{variant}")
        if variant in ("abnormal", "missing", "invalid"):
            p3 = _pump(rng, ANOMALY)
        elif variant == "ambiguous":
            p3 = _pump(rng, {"vibration_mm_s": BORDERLINE["vibration_mm_s"]})
        else:
            p3 = _pump(rng)
        p4 = _pump(rng)
        fan = {ch: _series(rng, FAN[ch]) for ch in FAN}
        rows = (_rows("DEMO-PUMP-03", p3, blank=("bearing_temp_c",) if variant == "missing" else (),
                      quality="partial" if variant == "missing" else "ok")
                + _rows("DEMO-PUMP-04", p4) + _rows("DEMO-FAN-01", fan))
        rows.sort(key=lambda r: (r[0], r[1]))          # interleaved by time, like a real historian export
        if variant == "invalid":
            rows[57][0] = "2026-09-20T06:1X:00Z"       # malformed timestamp
            rows[180][2] = "abc"                       # non-numeric vibration
        text = ",".join(COLUMNS) + "\n" + "".join(",".join(r) + "\n" for r in rows)
        files[f"{variant}.csv"] = text
    return files


def write(out: Path = OUT) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, text in build().items():
        p = out / name
        p.write_text(text, encoding="utf-8", newline="")
        paths.append(p)
    return paths


if __name__ == "__main__":
    for p in write():
        print(f"wrote {p.relative_to(Path.cwd()) if p.is_relative_to(Path.cwd()) else p}")
