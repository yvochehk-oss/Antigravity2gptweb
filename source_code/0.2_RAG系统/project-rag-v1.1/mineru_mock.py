#!/usr/bin/env python3
"""Offline local OCR CLI compatible with the small MinerU adapter contract.

This compatibility entry point intentionally supports only PDFs and images.
It never fabricates a successful parse: callers receive a non-zero exit code
when no usable text was extracted, a model is unavailable, or a format is not
implemented locally.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


class ParserError(RuntimeError):
    """A document cannot be safely parsed by the local OCR fallback."""


SCRIPT_DIR = Path(__file__).resolve().parent
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
REQUIRED_MODEL_FILES = (
    "ch_PP-OCRv4_det_infer.onnx",
    "ch_PP-OCRv4_rec_infer.onnx",
    "ch_ppocr_mobile_v2.0_cls_infer.onnx",
)


def default_models_dir() -> Path:
    """Return the bundled V2.0 model directory without machine-specific paths."""
    # project-rag-v1.1 -> 0.2_RAG系统 -> source_code -> V2.0
    return SCRIPT_DIR.parents[2] / "models" / "rapidocr"


def models_dir() -> Path:
    """Return an explicit operator override or the V2.0 bundled model directory."""
    configured = os.getenv("RAPIDOCR_MODELS_DIR", "").strip()
    return Path(configured).expanduser() if configured else default_models_dir()


def _require_models(directory: Path) -> dict[str, Path]:
    files = {name: directory / name for name in REQUIRED_MODEL_FILES}
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise ParserError(
            "RapidOCR local models are unavailable; set RAPIDOCR_MODELS_DIR "
            f"or install the bundled files (missing: {', '.join(missing)})"
        )
    return files


def get_ocr_engine() -> Any:
    """Build RapidOCR strictly with bundled local models; never download defaults."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise ParserError(
            "RapidOCR dependency is unavailable; install the project dependencies"
        ) from exc
    files = _require_models(models_dir())
    try:
        return RapidOCR(
            det_model_path=str(files["ch_PP-OCRv4_det_infer.onnx"]),
            rec_model_path=str(files["ch_PP-OCRv4_rec_infer.onnx"]),
            cls_model_path=str(files["ch_ppocr_mobile_v2.0_cls_infer.onnx"]),
        )
    except Exception as exc:
        raise ParserError("RapidOCR local engine initialization failed") from exc


def _ocr_lines(engine: Any, source: str | bytes) -> tuple[str, float | None]:
    try:
        result, _elapsed = engine(source)
    except Exception as exc:
        raise ParserError("RapidOCR could not process the input") from exc
    if not result:
        return "", None

    lines: list[str] = []
    confidence: list[float] = []
    for item in result:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        text = str(item[1]).strip()
        if text:
            lines.append(text)
        if len(item) >= 3 and isinstance(item[2], (int, float)):
            confidence.append(float(item[2]))
    return "\n".join(lines).strip(), (
        sum(confidence) / len(confidence) if confidence else None
    )


def _write_output(out_dir: Path, stem: str, blocks: list[dict[str, Any]]) -> None:
    text = "\n\n".join(str(block["text"]) for block in blocks).strip()
    if not text:
        raise ParserError("Parser produced no usable text; no output was written")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}.md").write_text(text, encoding="utf-8")
    (out_dir / f"{stem}_content_list.json").write_text(
        json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_file(input_path: str, out_dir: str) -> None:
    input_file = Path(input_path)
    if not input_file.is_file():
        raise ParserError("Input document does not exist or is not a regular file")

    extension = input_file.suffix.lower()
    blocks: list[dict[str, Any]] = []
    if extension == ".pdf":
        try:
            import fitz
        except ImportError as exc:
            raise ParserError("PyMuPDF dependency is unavailable") from exc
        try:
            with fitz.open(str(input_file)) as document:
                engine: Any | None = None
                for page_index, page in enumerate(document):
                    text = page.get_text().strip()
                    confidence: float | None = None
                    if len(text) < 30:
                        if engine is None:
                            engine = get_ocr_engine()
                        png = page.get_pixmap(dpi=150).tobytes("png")
                        text, confidence = _ocr_lines(engine, png)
                    if text:
                        block: dict[str, Any] = {
                            "type": "text", "text": text, "page_idx": page_index,
                        }
                        if confidence is not None:
                            block["score"] = confidence
                        blocks.append(block)
        except ParserError:
            raise
        except Exception as exc:
            raise ParserError("PDF could not be read") from exc
    elif extension in SUPPORTED_IMAGE_EXTENSIONS:
        text, confidence = _ocr_lines(get_ocr_engine(), str(input_file))
        if text:
            block = {"type": "text", "text": text, "page_idx": 0}
            if confidence is not None:
                block["score"] = confidence
            blocks.append(block)
    else:
        raise ParserError(
            f"Local OCR does not support '{extension or 'files without an extension'}'; "
            "use the full MinerU parser for office documents"
        )
    if not blocks:
        raise ParserError("Parser produced no usable text; no output was written")
    _write_output(Path(out_dir), input_file.stem, blocks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline RapidOCR local parser")
    parser.add_argument("-p", "--pdf", "--input", dest="input_path", required=True)
    parser.add_argument("-o", "--out", dest="out_dir", required=True)
    parser.add_argument("-b", "--backend", dest="backend")
    parser.add_argument("--api-url", dest="api_url")
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args_list:
        print("local-rapidocr-parser 1.0.0")
        return 0
    args = build_parser().parse_args(args_list)
    if args.backend or args.api_url:
        raise ParserError("--backend and --api-url are unsupported by offline RapidOCR")
    parse_file(args.input_path, args.out_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ParserError as exc:
        print(f"Local OCR parser failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
