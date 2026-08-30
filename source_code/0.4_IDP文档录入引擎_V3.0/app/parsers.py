from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import fitz

from .schemas import ParsedDocument


OCRCallable = Callable[[Path], ParsedDocument]


class DocumentParser:
    """V3 parser router: native PDF text -> OCR."""

    def __init__(
        self,
        ocr_parser: Optional[OCRCallable] = None,
        min_native_chars: int = 80,
    ) -> None:
        self.ocr_parser = ocr_parser
        self.min_native_chars = min_native_chars

    def parse(self, file_path: str | Path) -> ParsedDocument:
        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            native = self._parse_pdf_text(path)
            if len(native.text.strip()) >= self.min_native_chars:
                return native

            if self.ocr_parser is not None:
                ocr = self.ocr_parser(path)
                if len(ocr.text.strip()) >= self.min_native_chars:
                    return ocr

            return native

        if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
            if self.ocr_parser is None:
                raise RuntimeError("Image input requires an OCR parser")
            return self.ocr_parser(path)

        raise ValueError(f"Unsupported document type: {suffix}")

    @staticmethod
    def _parse_pdf_text(path: Path) -> ParsedDocument:
        doc = fitz.open(path)
        try:
            pages = [page.get_text("text") for page in doc]
            return ParsedDocument(
                text="\n\n".join(pages),
                parser="pymupdf",
                page_count=doc.page_count,
                metadata={"source": str(path)},
            )
        finally:
            doc.close()
