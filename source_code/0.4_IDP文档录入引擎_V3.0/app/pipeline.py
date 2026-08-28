from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict

from .extractors import HybridExtractor
from .parsers import DocumentParser
from .validators import decide_review, validate_contract, validate_invoice


class IDPPipeline:
    def __init__(self, parser: DocumentParser, extractor: HybridExtractor) -> None:
        self.parser = parser
        self.extractor = extractor

    @staticmethod
    def sha256(path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def classify(text: str) -> str:
        head = text[:5000]
        if "发票号码" in head or "价税合计" in head or "增值税" in head:
            return "invoice"
        if "合同" in head and any(k in head for k in ("甲方", "乙方", "签订", "合同编号")):
            return "contract"
        if "银行回单" in head or "电子回单" in head:
            return "bank_receipt"
        if "收据" in head:
            return "receipt"
        return "unknown"

    def process(self, file_path: str | Path) -> Dict[str, Any]:
        parsed = self.parser.parse(file_path)
        document_type = self.classify(parsed.text)
        data = self.extractor.extract(document_type, parsed.text)

        if document_type == "invoice":
            validation = validate_invoice(data)
        elif document_type == "contract":
            validation = validate_contract(data)
        else:
            validation = {"ok": False, "errors": ["unsupported_document_type"], "warnings": []}

        status = decide_review(data, validation) if data else "needs_review"

        return {
            "sha256": self.sha256(file_path),
            "document_type": document_type,
            "parser": parsed.parser,
            "page_count": parsed.page_count,
            "ocr_confidence": parsed.ocr_confidence,
            "data": data,
            "validation": validation,
            "status": status,
        }
