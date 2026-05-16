"""
Extract budget line items from PDF using Gemini 1.5 Pro and load into BigQuery.

Usage:
    python src/pdf_to_bq.py --pdf data/raw/nairobi_itemized_estimates_2025_2026.pdf
    python src/pdf_to_bq.py --pdf data/raw/nairobi_itemized_estimates_2025_2026.pdf --pages 1-50
"""
import argparse
import base64
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz
from dotenv import load_dotenv
from google import genai
from google.cloud import bigquery

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "gdg-ai-2026-496507")
DATASET_ID = os.getenv("BQ_DATASET", "county_budget")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")  # 1500 req/day free tier
LOCATION = os.getenv("GCP_REGION", "us-central1")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

EXTRACTION_PROMPT = """You are a Kenyan county budget analyst. Extract ALL budget line items visible in this page.

Return a JSON array. Each object must have these fields (use null if not found):
- vote_code: string (e.g., "5311000101")
- vote_name: string (e.g., "Human Resource Management")
- item_code: string (e.g., "2110199")
- item_description: string (e.g., "Basic Salaries - Permanent - Others")
- category: "recurrent" or "development"
- department: string
- programme: string
- sub_programme: string
- amount_approved: number (KES, no commas)
- amount_projected_next_fy: number or null
- amount_projected_next_next_fy: number or null

Return ONLY the JSON array, no explanation. If the page has no budget data, return [].
"""


def extract_page_text(pdf_path: Path, page_num: int) -> str:
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    return page.get_text("text")


def extract_with_gemini(client: genai.Client, pdf_path: Path, page_num: int) -> list[dict]:
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    pix = page.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            {
                "parts": [
                    {"text": EXTRACTION_PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(img_bytes).decode(),
                        }
                    },
                ]
            }
        ],
    )
    raw = response.text.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        items = json.loads(raw)
        return items if isinstance(items, list) else []
    except json.JSONDecodeError:
        log.warning("Page %d: could not parse Gemini JSON output", page_num + 1)
        return []


def upload_to_bq(client_bq: bigquery.Client, rows: list[dict], source_document: str) -> None:
    if not rows:
        return
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        row["county"] = "Nairobi"
        row["fiscal_year"] = "2025/2026"
        row["document_type"] = "itemized_estimates"
        row["source_document"] = source_document
        row["extracted_at"] = now
        row["extraction_confidence"] = 0.85
        row.setdefault("vote_code", None)
        row.setdefault("vote_name", None)
        row.setdefault("item_code", None)
        row.setdefault("item_description", None)
        row.setdefault("category", None)
        row.setdefault("department", None)
        row.setdefault("programme", None)
        row.setdefault("sub_programme", None)
        row.setdefault("amount_approved", None)
        row.setdefault("amount_projected_next_fy", None)
        row.setdefault("amount_projected_next_next_fy", None)
        row.setdefault("page_number", None)

    table_ref = f"{PROJECT_ID}.{DATASET_ID}.budget_line_items"
    errors = client_bq.insert_rows_json(table_ref, rows)
    if errors:
        log.error("BQ insert errors: %s", errors)
    else:
        log.info("  Inserted %d rows into %s", len(rows), table_ref)


def parse_page_range(spec: str, total: int) -> list[int]:
    if not spec:
        return list(range(total))
    pages = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            pages.extend(range(int(a) - 1, min(int(b), total)))
        else:
            pages.append(int(part) - 1)
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract budget data from PDF into BigQuery")
    parser.add_argument("--pdf", required=True, help="Path to PDF file")
    parser.add_argument("--pages", default="", help="Page range e.g. 1-50 or 1,5,10-20")
    parser.add_argument("--model", default=GEMINI_MODEL, help="Gemini model ID (default: gemini-1.5-flash)")
    parser.add_argument("--dry-run", action="store_true", help="Extract but don't insert to BQ")
    args = parser.parse_args()
    global GEMINI_MODEL
    GEMINI_MODEL = args.model

    pdf_path = Path(args.pdf)
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    pages_to_process = parse_page_range(args.pages, total_pages)
    log.info("Processing %d pages from %s", len(pages_to_process), pdf_path.name)

    if GOOGLE_API_KEY:
        genai_client = genai.Client(api_key=GOOGLE_API_KEY)
    else:
        genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
    bq_client = bigquery.Client(project=PROJECT_ID)

    total_rows = 0
    for page_num in pages_to_process:
        log.info("Extracting page %d/%d ...", page_num + 1, total_pages)
        items = extract_with_gemini(genai_client, pdf_path, page_num)
        for item in items:
            item["page_number"] = page_num + 1
        log.info("  Found %d line items on page %d", len(items), page_num + 1)
        if not args.dry_run:
            upload_to_bq(bq_client, items, pdf_path.name)
        total_rows += len(items)

    log.info("Extraction complete: %d total line items from %s", total_rows, pdf_path.name)


if __name__ == "__main__":
    main()
