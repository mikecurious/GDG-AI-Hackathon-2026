"""Analyze PDF structure to inform Document AI processing decisions."""
import argparse
import json
import logging
import re
from pathlib import Path

import fitz  # PyMuPDF

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ANALYSIS_DIR = Path(__file__).parent.parent / "data" / "analysis"


def is_scanned_page(page: fitz.Page) -> bool:
    text = page.get_text("text").strip()
    return len(text) < 50


def has_tables(page: fitz.Page) -> bool:
    text = page.get_text("text")
    lines = [l for l in text.splitlines() if l.strip()]
    numeric_lines = sum(1 for l in lines if re.search(r"\d{4,}", l))
    return numeric_lines > 5


def extract_headings(doc: fitz.Document) -> list[dict]:
    headings = []
    for i, page in enumerate(doc):
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("size", 0) > 13 and span.get("text", "").strip():
                        headings.append({"page": i + 1, "text": span["text"].strip(), "size": span["size"]})
    return headings[:50]


def analyze(pdf_path: Path) -> dict:
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    scanned_pages = sum(1 for p in doc if is_scanned_page(p))
    is_scanned = scanned_pages > total_pages * 0.5

    table_pages = sum(1 for p in doc if has_tables(p))

    sample_text = {}
    for i in range(min(5, total_pages)):
        sample_text[f"page_{i+1}"] = doc[i].get_text("text")[:500]

    headings = extract_headings(doc)

    result = {
        "filename": pdf_path.name,
        "total_pages": total_pages,
        "is_scanned": is_scanned,
        "scanned_page_count": scanned_pages,
        "table_page_count": table_pages,
        "recommended_processor": "OCR" if is_scanned else "form_parser",
        "sample_text": sample_text,
        "detected_headings": headings,
    }

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    out = ANALYSIS_DIR / f"{pdf_path.stem}_structure.json"
    with open(out, "w") as f:
        json.dump(result, f, indent=2)

    log.info("Analysis saved: %s", out)
    log.info("  Pages: %d  Scanned: %s  Table pages: %d", total_pages, is_scanned, table_pages)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze PDF structure")
    parser.add_argument("pdf", help="Path to PDF file")
    args = parser.parse_args()
    analyze(Path(args.pdf))


if __name__ == "__main__":
    main()
