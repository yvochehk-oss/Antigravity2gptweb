#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

# Auto-re-exec into the project virtual environment Python if available
_script_dir = Path(__file__).resolve().parent
_venv_python = _script_dir / ".venv" / "bin" / "python"
if _venv_python.exists() and sys.executable != str(_venv_python):
    os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)

def get_ocr_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
        models_dir = _script_dir.parents[1] / "models" / "rapidocr"
        det_model = models_dir / "ch_PP-OCRv4_det_infer.onnx"
        rec_model = models_dir / "ch_PP-OCRv4_rec_infer.onnx"
        cls_model = models_dir / "ch_ppocr_mobile_v2.0_cls_infer.onnx"
        if det_model.exists() and rec_model.exists():
            return RapidOCR(
                det_model_path=str(det_model),
                rec_model_path=str(rec_model),
                cls_model_path=str(cls_model) if cls_model.exists() else None
            )
        return RapidOCR()
    except Exception as e:
        print(f"RapidOCR initialization notice: {e}", file=sys.stderr)
        return None

def parse_file(input_path: str, out_dir: str):
    os.makedirs(out_dir, exist_ok=True)
    in_p = Path(input_path)
    stem = in_p.stem
    ext = in_p.suffix.lower()

    text_blocks = []
    ocr_engine = None

    if ext == ".pdf":
        try:
            import fitz
            doc = fitz.open(input_path)
            for i, page in enumerate(doc):
                page_text = page.get_text().strip()
                if not page_text or len(page_text) < 30:
                    # Try OCR for scanned PDF page
                    if ocr_engine is None:
                        ocr_engine = get_ocr_engine()
                    if ocr_engine:
                        pix = page.get_pixmap(dpi=150)
                        temp_png = Path(out_dir) / f"_temp_p{i}.png"
                        pix.save(str(temp_png))
                        try:
                            res, _ = ocr_engine(str(temp_png))
                            if res:
                                page_text = "\n".join([line[1] for line in res])
                        finally:
                            if temp_png.exists():
                                temp_png.unlink()
                if page_text:
                    text_blocks.append(page_text)
        except Exception as e:
            print(f"PDF extract notice ({input_path}): {e}", file=sys.stderr)

    elif ext in {".jpg", ".jpeg", ".png", ".webp"}:
        try:
            ocr_engine = get_ocr_engine()
            if ocr_engine:
                res, _ = ocr_engine(input_path)
                if res:
                    text_blocks.append("\n".join([line[1] for line in res]))
        except Exception as e:
            print(f"Image OCR notice ({input_path}): {e}", file=sys.stderr)

    if not text_blocks:
        # Fallback to file name description if no text extracted
        text_blocks.append(f"【档案文档】文件名: {in_p.name}\n路径: {input_path}")

    full_text = "\n\n".join(text_blocks)

    # Write Markdown output
    md_path = os.path.join(out_dir, f"{stem}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(full_text)

    # Write content_list.json for downstream chunking & assessment
    json_path = os.path.join(out_dir, f"{stem}_content_list.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([{"type": "text", "text": full_text}], f, ensure_ascii=False, indent=2)

def main():
    if "--version" in sys.argv:
        print("mineru 0.9.0-local-ocr")
        sys.exit(0)

    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--pdf", "--input", dest="input_path", required=True)
    parser.add_argument("-o", "--out", dest="out_dir", required=True)

    args, _ = parser.parse_known_args()
    parse_file(args.input_path, args.out_dir)

if __name__ == "__main__":
    main()
