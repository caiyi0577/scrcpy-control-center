"""Generate the Windows ICO from the project's icon design."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


SIZES = (16, 24, 32, 48, 64, 128, 256)


def make_icon(size: int) -> Image.Image:
    scale = 4
    canvas = 256 * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    p = lambda value: int(value * scale)

    draw.rounded_rectangle((p(10), p(10), p(246), p(246)), radius=p(56), fill="#0b1224")
    draw.rounded_rectangle((p(16), p(16), p(240), p(240)), radius=p(50), fill="#1a2b56", outline="#7bdbff", width=p(5))
    draw.line((p(55), p(104), p(42), p(104)), fill="#c9f5ff", width=p(6))
    draw.line((p(201), p(104), p(214), p(104)), fill="#c9f5ff", width=p(6))
    draw.polygon([(p(48), p(97)), (p(41), p(104)), (p(48), p(111))], fill="#c9f5ff")
    draw.polygon([(p(208), p(97)), (p(215), p(104)), (p(208), p(111))], fill="#c9f5ff")

    draw.rounded_rectangle((p(67), p(34), p(189), p(222)), radius=p(27), fill="#f4fbff")
    screen_gradient = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(screen_gradient)
    for y in range(p(51), p(179)):
        ratio = (y - p(51)) / max(1, p(127))
        if ratio < 0.52:
            t = ratio / 0.52
            color = tuple(round(a + (b - a) * t) for a, b in zip((103, 216, 255), (78, 124, 255)))
        else:
            t = (ratio - 0.52) / 0.48
            color = tuple(round(a + (b - a) * t) for a, b in zip((78, 124, 255), (118, 88, 219)))
        gradient_draw.line((p(79), y, p(177), y), fill=color, width=1)
    screen_mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(screen_mask).rounded_rectangle((p(79), p(51), p(177), p(178)), radius=p(15), fill=255)
    image.paste(screen_gradient, (0, 0), screen_mask)
    draw = ImageDraw.Draw(image)
    draw.ellipse((p(124), p(39), p(132), p(47)), fill="#a5bacd")
    draw.line((p(99), p(107), p(121), p(107)), fill="#effcff", width=p(7))
    draw.line((p(110), p(96), p(110), p(118)), fill="#effcff", width=p(7))
    draw.line((p(135), p(131), p(155), p(131)), fill="#effcff", width=p(7))
    draw.line((p(145), p(121), p(145), p(141)), fill="#effcff", width=p(7))
    draw.rounded_rectangle((p(79), p(187), p(177), p(210)), radius=p(12), fill="#d8e8f3")
    for x in (101, 128, 155):
        draw.ellipse((p(x - 5), p(193.5), p(x + 5), p(203.5)), fill="#4168db")
    draw.ellipse((p(200), p(41), p(222), p(63)), fill="#72e0a8", outline="#0b1224", width=p(5))
    draw.line((p(207), p(52), p(210), p(55), p(216), p(48)), fill="#102039", width=p(3))
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "assets" / "scrcpy-control-center.ico",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    make_icon(1024).save(args.output, format="ICO", sizes=[(size, size) for size in SIZES])
    print(f"Generated {args.output}")


if __name__ == "__main__":
    main()
