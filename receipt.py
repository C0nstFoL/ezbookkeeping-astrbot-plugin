"""Render an ezBookkeeping receipt as a paper-ledger style PNG."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "wqy-microhei.ttc"

WIDTH = 820
MARGIN = 64

PAPER = "#FBF7EE"
PAPER_EDGE = "#E6DECB"
INK = "#33302B"
MUTED = "#8A8377"
RULE = "#DED5C1"
BINDING = "#C7B79B"

KIND_STYLE = {
    "收入": ("#2E7D5B", "#E7F2EC"),
    "支出": ("#B5432F", "#F6E4DF"),
    "转账": ("#2F5D8A", "#E3ECF5"),
}
DEFAULT_STYLE = ("#5A5348", "#EFE9DC")


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Wrap text by measured pixel width, keeping explicit line breaks."""
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = char
            else:
                current = candidate
        lines.append(current)
    return lines


def render_receipt_png(
    *,
    kind: str,
    amount: str,
    rows: list[tuple[str, str]],
    note: str = "",
    transaction_id: str = "",
    date_text: str = "",
) -> bytes:
    """Render one receipt card and return PNG bytes.

    kind: 收入 / 支出 / 转账; amount: the large highlighted amount line;
    rows: label/value pairs such as (账户, 微信钱包); note: optional comment.
    """
    accent, badge_bg = KIND_STYLE.get(kind, DEFAULT_STYLE)
    title_font = _font(30)
    amount_font = _font(64)
    label_font = _font(26)
    value_font = _font(28)
    small_font = _font(20)
    badge_font = _font(26)

    row_height = 54
    line_height = 40
    content_width = WIDTH - 2 * MARGIN
    label_col = 150
    value_width = content_width - label_col

    probe = ImageDraw.Draw(Image.new("RGB", (WIDTH, 10)))

    row_specs = [
        (label, _wrap(probe, value, value_font, value_width)) for label, value in rows
    ]
    note_lines = _wrap(probe, note, value_font, content_width) if note else []

    top = 56
    header_h = 44
    gap_after_header = 28
    amount_h = 108
    gap_after_amount = 12

    rows_h = sum(
        max(row_height, len(lines) * line_height + 16) for _, lines in row_specs
    )
    note_h = len(note_lines) * line_height + 16 if note_lines else 0
    divider_gap = 30
    footer_h = 78
    height = (
        top
        + header_h
        + gap_after_header
        + amount_h
        + gap_after_amount
        + rows_h
        + note_h
        + divider_gap
        + footer_h
        + 24
    )
    height = max(height, 520)

    img = Image.new("RGB", (WIDTH, height), PAPER)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, WIDTH - 1, height - 1], outline=PAPER_EDGE, width=2)

    # Left binding lines (notebook margin).
    draw.line([(MARGIN - 34, top), (MARGIN - 34, height - 40)], fill=BINDING, width=2)
    draw.line([(MARGIN - 26, top), (MARGIN - 26, height - 40)], fill=RULE, width=1)

    # Header: kind badge on the left, date on the right.
    badge_w = int(draw.textlength(kind, font=badge_font)) + 34
    draw.rounded_rectangle(
        [MARGIN, top, MARGIN + badge_w, top + 40], radius=10, fill=badge_bg
    )
    draw.text((MARGIN + 17, top + 6), kind, font=badge_font, fill=accent)
    if date_text:
        draw.text(
            (WIDTH - MARGIN - draw.textlength(date_text, font=title_font), top + 4),
            date_text,
            font=title_font,
            fill=MUTED,
        )

    # Highlighted amount with a heavier rule beneath it.
    amount_y = top + header_h + gap_after_header
    draw.text((MARGIN, amount_y), amount, font=amount_font, fill=accent)
    draw.line(
        [
            (MARGIN, amount_y + amount_h - 22),
            (WIDTH - MARGIN, amount_y + amount_h - 22),
        ],
        fill=RULE,
        width=2,
    )

    # Ledger rows, each closed by a thin rule.
    row_y = amount_y + amount_h + gap_after_amount
    for label, lines in row_specs:
        block_h = max(row_height, len(lines) * line_height + 16)
        draw.text((MARGIN, row_y + 12), label, font=label_font, fill=MUTED)
        text_y = row_y + 12
        for line in lines:
            draw.text((MARGIN + label_col, text_y), line, font=value_font, fill=INK)
            text_y += line_height
        row_y += block_h
        draw.line([(MARGIN, row_y), (WIDTH - MARGIN, row_y)], fill=RULE, width=1)

    # Optional comment block.
    if note_lines:
        draw.text((MARGIN, row_y + 12), "备注", font=label_font, fill=MUTED)
        text_y = row_y + 12
        for line in note_lines:
            draw.text((MARGIN + label_col, text_y), line, font=value_font, fill=INK)
            text_y += line_height
        row_y += note_h
        draw.line([(MARGIN, row_y), (WIDTH - MARGIN, row_y)], fill=RULE, width=1)

    # Dashed divider above the footer.
    divider_y = row_y + divider_gap
    dash = 14
    x = MARGIN
    while x < WIDTH - MARGIN:
        draw.line(
            [(x, divider_y), (min(x + dash, WIDTH - MARGIN), divider_y)],
            fill=RULE,
            width=2,
        )
        x += dash * 2

    if transaction_id:
        text = f"交易编号 {transaction_id}"
        draw.text(
            ((WIDTH - draw.textlength(text, font=small_font)) / 2, divider_y + 24),
            text,
            font=small_font,
            fill=MUTED,
        )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
