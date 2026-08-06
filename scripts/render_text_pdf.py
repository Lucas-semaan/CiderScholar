"""Render extracted document text into a searchable, page-addressable PDF."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

import fitz

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN = 48
FONT_SIZE = 10
LINE_HEIGHT = 13
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 text extracted from the source document")
    parser.add_argument("output", type=Path, help="Destination PDF")
    parser.add_argument("--title", required=True, help="Original document name")
    parser.add_argument("--source-path", required=True, help="Original local source path")
    return parser


def _font_file() -> Path | None:
    candidates = (
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "arial.ttf",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "calibri.ttf",
    )
    return next((path for path in candidates if path.is_file()), None)


def _wrapped_lines(text: str, font: fitz.Font, maximum_width: float) -> list[str]:
    normalized = CONTROL_CHARACTERS.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    rendered: list[str] = []
    for paragraph in normalized.split("\n"):
        words = paragraph.replace("\t", " ").split()
        if not words:
            rendered.append("")
            continue
        line = words[0]
        for word in words[1:]:
            candidate = f"{line} {word}"
            if font.text_length(candidate, fontsize=FONT_SIZE) <= maximum_width:
                line = candidate
                continue
            rendered.append(line)
            line = word
        while font.text_length(line, fontsize=FONT_SIZE) > maximum_width and len(line) > 1:
            split_at = max(
                1, int(len(line) * maximum_width / font.text_length(line, fontsize=FONT_SIZE))
            )
            rendered.append(line[:split_at])
            line = line[split_at:]
        rendered.append(line)
    return rendered


def render_text_pdf(
    text: str,
    destination: Path,
    *,
    title: str,
    source_path: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    font_file = _font_file()
    font = fitz.Font(fontfile=str(font_file)) if font_file is not None else fitz.Font("helv")
    lines = _wrapped_lines(text, font, PAGE_WIDTH - 2 * MARGIN)
    lines_per_page = int((PAGE_HEIGHT - 2 * MARGIN) // LINE_HEIGHT)
    if not lines:
        lines = [""]

    document = fitz.open()
    try:
        for offset in range(0, len(lines), lines_per_page):
            page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            if font_file is not None:
                page.insert_font(fontname="document-font", fontfile=str(font_file))
                font_name = "document-font"
            else:
                font_name = "helv"
            for line_index, line in enumerate(lines[offset : offset + lines_per_page]):
                page.insert_text(
                    (MARGIN, MARGIN + FONT_SIZE + line_index * LINE_HEIGHT),
                    line,
                    fontname=font_name,
                    fontsize=FONT_SIZE,
                )
        document.set_metadata(
            {
                "title": title,
                "subject": f"Converted from {source_path}",
                "creator": "CiderScholar local document converter",
            }
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}.", suffix=".pdf", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            document.save(temporary, garbage=4, deflate=True)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    finally:
        document.close()


def main() -> int:
    args = build_parser().parse_args()
    text = args.input.read_text(encoding="utf-8-sig", errors="replace")
    render_text_pdf(
        text,
        args.output,
        title=args.title,
        source_path=args.source_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
