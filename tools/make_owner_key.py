"""Print the JAMES owner key: an ArUco marker (DICT_4X4_50, id config.OWNER_KEY_ID).

    python tools/make_owner_key.py

Writes assets/owner_key_<id>.png (show it on a phone to test) and assets/owner_key_<id>.pdf (A4, print at
100% / "actual size", NOT "fit to page"). The PDF has the key at 2, 3 and 4 cm. Start with 3 cm: stick it
on the watch strap or the back of the hand, flat, with its white border left on (the border is part of
the marker). Glossy tape over it causes glare; matte paper works best.

Anyone holding a copy of this key can drive JAMES. It keeps other hands in view out; it is not a password."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

CM = 72 / 2.54                      # PDF points per cm


def marker_png(key_id: int, out: Path) -> Path:
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    img = cv2.aruco.generateImageMarker(d, key_id, 600)            # 6x6 cells, 100 px each (black edge included)
    img = cv2.copyMakeBorder(img, 100, 100, 100, 100, cv2.BORDER_CONSTANT, value=255)   # one-cell white quiet zone
    cv2.imwrite(str(out), img)
    return out


def main():
    import pymupdf
    key_id = config.OWNER_KEY_ID
    A = config.ASSETS
    png = marker_png(key_id, A / f"owner_key_{key_id}.png")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((56, 70), f"JAMES owner key  (ArUco 4x4, id {key_id})", fontsize=18)
    page.insert_text((56, 94), "Print at 100% (actual size). Cut along the grey line, keep the white border.", fontsize=11)
    page.insert_text((56, 110), "Wear one on the watch strap or the back of the hand. Start with the 3 cm key.", fontsize=11)
    x = 56
    for cm in (2, 3, 4):
        black = cm * CM                  # the black square is `cm` wide
        full = black * 8 / 6             # plus one white cell on each side (the png has that border)
        y = 150
        r = pymupdf.Rect(x, y, x + full, y + full)
        page.insert_image(r, filename=str(png))
        page.draw_rect(r, color=(0.7, 0.7, 0.7), width=0.5)
        page.insert_text((x, y + full + 18), f"{cm} cm", fontsize=11)
        x += full + 30
    page.insert_text((56, 420), "Anyone holding a copy of this key can drive JAMES. It keeps other hands out; it is not a password.",
                     fontsize=9)
    pdf = A / f"owner_key_{key_id}.pdf"
    doc.save(pdf)
    print("wrote", png)
    print("wrote", pdf)


if __name__ == "__main__":
    main()
