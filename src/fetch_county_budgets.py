"""Download Nairobi County budget PDFs into data/raw/."""
import argparse
import logging
import time
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CountyBudgetWatchdog/1.0; +https://github.com/gdg-nairobi)",
    "Accept": "application/pdf,*/*",
}

DOCUMENTS = [
    (
        "nairobi_itemized_estimates_2025_2026.pdf",
        "https://nairobiassembly.go.ke/ncca/wp-content/uploads/paperlaid/2025/"
        "NAIROBI-CITY-COUNTY-ITEMIZED-REVENUE-AND-EXPENDITURE-ESTIMATES-FY-2025-2026.pdf",
    ),
    (
        "nairobi_adp_2026_2027.pdf",
        "https://nairobiassembly.go.ke/ncca/wp-content/uploads/paperlaid/2025/"
        "NAIROBI-CITY-COUNTY-ANNUAL-DEVELOPMENT-PLAN-FY-2026-2027.pdf",
    ),
    (
        "nairobi_supplementary_i_2025_2026.pdf",
        "https://nairobiassembly.go.ke/ncca/wp-content/uploads/paperlaid/2025/"
        "THE-NAIROBI-CITY-COUNTY-SUPPLEMENTARY-I-EXPENDITURE-AND-REVENUE-ESTIMATES-FY-2025-2026.pdf",
    ),
    (
        "nairobi_cfsp_2025_2026.pdf",
        "https://nairobiassembly.go.ke/ncca/wp-content/uploads/paperlaid/2025/"
        "NAIROBI-CITY-COUNTY-FISCAL-STRATEGY-PAPER-FY-2025-2026.pdf",
    ),
    (
        "nairobi_brop_2025.pdf",
        "https://nairobiassembly.go.ke/ncca/wp-content/uploads/paperlaid/2025/"
        "THE-NAIROBI-CITY-COUNTY-BUDGET-REVIEW-AND-OUTLOOK-PAPER-2025.pdf",
    ),
    (
        "national_bps_2026.pdf",
        "https://www.treasury.go.ke/sites/default/files/Latest%20updates/2026%20Budget%20Policy%20Statement.pdf",
    ),
]

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def download_pdf(filename: str, url: str, dest_dir: Path, dry_run: bool = False) -> None:
    dest = dest_dir / filename
    if dest.exists():
        log.info("SKIP %s (already downloaded, %d bytes)", filename, dest.stat().st_size)
        return

    if dry_run:
        log.info("DRY-RUN would download: %s <- %s", filename, url)
        return

    log.info("Downloading %s ...", filename)
    with requests.get(url, headers=HEADERS, stream=True, timeout=120) as r:
        r.raise_for_status()
        content_length = int(r.headers.get("Content-Length", 0))
        log.info("  HTTP %d  size=%s bytes", r.status_code, content_length or "unknown")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)

    size = dest.stat().st_size
    log.info("  Saved %s (%d bytes)", dest.name, size)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Nairobi County budget PDFs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be downloaded")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in DOCUMENTS:
        try:
            download_pdf(filename, url, RAW_DIR, dry_run=args.dry_run)
        except Exception as exc:
            log.error("FAILED %s: %s", filename, exc)
        time.sleep(1)

    log.info("Done.")


if __name__ == "__main__":
    main()
