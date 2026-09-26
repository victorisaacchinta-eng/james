"""Print the CURRENT markers, labelled (iteration 2, P1-C). Reprint after any marker change.

    python -m tools.make_marker_sheet

Writes assets/print/markers_<pack>-<version>.pdf (A4, print at 100% / actual size) with:
  * the owner key (config.OWNER_KEY_ID), clearly marked NOT A MACHINE TAG
  * every asset tag the active pack binds (e.g. pump P-3), with the pack id, version and snapshot printed on it
  * a notice for retired cards: until 2026-09-26 pump P-3's tag was id 3, which is now the owner key.
    Any old card labelled P-3 with id 3 must be destroyed or relabelled."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

CM = 72 / 2.54


def marker_png(mid: int, out: Path) -> Path:
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = cv2.aruco.generateImageMarker(d, mid, 600)
    img = cv2.copyMakeBorder(img, 100, 100, 100, 100, cv2.BORDER_CONSTANT, value=255)
    cv2.imwrite(str(out), img)
    return out


def main(out_dir: Path | None = None) -> Path:
    import pymupdf
    pack = config.PACK
    out_dir = out_dir or (config.ASSETS / "print")
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((48, 58), f"JAMES markers  ·  pack {pack.id} {pack.version}  ·  snapshot {pack.snapshot}", fontsize=14)
    page.insert_text((48, 76), f"Printed {date.today().isoformat()}. Print at 100% (actual size). Keep the white border.",
                     fontsize=10)
    y = 100

    def block(mid: int, cm: float, title: str, lines: list[str]):
        nonlocal y
        png = marker_png(mid, out_dir / f"aruco_{mid}.png")
        full = cm * CM * 8 / 6
        r = pymupdf.Rect(48, y, 48 + full, y + full)
        page.insert_image(r, filename=str(png))
        page.draw_rect(r, color=(0.7, 0.7, 0.7), width=0.5)
        page.insert_text((48 + full + 18, y + 18), title, fontsize=13)
        for k, ln in enumerate(lines):
            page.insert_text((48 + full + 18, y + 38 + 15 * k), ln, fontsize=9)
        y += max(full, 40 + 15 * len(lines)) + 26

    block(config.OWNER_KEY_ID, 3, f"OWNER KEY  ·  id {config.OWNER_KEY_ID}  ·  NOT A MACHINE TAG",
          ["Wear it on the hand that drives JAMES.", "Anyone holding a copy can drive JAMES: a visual filter,",
           "not a password. JAMES never treats this id as a machine."])
    for mid, asset in sorted(pack.asset_tags().items()):
        a = pack.asset(asset)
        block(mid, 6, f"MACHINE  {asset}  ·  id {mid}",
              [a.label if a else asset, f"pack {pack.id} {pack.version}, snapshot {pack.snapshot}",
               "Stick flat on the machine, at eye height."])
    page.insert_text((48, y + 10), "RETIRED CARDS", fontsize=12)
    page.insert_text((48, y + 28), f"Until 2026-09-26 pump P-3's tag was id 3. Id {config.OWNER_KEY_ID} is now the owner key.",
                     fontsize=9)
    page.insert_text((48, y + 43), "Destroy or relabel any old card marked P-3 with id 3. JAMES shows a warning if it sees one.",
                     fontsize=9)
    pdf = out_dir / f"markers_{pack.id}-{pack.version}.pdf"
    doc.save(pdf)
    print("wrote", pdf)
    return pdf


if __name__ == "__main__":
    main()
