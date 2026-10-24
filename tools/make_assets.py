"""Generate the demo's sample assets: a sample pump manual (written by us, not an OEM
document), a sample vibration log, seeded past jobs and a printable asset tag.

    python tools/make_assets.py
    python -m james_core.pack rehash packs/pharma-utility   # the manual and log live in the pack

The pack refuses to load until you rehash, because its files changed.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

A = config.ASSETS
A.mkdir(exist_ok=True)

# ---------- manual ----------
PAGES = [
    ("Centrifugal Pump P-3, Plant Utilities",
     ["SAMPLE MAINTENANCE GUIDE FOR DEMONSTRATION.",
      "Written for the JAMES demo. This is not an OEM document",
      "and must not be used on real equipment.",
      "",
      "Contents: 1 Safety. 2 Description. 5 Troubleshooting. 6.3 Bearing and coupling",
      "inspection. 6.4 Alignment check. 6.5 Cavitation checks."]),
    ("1 Safety",
     ["Apply lockout and tagout before any work that exposes rotating parts, the coupling",
      "or electrical terminals. Verify zero energy with a try-start from the local station.",
      "",
      "Wear gloves and eye protection for all inspection work.",
      "",
      "Bearing housings and the casing can be hot. Do not touch surfaces above the",
      "plant limit. Let the pump cool and measure before contact."]),
    ("2 Description",
     ["Close-coupled end-suction centrifugal pump for chilled water.",
      "Motor 7.5 kW, 2900 rpm. Drive-end bearing 6205-2RS. Flexible jaw coupling.",
      "",
      "Normal vibration (sample value): below 4.5 mm/s RMS at the bearing housing.",
      "Normal casing temperature (sample value): 35 to 50 C."]),
    ("5 Troubleshooting",
     ["Symptom: high vibration with rising bearing temperature.",
      "Likely cause: bearing wear. Go to 6.3 Bearing and coupling inspection.",
      "",
      "Symptom: high vibration with normal temperature, often after coupling work.",
      "Likely cause: misalignment. Go to 6.4 Alignment check.",
      "",
      "Symptom: noise like gravel in the pump, discharge pressure fluctuating.",
      "Likely cause: cavitation. Go to 6.5 Cavitation checks."]),
    ("6.3 Bearing and coupling inspection",
     ["Procedure for suspected bearing wear.",
      "Step 1: Review the vibration and temperature trend and confirm the fault.",
      "Step 2: Isolate the motor at the breaker and apply your personal lock and tag.",
      "Step 3: Verify zero energy with a try-start from the local station.",
      "Step 4: Remove the coupling guard.",
      "Step 5: Inspect the bearing housing for heat, noise and grease leakage and record findings.",
      "",
      "If the bearing is damaged, replace bearing 6205-2RS and escalate to the senior technician."]),
    ("6.4 Alignment check",
     ["Procedure for suspected misalignment.",
      "Step 1: Review the vibration trend and recent coupling work.",
      "Step 2: Isolate the motor at the breaker and apply your personal lock and tag.",
      "Step 3: Verify zero energy with a try-start from the local station.",
      "Step 4: Remove the coupling guard.",
      "Step 5: Check coupling alignment with a straight edge and record findings."]),
    ("6.5 Cavitation checks",
     ["Procedure for suspected cavitation.",
      "Step 1: Review suction and discharge pressure readings.",
      "Step 2: Check that the suction valve is fully open.",
      "Step 3: Check the suction strainer differential pressure.",
      "Step 4: Record findings and escalate if pressure stays unstable."]),
]


def make_manual():
    import pymupdf
    doc = pymupdf.open()
    for i, (title, lines) in enumerate(PAGES, 1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((56, 70), "SAMPLE - NOT AN OEM DOCUMENT", fontsize=8, color=(0.6, 0, 0))
        page.insert_text((56, 110), title, fontsize=18)
        y = 150
        for ln in lines:
            page.insert_text((56, y), ln, fontsize=11)
            y += 18
        page.insert_text((280, 800), f"p. {i}", fontsize=9)
    doc.save(config.MANUAL)


def make_log():
    rng = np.random.default_rng(7)
    start = datetime(2026, 9, 25, 0, 0)
    rows = ["timestamp,asset_id,vibration_rms_mm_s,casing_temp_c"]
    n_base, n_recent = 22 * 12, 2 * 12          # 5-minute samples
    for i in range(n_base + n_recent):
        t = start + timedelta(minutes=5 * i)
        if i < n_base:
            v, c = 2.8 + rng.normal(0, 0.15), 41 + rng.normal(0, 0.6)
        else:
            k = (i - n_base) / (n_recent - 1)
            v, c = 6.6 + 1.0 * k + rng.normal(0, 0.1), 45 + 3 * k + rng.normal(0, 0.3)
        rows.append(f"{t.isoformat()},P-3,{v:.2f},{c:.1f}")
    config.PACK.sample_log("P-3").write_text("\n".join(rows) + "\n")  # then: python -m james_core.pack rehash packs/pharma-utility


def make_jobs():
    config.SEEDED_JOBS.write_text("""# Seeded past jobs (sample data). Only approved jobs are ever reused.
jobs:
  - {job_id: "018", asset_id: P-3, symptoms: [vibration, overheating], cause: bearing_wear,
     fix: "Replaced drive-end bearing 6205-2RS", approved_by: senior-01}
  - {job_id: "019", asset_id: P-7, symptoms: [leak], cause: seal_wear,
     fix: "Replaced mechanical seal", approved_by: senior-01}
  - {job_id: "020", asset_id: P-3, symptoms: [vibration], cause: misalignment,
     fix: "Realigned coupling (unreviewed note)", approved_by: null}
""")


def make_tag():
    import cv2
    import pymupdf
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for mid, asset in config.ASSET_TAGS.items():
        img = cv2.aruco.generateImageMarker(d, mid, 600)
        img = cv2.copyMakeBorder(img, 60, 60, 60, 60, cv2.BORDER_CONSTANT, value=255)
        png = A / f"asset_tag_{asset}.png"
        cv2.imwrite(str(png), img)
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(97, 120, 497, 520), filename=str(png))
        page.insert_text((97, 560), f"Asset tag {asset}  (ArUco 4x4, id {mid})", fontsize=16)
        page.insert_text((97, 585), "Print at 100%. Stick it on the pump, or on a photo of a pump.", fontsize=11)
        doc.save(A / f"asset_tag_{asset}.pdf")


if __name__ == "__main__":
    make_manual(); make_log(); make_jobs(); make_tag()
    print("assets written to", A)
