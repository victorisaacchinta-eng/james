"""Draw the ATLAS launcher icon (assets/icons/internal_atlas.png): an exploded cube in JAMES cyan on a dark tile.
Run once: python -m tools.make_atlas_icon"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "assets" / "icons" / "internal_atlas.png"
S = 4                                    # draw 4x, then downsample: smooth edges without a vector library


def iso(x, y, z, cx, cy, k):
    return cx + (x - z) * 0.866 * k, cy + ((x + z) * 0.5 - y) * k


def main():
    n = 128 * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6 * S, 6 * S, n - 6 * S, n - 6 * S), radius=26 * S, fill=(13, 20, 23, 255),
                        outline=(42, 60, 64, 255), width=2 * S)
    cyan, soft, teal = (53, 224, 208, 255), (104, 230, 224, 255), (42, 184, 176, 200)
    cx, cy, k = n / 2, n / 2 + 6 * S, 17 * S

    def face(pts, col, off):
        p = [iso(x + off[0], y + off[1], z + off[2], cx, cy, k) for x, y, z in pts]
        d.line(p + [p[0]], fill=col, width=3 * S, joint="curve")

    face([(-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1)], cyan, (0, 1.15, 0))          # top, lifted
    face([(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], soft, (0, 0, 0.55))           # front, pulled out
    face([(1, -1, -1), (1, -1, 1), (1, 1, 1), (1, 1, -1)], soft, (0.55, 0, 0))           # side, pulled out
    for a, b in (((1, 1, 1), (1, 1 + 1.15, 1)), ((-1, 1, 1), (-1, 1 + 1.15, 1)), ((1, 1, -1), (1, 1 + 1.15, -1))):
        pa, pb = iso(*a, cx, cy, k), iso(*b, cx, cy, k)
        d.line([pa, pb], fill=teal, width=S)                                             # travel lines
    im = im.resize((128, 128), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
