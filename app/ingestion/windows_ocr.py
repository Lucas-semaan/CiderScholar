"""Optional offline OCR adapter backed by the French Windows OCR engine."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import fitz

from app.ingestion.pdf_extractor import ExtractedDocument, OcrPageTrace, PageText

_WORD_PATTERN = re.compile(r"\b[^\W\d_][^\W_]{1,}\b", re.UNICODE)


def ocr_text_confidence(text: str) -> float:
    """Deterministic text-quality proxy; Windows OCR exposes no word confidence."""

    cleaned = text.strip()
    if not cleaned:
        return 0.0
    printable_ratio = sum(character.isprintable() for character in cleaned) / len(cleaned)
    alphanumeric_ratio = sum(character.isalnum() for character in cleaned) / len(cleaned)
    tokens = cleaned.split()
    word_ratio = (
        sum(bool(_WORD_PATTERN.fullmatch(token.strip(".,;:!?()[]{}"))) for token in tokens)
        / len(tokens)
        if tokens
        else 0.0
    )
    length_score = min(len(cleaned) / 80.0, 1.0)
    score = (
        0.25 * printable_ratio
        + 0.30 * min(alphanumeric_ratio / 0.70, 1.0)
        + 0.25 * word_ratio
        + 0.20 * length_score
    )
    return round(min(max(score, 0.0), 1.0), 6)


class WindowsOcrPdfExtractor:
    """Render image-only pages locally and recognize them without a network service."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        min_page_text_characters: int = 25,
        language: str = "fr-FR",
        min_confidence: float = 0.75,
        scale: float = 2.0,
        powershell_executable: str = "powershell.exe",
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows OCR is available on Windows only")
        if scale <= 0:
            raise ValueError("OCR rendering scale must be positive")
        if not 0.5 <= min_confidence <= 1.0:
            raise ValueError("OCR minimum confidence must be between 0.5 and 1.0")
        self.cache_dir = cache_dir
        self.min_page_text_characters = min_page_text_characters
        self.language = language
        self.min_confidence = min_confidence
        self.scale = scale
        self.powershell_executable = powershell_executable
        self.ocr_script = Path(__file__).parents[2] / "scripts" / "windows_ocr_images.ps1"

    def extract(self, pdf_path: Path) -> ExtractedDocument:
        if not self.ocr_script.is_file():
            raise FileNotFoundError(f"Windows OCR helper not found: {self.ocr_script}")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="windows-ocr-", dir=self.cache_dir) as temp_name:
            temp_dir = Path(temp_name)
            image_dir = temp_dir / "images"
            image_dir.mkdir()
            output_path = temp_dir / "ocr.json"
            embedded_text: dict[int, str] = {}
            original_text: dict[int, str] = {}
            image_pages: list[int] = []
            with fitz.open(pdf_path) as document:
                metadata = {
                    str(key): str(value or "") for key, value in (document.metadata or {}).items()
                }
                page_count = document.page_count
                matrix = fitz.Matrix(self.scale, self.scale)
                for page_index, page in enumerate(document):
                    text = page.get_text("text").strip()
                    page_number = page_index + 1
                    original_text[page_number] = text
                    if len(text) >= self.min_page_text_characters:
                        embedded_text[page_number] = text
                        continue
                    image_path = image_dir / f"page-{page_number:04d}.png"
                    page.get_pixmap(matrix=matrix, alpha=False).save(image_path)
                    image_pages.append(page_number)

            recognized: dict[int, str] = {}
            if image_pages:
                completed = subprocess.run(
                    [
                        self.powershell_executable,
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(self.ocr_script),
                        "-InputDirectory",
                        str(image_dir),
                        "-OutputPath",
                        str(output_path),
                        "-Language",
                        self.language,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=max(120, len(image_pages) * 60),
                )
                if completed.returncode != 0:
                    message = (completed.stderr or completed.stdout).strip()[-1000:]
                    raise RuntimeError(f"Windows OCR failed: {message}")
                payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
                for item in payload:
                    page_number = int(str(item["file_name"])[5:9])
                    if str(item.get("language") or self.language) != self.language:
                        raise RuntimeError("Windows OCR returned an unexpected language")
                    recognized[page_number] = str(item.get("text") or "").strip()

            traces: list[OcrPageTrace] = []
            pages: list[PageText] = []
            for page_number in range(1, page_count + 1):
                if page_number in embedded_text:
                    pages.append(
                        PageText(
                            page_number=page_number,
                            text=embedded_text[page_number],
                        )
                    )
                    continue
                ocr_text = recognized.get(page_number, "")
                confidence = ocr_text_confidence(ocr_text)
                if not ocr_text:
                    decision = "ocr_empty"
                elif confidence >= self.min_confidence:
                    decision = "ocr_confident"
                else:
                    decision = "ocr_low_confidence"
                admitted = decision == "ocr_confident"
                traces.append(
                    OcrPageTrace(
                        page_number=page_number,
                        language=self.language,
                        confidence=confidence,
                        embedded_text_original=original_text.get(page_number, ""),
                        ocr_text=ocr_text,
                        admitted=admitted,
                        decision_reason=decision,
                    )
                )
                pages.append(
                    PageText(
                        page_number=page_number,
                        text=(ocr_text if admitted else original_text.get(page_number, "")),
                        source_kind=("windows_ocr" if admitted else "pdf_embedded"),
                        ocr_language=self.language if admitted else None,
                        ocr_confidence=confidence if admitted else None,
                    )
                )
            text_page_count = sum(
                len(page.text.strip()) >= self.min_page_text_characters for page in pages
            )
            return ExtractedDocument(
                pdf_path=str(pdf_path.resolve()),
                page_count=page_count,
                pages=pages,
                metadata=metadata,
                text_character_count=sum(len(page.text) for page in pages),
                text_page_count=text_page_count,
                requires_ocr=text_page_count == 0,
                ocr_pages=traces,
            )
