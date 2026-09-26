"""PULSE: sensor-log validation, ingestion and deterministic analysis. No AI.

A dataset is validated as a whole before anything is stored: one bad row rejects the file.
Analysis filters strictly by asset and window, and never fills a gap with an invented value."""
from __future__ import annotations

import csv
import hashlib
import io
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Optional

from .schemas import ChannelSummary, Evidence, SensorEvidence

COLUMNS = ["timestamp_utc", "asset_id", "vibration_mm_s", "bearing_temp_c",
           "motor_current_a", "flow_l_min", "quality_flag"]
CHANNELS = COLUMNS[2:6]
QUALITY_OK = {"ok", "partial"}          # partial: some channels empty, the rest usable
QUALITY_ALL = QUALITY_OK | {"suspect", "bad"}
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
LABELS = {"vibration_mm_s": "vibration", "bearing_temp_c": "bearing temperature",
          "motor_current_a": "motor current", "flow_l_min": "flow"}


class DatasetInvalid(Exception):
    def __init__(self, name: str, problems: list[str]):
        self.problems = problems
        shown = problems[:12] + ([f"... and {len(problems) - 12} more"] if len(problems) > 12 else [])
        super().__init__(f"Dataset {name} rejected, nothing stored:\n" + "\n".join(f"  - {p}" for p in shown))


@dataclass(frozen=True)
class Sample:
    row: int                     # 1 = first data row of the file
    ts: datetime
    asset_id: str
    values: dict                 # channel -> float | None
    quality: str


@dataclass
class Dataset:
    name: str
    sha256: str
    samples: list[Sample] = field(default_factory=list)

    @property
    def dataset_id(self) -> str:
        return f"ds-{self.sha256[:12]}"


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, TS_FMT).replace(tzinfo=timezone.utc)


def fmt_ts(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime(TS_FMT)


def validate(name: str, raw: bytes, assets: dict, pack_units: dict) -> Dataset:
    """Parse and validate a whole CSV. `assets` is the site metadata; `pack_units` maps
    asset_id -> {column: unit} from that asset's industry pack (empty if no pack)."""
    problems: list[str] = []
    digest = hashlib.sha256(raw).hexdigest()
    ds = Dataset(name=name, sha256=digest)
    # unit metadata must agree with the pack before any row is trusted
    for aid, meta in assets.items():
        for col, unit in (pack_units.get(aid) or {}).items():
            site = meta["channels"].get(col)
            if site not in (unit, None, "not_applicable"):
                problems.append(f"{aid}.{col}: site metadata says '{site}', the pack says '{unit}'")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise DatasetInvalid(name, ["file is not UTF-8 text"]) from None
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header != COLUMNS:
        raise DatasetInvalid(name, problems + [f"header must be {','.join(COLUMNS)}"])
    last: dict[str, datetime] = {}
    for i, rec in enumerate(reader, start=1):
        where = f"row {i}"
        if len(rec) != len(COLUMNS):
            problems.append(f"{where}: {len(rec)} fields, expected {len(COLUMNS)}")
            continue
        ts_s, aid, *vals, q = rec
        try:
            ts = parse_ts(ts_s)
        except ValueError:
            problems.append(f"{where}: malformed timestamp '{ts_s}' (expected {TS_FMT}, UTC)")
            continue
        if aid not in assets:
            problems.append(f"{where}: unknown asset '{aid}'")
            continue
        if q not in QUALITY_ALL:
            problems.append(f"{where}: unknown quality flag '{q}'")
        values = {}
        for col, v in zip(CHANNELS, vals):
            unit = assets[aid]["channels"].get(col)
            if v.strip() == "":
                values[col] = None
                continue
            if unit == "not_applicable":
                problems.append(f"{where}: {aid} has no {col} channel, but a value was supplied")
                continue
            try:
                f = float(v)
                if not math.isfinite(f):
                    raise ValueError
            except ValueError:
                problems.append(f"{where}: {col} value '{v}' is not a number")
                continue
            values[col] = f
        if aid in last and ts <= last[aid]:
            problems.append(f"{where}: {aid} timestamps are not strictly increasing")
        last[aid] = ts
        ds.samples.append(Sample(i, ts, aid, values, q))
    if not ds.samples:
        problems.append("no data rows")
    if problems:
        raise DatasetInvalid(name, problems)
    return ds


def _evidence_id(*parts) -> str:
    return "ev-" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:10]


