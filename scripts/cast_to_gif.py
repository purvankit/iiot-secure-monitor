"""Render an asciinema v2 recording (.cast) to an animated GIF with Pillow.

Small on purpose: plain text, ANSI colours (SGR codes), newlines, carriage returns and
scrolling. Enough for line-oriented demo output, not a full terminal emulator. Idle gaps
are capped so the GIF stays short; the speed-up is printed and should be stated wherever
the GIF is shown.

Usage: python scripts/cast_to_gif.py demo.cast docs/demo.gif [--max-idle 1.5] [--speed 1.0]
"""

from __future__ import annotations

import argparse
import json
import re

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
BG, FG = (13, 17, 23), (201, 209, 217)
SGR = {"31": (248, 81, 73), "33": (210, 153, 34), "32": (63, 185, 80), "36": (57, 197, 207),
       "1;31": (255, 123, 114), "0": FG, "": FG}
ESC = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")


class Screen:
    def __init__(self, rows: int):
        self.rows, self.lines, self.color = rows, [[]], FG

    def feed(self, data: str) -> None:
        pos = 0
        for m in ESC.finditer(data):
            self._text(data[pos:m.start()])
            if m.group(2) == "m":
                self.color = SGR.get(m.group(1), FG)
            pos = m.end()
        self._text(data[pos:])

    def _text(self, s: str) -> None:
        for ch in s:
            if ch == "\n":
                self.lines.append([])
            elif ch == "\r":
                pass  # recordings use \r\n; a bare \r redraw isn't needed for this demo
            elif ch >= " ":
                self.lines[-1].append((ch, self.color))
        self.lines = self.lines[-self.rows:]


def render(screen: Screen, cols: int, font, cw: int, ch: int) -> Image.Image:
    img = Image.new("RGB", (cols * cw + 24, screen.rows * ch + 24), BG)
    d = ImageDraw.Draw(img)
    for r, line in enumerate(screen.lines):
        for c, (char, color) in enumerate(line[:cols]):
            d.text((12 + c * cw, 12 + r * ch), char, font=font, fill=color)
    return img


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cast")
    p.add_argument("gif")
    p.add_argument("--max-idle", type=float, default=1.5, help="cap pauses at N seconds")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--font-size", type=int, default=15)
    a = p.parse_args()

    with open(a.cast) as f:
        header = json.loads(f.readline())
        events = [json.loads(line) for line in f if line.strip()]
    cols, rows = header["width"], header["height"]
    font = ImageFont.truetype(FONT, a.font_size)
    cw = int(font.getlength("M")) + 1
    chh = int(a.font_size * 1.35)

    screen, frames, durations = Screen(rows), [], []
    last_t, shown_t, real_end = 0.0, 0.0, 0.0
    for t, kind, data in events:
        if kind != "o":
            continue
        gap = min(t - last_t, a.max_idle) / a.speed
        if frames and gap >= 0.05:  # one frame per burst of output
            durations[-1] = max(int(gap * 1000), 20)
            shown_t += gap
        screen.feed(data)
        if not frames or gap >= 0.05:
            frames.append(render(screen, cols, font, cw, chh))
            durations.append(100)
        else:
            frames[-1] = render(screen, cols, font, cw, chh)
        last_t, real_end = t, t
    durations[-1] = 4000  # hold the final screen
    frames[0].save(a.gif, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=True)
    print(f"{a.gif}: {len(frames)} frames, {real_end:.0f} s recorded -> "
          f"{shown_t + 4:.0f} s shown (pauses capped at {a.max_idle} s, speed x{a.speed})")


if __name__ == "__main__":
    main()
