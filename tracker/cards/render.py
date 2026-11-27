"""Link preview cards (1200x630 PNG) drawn from the site's palette and bundled fonts."""

import io
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from beacon_contract.v1 import ComplianceLabel
from tracker.display import LABEL_TEXT

WIDTH, HEIGHT, MARGIN = 1200, 630, 88
CONTENT = WIDTH - 2 * MARGIN
FONTS = Path(__file__).parent.parent / "static" / "fonts"

BG = "#1c1917"
INK = "#fafaf9"
MUTED = "#a8a29e"
FAINT = "#78716c"
TRACK = "#292524"
ACCENT = "#4b6584"
# Status fills from site.css, each with an ink that keeps text on it legible.
STATUS = {
    ComplianceLabel.COMPLIANT: ("#166534", INK),
    ComplianceLabel.PROVISIONAL: ("#d97706", BG),
    ComplianceLabel.NON_COMPLIANT: ("#991b1b", INK),
    None: ("#44403c", INK),
}


@dataclass(frozen=True)
class Figures:
    """A committee, chamber, or the whole session: what the dashboard's scorecard shows."""

    title: str
    rate: float
    total: int
    compliant: int
    provisional: int
    non_compliant: int
    as_of: str
    subtitle: str | None = None


@dataclass(frozen=True)
class Segment:
    committee: str
    label: ComplianceLabel | None  # None: the referral owed nothing, so it has no status


@dataclass(frozen=True)
class Bill:
    bill_id: str
    kicker: str
    title: str
    segments: Sequence[Segment]
    as_of: str


@cache
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(
        str(FONTS / f"{name}.woff2"), size, layout_engine=ImageFont.Layout.BASIC
    )