def analyze(ds: Dataset, asset_id: str, asset_meta: dict, clock: datetime, cfg: dict,
            job_id: Optional[str] = None, ingested_at: Optional[str] = None) -> SensorEvidence:
    """Baseline vs current per channel, for one asset only. Deterministic."""
    mine = [s for s in ds.samples if s.asset_id == asset_id and s.ts <= clock]
    limitations: list[str] = []
    if not mine:
        return SensorEvidence(
            asset_id=asset_id, dataset_id=ds.dataset_id, dataset_hash=ds.sha256, scenario_clock=fmt_ts(clock),
            window_start=fmt_ts(clock), window_end=fmt_ts(clock), baseline_end=fmt_ts(clock),
            current_start=fmt_ts(clock), newest_sample=None, freshness="unknown",
            channels=tuple(ChannelSummary(channel=c, units=asset_meta["channels"].get(c), flag="unavailable")
                           for c in CHANNELS),
            limitations=(f"No samples for {asset_id} in {ds.name} at or before the scenario clock.",))
    newest = max(s.ts for s in mine)
    age = clock - newest
    fresh_limit = timedelta(minutes=cfg["freshness_limit_min"])
    freshness = "stale" if age > fresh_limit else "fresh"
    if freshness == "stale":
        h, m = divmod(int(age.total_seconds() // 60), 60)
        limitations.append(f"Stale: the newest {asset_id} sample ({fmt_ts(newest)}) is {h} h {m} min older than "
                           f"the scenario clock ({fmt_ts(clock)}); the demo freshness limit is "
                           f"{cfg['freshness_limit_min']} min. Values below describe that older window, not now.")
    end = newest                                            # analyse up to the newest real sample
    w_start = end - timedelta(minutes=cfg["window_min"]) + timedelta(minutes=1)
    b_end = w_start + timedelta(minutes=cfg["baseline_min"])
    c_start = end - timedelta(minutes=cfg["current_min"]) + timedelta(minutes=1)
    window = [s for s in mine if w_start <= s.ts <= end]
    used = [s for s in window if s.quality in QUALITY_OK]
    excluded = len(window) - len(used)
    if excluded:
        limitations.append(f"{excluded} rows excluded for a quality flag other than ok/partial.")
    base = [s for s in used if s.ts < b_end]
    cur = [s for s in used if s.ts >= c_start]
    obs_at = fmt_ts(newest)
    chans, evs = [], []
    for col in CHANNELS:
        unit = asset_meta["channels"].get(col)
        if unit == "not_applicable":
            chans.append(ChannelSummary(channel=col, units=None, flag="not_applicable",
                                        note=f"{asset_id} has no {LABELS[col]} channel"))
            continue
        bv = [s.values[col] for s in base if s.values.get(col) is not None]
        cv = [s.values[col] for s in cur if s.values.get(col) is not None]
        if not bv or not cv:
            note = f"{LABELS[col]} unavailable: {len(bv)} baseline and {len(cv)} current readings in the window"
            limitations.append(note[0].upper() + note[1:] + ". No value is estimated.")
            chans.append(ChannelSummary(channel=col, units=unit, flag="unavailable", baseline_n=len(bv),
                                        current_n=len(cv), note=note))
            continue
        bm, cm = round(fmean(bv), 2), round(fmean(cv), 2)
        latest = cv[-1]
        diff = round(cm - bm, 2)
        pct = round(diff / bm * 100, 1) if bm else None
        thr = float(cfg["thresholds_pct"][col])
        if pct is None:
            flag = "unavailable"
        elif abs(pct) >= thr:
            flag = "up" if pct > 0 else "down"
        elif abs(pct) >= thr / 2:
            flag = "borderline"
        else:
            flag = "within_band"
        eid = _evidence_id(ds.sha256, asset_id, col, fmt_ts(w_start), fmt_ts(end))
        rows = [s.row for s in window]
        summary = (f"{LABELS[col]}: baseline mean {bm:g} {unit} ({fmt_ts(w_start)[11:16]}-{fmt_ts(b_end - timedelta(minutes=1))[11:16]} UTC, "
                   f"n={len(bv)}), current mean {cm:g} {unit} ({fmt_ts(c_start)[11:16]}-{fmt_ts(end)[11:16]} UTC, n={len(cv)}), "
                   f"change {diff:+g} {unit} ({pct:+g}%), demo flag at {thr:g}%: {flag.replace('_', ' ')}")
        evs.append(Evidence(
            evidence_id=eid, source_type="sensor_channel", asset_id=asset_id, job_id=job_id,
            source_id=ds.dataset_id, source_version=ds.sha256,
            locator=f"{ds.name}#asset={asset_id}&channel={col}&t={fmt_ts(w_start)}..{fmt_ts(end)}&rows={min(rows)}-{max(rows)}",
            observed_at=obs_at, ingested_at=ingested_at, freshness=freshness, value=cm, units=unit,
            quality="partial" if any(s.quality == "partial" for s in window) else "ok",
            provenance="synthetic_sensor", summary=summary))
        chans.append(ChannelSummary(channel=col, units=unit, flag=flag, baseline_n=len(bv), current_n=len(cv),
                                    baseline_mean=bm, current_mean=cm, current_latest=latest, abs_change=diff,
                                    pct_change=pct, threshold_pct=thr, evidence_id=eid))
    return SensorEvidence(
        asset_id=asset_id, dataset_id=ds.dataset_id, dataset_hash=ds.sha256, scenario_clock=fmt_ts(clock),
        window_start=fmt_ts(w_start), window_end=fmt_ts(end), baseline_end=fmt_ts(b_end),
        current_start=fmt_ts(c_start), newest_sample=obs_at, freshness=freshness,
        rows=tuple(s.row for s in window), excluded_rows=excluded, channels=tuple(chans),
        limitations=tuple(limitations), evidence=tuple(evs))


def series(ds: Dataset, asset_id: str, sev: SensorEvidence) -> dict:
    """Chart data for the analysed window only."""
    if sev.newest_sample is None:
        return {"t": [], "channels": {}}
    lo, hi = parse_ts(sev.window_start), parse_ts(sev.window_end)
    pts = [s for s in ds.samples if s.asset_id == asset_id and lo <= s.ts <= hi]
    return {"t": [fmt_ts(s.ts) for s in pts],
            "channels": {c: [s.values.get(c) for s in pts] for c in CHANNELS},
            "quality": [s.quality for s in pts]}


def load_file(path: Path) -> tuple[str, bytes]:
    return path.name, path.read_bytes()
