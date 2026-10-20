"""Draw the CANTUS launcher icon (assets/icons/internal_cantus.png): a red and a blue block, each cut by a swing,
on a dark tile. Run once: python -m tools.make_cantus_icon"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "assets" / "icons" / "internal_cantus.png"
S = 4                                    # draw 4x, then downsample


def main():
    n = 128 * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6 * S, 6 * S, n - 6 * S, n - 6 * S), radius=26 * S, fill=(13, 20, 23, 255),
                        outline=(42, 60, 64, 255), width=2 * S)
    red, blue, white = (232, 80, 91, 255), (76, 141, 255, 255), (234, 244, 244, 255)
    for (x, y, col, up) in ((30, 40, red, False), (68, 54, blue, True)):
        x, y, w = x * S, y * S, 32 * S
        d.rectangle((x, y, x + w, y + w), fill=tuple(int(c * 0.3) for c in col[:3]) + (255,), outline=col, width=3 * S)
        cx = x + w / 2
        if up:
            d.polygon([(cx, y + 8 * S), (cx - 9 * S, y + 20 * S), (cx + 9 * S, y + 20 * S)], fill=white)
        else:
            d.polygon([(cx, y + w - 8 * S), (cx - 9 * S, y + w - 20 * S), (cx + 9 * S, y + w - 20 * S)], fill=white)
    d.line([(22 * S, 104 * S), (106 * S, 24 * S)], fill=white, width=3 * S)              # the swing
    im = im.resize((128, 128), Image.LANCZOS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
