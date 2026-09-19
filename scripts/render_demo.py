"""Render docs/demo/*.json casts into animated GIFs and static SVG "screenshots".

    uv run python scripts/render_demo.py             # all casts -> docs/assets/demo-<name>.gif / .svg
    uv run python scripts/render_demo.py jsort jroute

The GIF is drawn with Pillow from the recorded output, typed out command first, lines appearing
at their recorded times (sped up to keep files small). The SVG is a static, crisp rendering of
the same terminal for places that do not animate. No screen recorder, no fonts beyond DejaVu.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DEMOS = ROOT / "docs" / "demo"
ASSETS = ROOT / "docs" / "assets"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

COLS, PAD, LINE_H, CHAR_W, FONT_SIZE = 96, 18, 22, 9.6, 15
BG, FG, DIM, GREEN, YELLOW, RED, PROMPT = "#0f1419", "#e6e6e6", "#8a8f98", "#6fd08c", "#e5c07b", "#e06c75", "#7aa2f7"
TITLE_BG = "#1a1f26"
SPEED = 2.0  # recorded seconds per GIF second

_SCORE = re.compile(r"^(\d\.\d{3})\t")
_JSON_LINE = re.compile(r'^\{"')


def font(bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT, FONT_SIZE)


def colour_for_score(p: float) -> str:
    return GREEN if p >= 0.75 else YELLOW if p >= 0.4 else RED


def wrap(text: str, width: int = COLS) -> list[str]:
    text = text.replace("\t", "  ")
    return [text[i : i + width] for i in range(0, max(len(text), 1), width)]


def frame_size(rows: int) -> tuple[int, int]:
    return int(COLS * CHAR_W + PAD * 2), int(rows * LINE_H + PAD * 2 + 30)


def draw_terminal(lines: list[tuple[str, str]], rows: int, title: str) -> Image.Image:
    """lines: (kind, text) where kind is prompt|cmd|out|err|score."""
    w, h = frame_size(rows)
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 30], fill=TITLE_BG)
    for i, c in enumerate((RED, YELLOW, GREEN)):
        d.ellipse([12 + i * 20, 9, 24 + i * 20, 21], fill=c)
    d.text((80, 7), title, fill=DIM, font=font())
    y = 30 + PAD
    for kind, text in lines[-rows:]:
        x = PAD
        if kind == "cmd":
            d.text((x, y), "$ ", fill=PROMPT, font=font(True))
            d.text((x + 2 * CHAR_W, y), text, fill=FG, font=font(True))
        elif kind == "err":
            d.text((x, y), text, fill=DIM, font=font())
        elif kind == "out":
            m = _SCORE.match(text)
            if m:
                p = float(m.group(1))
                d.text((x, y), m.group(1), fill=colour_for_score(p), font=font(True))
                d.text((x + 7 * CHAR_W, y), text[len(m.group(0)) :].replace("\t", "  "), fill=FG, font=font())
            elif _JSON_LINE.match(text):
                d.text((x, y), text, fill=YELLOW, font=font())
            else:
                d.text((x, y), text.replace("\t", "   "), fill=FG, font=font())
        y += LINE_H
    return img


def render_gif(scene: dict, path: Path) -> None:
    cmd = scene["command"]
    out_events = scene["stdout"]
    err_lines = scene["stderr"]
    rows = min(22, max(8, 3 + sum(len(wrap(t)) for _, t in out_events) + len(err_lines)))
    frames: list[Image.Image] = []
    durations: list[int] = []
    # type the command
    for i in range(0, len(cmd) + 1, 2):
        frames.append(draw_terminal([("cmd", cmd[:i] + ("▌" if i < len(cmd) else ""))], rows, scene["title"]))
        durations.append(35)
    lines: list[tuple[str, str]] = [("cmd", cmd)]
    frames.append(draw_terminal(lines, rows, scene["title"]))
    durations.append(500)
    last_t = 0.0
    for at, text in out_events:
        gap = max(0.0, float(at) - last_t) / SPEED
        durations[-1] = max(durations[-1], int(gap * 1000)) if frames else int(gap * 1000)
        for piece in wrap(text):
            lines.append(("out", piece))
        frames.append(draw_terminal(lines, rows, scene["title"]))
        durations.append(120)
        last_t = float(at)
    for text in err_lines:
        for piece in wrap(text):
            lines.append(("err", piece))
    frames.append(draw_terminal(lines, rows, scene["title"]))
    durations.append(3500)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg(scene: dict, path: Path) -> None:
    cmd = scene["command"]
    body: list[tuple[str, str]] = [("cmd", cmd)]
    for _, text in scene["stdout"]:
        body.extend(("out", piece) for piece in wrap(text))
    body.extend(("err", piece) for text in scene["stderr"] for piece in wrap(text))
    rows = len(body)
    w, h = frame_size(rows)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="DejaVu Sans Mono, Menlo, Consolas, monospace" font-size="{FONT_SIZE}px">',
        f'<rect width="{w}" height="{h}" rx="10" fill="{BG}"/>',
        f'<rect width="{w}" height="30" fill="{TITLE_BG}"/>',
        f'<circle cx="18" cy="15" r="6" fill="{RED}"/><circle cx="38" cy="15" r="6" fill="{YELLOW}"/><circle cx="58" cy="15" r="6" fill="{GREEN}"/>',
        f'<text x="80" y="20" fill="{DIM}">{esc(scene["title"])}</text>',
    ]
    y = 30 + PAD + FONT_SIZE
    for kind, text in body:
        if kind == "cmd":
            parts.append(f'<text x="{PAD}" y="{y}" fill="{PROMPT}" font-weight="bold">$</text>')
            parts.append(
                f'<text x="{PAD + 2 * CHAR_W}" y="{y}" fill="{FG}" font-weight="bold" xml:space="preserve">{esc(text)}</text>'
            )
        elif kind == "err":
            parts.append(f'<text x="{PAD}" y="{y}" fill="{DIM}" xml:space="preserve">{esc(text)}</text>')
        else:
            m = _SCORE.match(text.replace("  ", "\t", 1)) if not text.startswith("{") else None
            m = _SCORE.match(scene_line_original(text)) if m is None and not text.startswith("{") else m
            if m:
                p = float(m.group(1))
                parts.append(
                    f'<text x="{PAD}" y="{y}" fill="{colour_for_score(p)}" font-weight="bold">{m.group(1)}</text>'
                )
                parts.append(
                    f'<text x="{PAD + 7 * CHAR_W}" y="{y}" fill="{FG}" xml:space="preserve">{esc(text[len(m.group(1)) :].strip())}</text>'
                )
            else:
                fill = YELLOW if text.startswith("{") else FG
                parts.append(f'<text x="{PAD}" y="{y}" fill="{fill}" xml:space="preserve">{esc(text)}</text>')
        y += LINE_H
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n")


def scene_line_original(text: str) -> str:
    return text.replace("  ", "\t", 1)


def main(names: list[str]) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    casts = sorted(DEMOS.glob("*.json"))
    if names:
        casts = [DEMOS / f"{n}.json" for n in names]
    for cast in casts:
        scene = json.loads(cast.read_text())
        render_gif(scene, ASSETS / f"demo-{scene['name']}.gif")
        render_svg(scene, ASSETS / f"demo-{scene['name']}.svg")
        print(f"rendered {scene['name']}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
