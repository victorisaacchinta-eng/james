"""PULSE: reads the machine log it was given. Plain maths, no AI.

It never invents a reading: no log for the machine means no evidence, and the bundle
shows a MISSING_SENSOR limitation. It does not imply live telemetry.
Column names, units, the WARDEN fact and the fault signatures come from the industry
pack (sensors/<class>.yaml and faults/<class>.yaml), not from this file."""
from __future__ import annotations

import io

import pandas as pd

import config
from james_core.schemas import AgentId, Evidence, EvidenceKind


def analyze(asset_id: str, path=None, recent_h: float = 2.0, pack=None) -> tuple[list[Evidence], dict]:
    pack = pack or config.PACK
    cls = pack.class_of(asset_id)
    if cls is None:                       # machine not in the pack: nothing to read
        return [], {}
    if path is None:                      # the verified bytes from the pack, never a re-read of the folder
        got = pack.sample_log_bytes(asset_id)
        if got is None:
            return [], {}
        name, src = got[0], io.BytesIO(got[1])
    else:
        name, src = path.name, path
    sensors, faults = pack.sensors(cls), pack.faults(cls)
    vib, tmp = sensors.channels.get("vibration"), sensors.channels.get("casing_temp")
    if vib is None or tmp is None:        # this analysis needs both channels
        return [], {}
    ts = sensors.timestamp_column
    try:
        df = pd.read_csv(src, parse_dates=[ts])
        df = df[df[sensors.asset_column] == asset_id].sort_values(ts)
        v_col, t_col, t_idx = df[vib.column], df[tmp.column], df[ts]
    except (FileNotFoundError, KeyError, ValueError):
        return [], {}
    if len(df) < 10:
        return [], {}
    end = t_idx.max()
    recent_mask = t_idx > end - pd.Timedelta(hours=recent_h)
    v_now, v_base = v_col[recent_mask].mean(), v_col[~recent_mask].median()
    t_now, t_base = t_col[recent_mask].iloc[-1], t_col[~recent_mask].median()
    window = f"{t_idx[recent_mask].min():%H:%M}..{end:%H:%M}"
    ref = f"log:{name}[{window}]"
    ratio = v_now / v_base if v_base else 0
    rise = t_now - t_base
    stance = None
    for sig in faults.sensor_signatures:  # first matching signature wins
        if sig.vibration_ratio_min is not None and not ratio >= sig.vibration_ratio_min:
            continue
        if sig.temp_rise_min_c is not None and not rise >= sig.temp_rise_min_c:
            continue
        stance = sig.cause
        break
    claim = (f"vibration RMS {v_now:.1f} {vib.unit}, last {recent_h:g} h, baseline {v_base:.1f}; "
             f"casing {t_now:.0f} {tmp.unit} (baseline {t_base:.0f})")
    ev = Evidence(agent=AgentId.PULSE, kind=EvidenceKind.SENSOR, asset_id=asset_id, claim=claim, ref=ref,
                  confidence=0.8 if int(recent_mask.sum()) >= 12 else 0.5,
                  topic="likely_cause" if stance else None, stance=stance)
    # A measured value from the supplied log, used by WARDEN's temperature rule.
    return [ev], ({tmp.warden_fact: round(float(t_now), 1)} if tmp.warden_fact else {})