def _serif(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    return _font(f"merriweather-latin-{weight}-normal", size)


def _sans(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    return _font(f"inter-latin-{weight}-normal", size)


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int, lines: int
) -> list[str]:
    """Greedy word wrap to at most `lines` lines, ending in an ellipsis if text is left over."""
    words, out = text.split(), []
    while words and len(out) < lines:
        line = words.pop(0)
        while words and draw.textlength(f"{line} {words[0]}", font=font) <= width:
            line = f"{line} {words.pop(0)}"
        out.append(line)
    if words or (out and draw.textlength(out[-1], font=font) > width):
        last = out[-1]
        while last and draw.textlength(f"{last}…", font=font) > width:
            last = last[:-1]
        out[-1] = last.rstrip(" ,;:") + "…"
    return out


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Background plus the brand row: the favicon's T mark and the site name."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    size, x, y = 48, MARGIN, 64
    s = size / 32
    draw.rounded_rectangle([x, y, x + size, y + size], radius=4 * s, fill=INK)
    t_mark = [(9, 9), (23, 9), (23, 12), (17.5, 12), (17.5, 24), (14.5, 24), (14.5, 12), (9, 12)]
    draw.polygon([(x + a * s, y + b * s) for a, b in t_mark], fill=BG)
    draw.text(
        (x + size + 20, y + size / 2),
        "BEACON HILL COMPLIANCE TRACKER",
        font=_sans(22, 600),
        fill=INK,
        anchor="lm",
    )
    return img, draw


def _footer(draw: ImageDraw.ImageDraw, as_of: str) -> None:
    draw.text((MARGIN, HEIGHT - 56), f"Data as of {as_of}", font=_sans(24), fill=FAINT, anchor="ls")


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def figures_card(f: Figures) -> bytes:
    img, draw = _canvas()
    y = 168
    for line in _wrap(draw, f.title, _serif(48), CONTENT, 2):
        draw.text((MARGIN, y), line, font=_serif(48), fill=INK)
        y += 64
    if f.subtitle:
        draw.text((MARGIN, y + 4), f.subtitle, font=_sans(26), fill=MUTED)

    # Headline rate, as on the dashboard scorecard, with its definition beside it.
    rate_font, base = _serif(112, 900), 448
    rate = f"{f.rate:.1f}%"
    draw.text((MARGIN, base), rate, font=rate_font, fill=INK, anchor="ls")
    x = MARGIN + draw.textlength(rate, font=rate_font) + 32
    for i, line in enumerate(("of bill segments with", "no detected violation")):
        draw.text((x, base - 46 + i * 38), line, font=_sans(30), fill=MUTED, anchor="ls")

    # Stacked bar of the three labels, then a legend with the counts.
    bar_y, bar_h = base + 28, 18
    draw.rectangle([MARGIN, bar_y, MARGIN + CONTENT, bar_y + bar_h], fill=TRACK)
    parts = (
        (ComplianceLabel.COMPLIANT, f.compliant),
        (ComplianceLabel.PROVISIONAL, f.provisional),
        (ComplianceLabel.NON_COMPLIANT, f.non_compliant),
    )
    x = float(MARGIN)
    for label, count in parts:
        w = CONTENT * count / f.total if f.total else 0
        if w:
            draw.rectangle([round(x), bar_y, round(x + w), bar_y + bar_h], fill=STATUS[label][0])
        x += w
    x, legend_y = float(MARGIN), bar_y + bar_h + 34
    for label, count in parts:
        draw.rectangle([x, legend_y - 18, x + 14, legend_y - 4], fill=STATUS[label][0])
        text = f"{count:,} {LABEL_TEXT[label].lower()}"
        draw.text((x + 24, legend_y), text, font=_sans(24, 500), fill=INK, anchor="ls")
        x += 24 + draw.textlength(text, font=_sans(24, 500)) + 36
    _footer(draw, f.as_of)
    return _png(img)


def bill_card(b: Bill) -> bytes:
    img, draw = _canvas()
    # A larger title when it fits in two lines; long titles drop a size and end in an ellipsis.
    title_font, line_h = _serif(56), 74
    title = _wrap(draw, b.title, title_font, CONTENT, 2)
    if title[-1].endswith("…"):
        title_font, line_h = _serif(44), 60
        title = _wrap(draw, b.title, title_font, CONTENT, 2)

    # One row per committee: a status pill, then the committee's name.
    # Three rows fit; with more, the third makes room for an "and N more" line.
    shown = b.segments[:3] if len(b.segments) <= 3 else b.segments[:2]
    hidden = len(b.segments) - len(shown)
    rows = len(shown) + (1 if hidden else 0)

    # Centre the kicker, title, and rows between the brand row and the footer.
    height = 44 + len(title) * line_h + 32 + rows * 54 - 14
    y = 136 + (390 - height) // 2
    draw.text((MARGIN, y), b.kicker, font=_sans(26, 600), fill=MUTED)
    y += 44
    for line in title:
        draw.text((MARGIN, y), line, font=title_font, fill=INK)
        y += line_h
    rows_y = y + 32
    pill_font, name_font = _sans(22, 600), _sans(28)
    pill_w = (
        max(
            draw.textlength(LABEL_TEXT[s.label] if s.label else "No status", font=pill_font)
            for s in shown
        )
        + 36
    )
    for s in shown:
        fill, ink = STATUS[s.label]
        text = LABEL_TEXT[s.label] if s.label else "No status"
        draw.rounded_rectangle([MARGIN, rows_y, MARGIN + pill_w, rows_y + 40], radius=20, fill=fill)
        draw.text((MARGIN + pill_w / 2, rows_y + 20), text, font=pill_font, fill=ink, anchor="mm")
        name_x = MARGIN + pill_w + 20
        name = _wrap(draw, s.committee, name_font, WIDTH - MARGIN - name_x, 1)[0]
        draw.text((name_x, rows_y + 20), name, font=name_font, fill=INK, anchor="lm")
        rows_y += 54
    if hidden:
        more = f"and {hidden} more {'committee' if hidden == 1 else 'committees'}"
        draw.text((MARGIN, rows_y + 20), more, font=_sans(26), fill=MUTED, anchor="lm")
    _footer(draw, b.as_of)
    return _png(img)
