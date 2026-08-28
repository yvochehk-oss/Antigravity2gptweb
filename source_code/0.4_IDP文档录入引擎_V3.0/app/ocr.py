from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

import fitz
import numpy as np

from .schemas import ParsedDocument


class PaddleOCRAdapter:
    """Lazy OCR adapter for scans and images.

    PaddleOCR is imported and initialized only when OCR is actually needed so
    native-text PDFs do not pay the RAM/startup cost on low-resource Windows PCs.
    """

    def __init__(self) -> None:
        self.lang = os.getenv("OCR_LANG", "ch")
        self.dpi = int(os.getenv("OCR_PDF_DPI", "180"))
        self._engine: Any | None = None

    def _get_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError(
                "OCR is required for this document. Install optional dependencies: "
                "pip install paddleocr paddlepaddle"
            ) from exc

        try:
            self._engine = PaddleOCR(
                lang=self.lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        except (TypeError, ValueError):
            # PaddleOCR 2.x compatibility.
            self._engine = PaddleOCR(lang=self.lang, use_angle_cls=True, show_log=False)
        return self._engine

    def __call__(self, path: Path) -> ParsedDocument:
        if path.suffix.lower() == ".pdf":
            images = self._pdf_images(path)
            page_count = len(images)
        else:
            images = [str(path)]
            page_count = 1

        page_texts: list[str] = []
        confidences: list[float] = []
        engine = self._get_engine()
        for image in images:
            result = self._run(engine, image)
            lines = list(self._iter_text_scores(result))
            page_texts.append("\n".join(text for text, _ in lines))
            confidences.extend(score for _, score in lines if score is not None)

        mean_confidence = (
            sum(confidences) / len(confidences) if confidences else None
        )
        return ParsedDocument(
            text="\n\n".join(page_texts),
            parser="paddleocr",
            page_count=page_count,
            ocr_confidence=mean_confidence,
            metadata={"source": str(path), "ocr_lang": self.lang},
        )

    def _pdf_images(self, path: Path) -> list[np.ndarray]:
        doc = fitz.open(path)
        images: list[np.ndarray] = []
        try:
            zoom = self.dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            for page in doc:
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                array = np.frombuffer(pix.samples, dtype=np.uint8)
                array = array.reshape(pix.height, pix.width, pix.n)
                images.append(array[:, :, :3].copy())
        finally:
            doc.close()
        return images

    @staticmethod
    def _run(engine: Any, image: Any) -> Any:
        # PaddleOCR 2.x exposes ocr(); 3.x keeps compatibility in common builds.
        if hasattr(engine, "ocr"):
            try:
                return engine.ocr(image, cls=True)
            except TypeError:
                return engine.ocr(image)
        if hasattr(engine, "predict"):
            return engine.predict(image)
        raise RuntimeError("Unsupported PaddleOCR runtime: neither ocr nor predict is available")

    @classmethod
    def _iter_text_scores(cls, node: Any) -> Iterable[tuple[str, float | None]]:
        if node is None:
            return

        if isinstance(node, dict):
            texts = node.get("rec_texts") or node.get("texts")
            scores = node.get("rec_scores") or node.get("scores")
            if isinstance(texts, (list, tuple)):
                for index, text in enumerate(texts):
                    score = None
                    if isinstance(scores, (list, tuple)) and index < len(scores):
                        try:
                            score = float(scores[index])
                        except (TypeError, ValueError):
                            score = None
                    if text:
                        yield str(text), score
                return
            for value in node.values():
                yield from cls._iter_text_scores(value)
            return

        if isinstance(node, (list, tuple)):
            # PaddleOCR 2.x line: [box, (text, score)]
            if (
                len(node) == 2
                and isinstance(node[1], (list, tuple))
                and len(node[1]) >= 2
                and isinstance(node[1][0], str)
            ):
                try:
                    score = float(node[1][1])
                except (TypeError, ValueError):
                    score = None
                yield node[1][0], score
                return
            for child in node:
                yield from cls._iter_text_scores(child)
            return

        # PaddleOCR 3.x result objects often expose a json/res mapping.
        for attr in ("json", "res"):
            value = getattr(node, attr, None)
            if value is not None and value is not node:
                yield from cls._iter_text_scores(value)
                return
