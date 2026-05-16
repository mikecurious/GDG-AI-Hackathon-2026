"""Scrape the Nairobi County Assembly papers-laid page for budget PDFs."""
import argparse
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PAPERS_URL = "https://nairobiassembly.go.ke/ncca/papers-laid/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CountyBudgetWatchdog/1.0)",
    "Accept": "text/html,application/xhtml+xml",
}
BUDGET_KEYWORDS = re.compile(
    r"budget|estimates|supplementary|expenditure|revenue|appropriation|fiscal",
    re.IGNORECASE,
)

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
MANIFEST_DIR = Path(__file__).parent.parent / "data" / "manifests"
MANIFEST_FILE = MANIFEST_DIR / "nairobi_assembly_papers.json"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def fetch_page(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.text


def scrape_papers() -> list[dict]:
    log.info("Fetching papers page: %s", PAPERS_URL)
    html = fetch_page(PAPERS_URL)
    soup = BeautifulSoup(html, "lxml")

    papers = []
    content = soup.find("div", class_=re.compile(r"content|entry|main|post", re.I)) or soup.body

    for a in content.find_all("a", href=re.compile(r"\.pdf$", re.I)):
        href = a.get("href", "")
        title = a.get_text(strip=True) or Path(href).stem
        if not BUDGET_KEYWORDS.search(title) and not BUDGET_KEYWORDS.search(href):
            continue

        full_url = urljoin(PAPERS_URL, href)
        filename = Path(href).name
        dest = RAW_DIR / filename

        papers.append({
            "title": title,
            "url": full_url,
            "date_posted": None,
            "file_size_mb": None,
            "downloaded": dest.exists(),
        })

    log.info("Found %d budget-related PDFs", len(papers))
    return papers


def save_manifest(papers: list[dict]) -> None:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_FILE, "w") as f:
        json.dump(papers, f, indent=2)
    log.info("Manifest saved: %s", MANIFEST_FILE)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def download_pdf(paper: dict) -> None:
    dest = RAW_DIR / Path(paper["url"]).name
    if dest.exists():
        log.info("SKIP %s (exists)", dest.name)
        return
    log.info("Downloading %s", paper["url"])
    with requests.get(paper["url"], headers=HEADERS, stream=True, timeout=120) as r:
        r.raise_for_status()
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
    paper["downloaded"] = True
    paper["file_size_mb"] = round(dest.stat().st_size / 1_048_576, 2)
    log.info("  Saved %s (%.2f MB)", dest.name, paper["file_size_mb"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Nairobi Assembly papers-laid page")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    papers = scrape_papers()
    save_manifest(papers)

    if args.dry_run:
        for p in papers:
            log.info("DRY-RUN: %s", p["url"])
        return

    for paper in papers:
        if not paper["downloaded"]:
            try:
                download_pdf(paper)
            except Exception as exc:
                log.error("FAILED %s: %s", paper["url"], exc)
            time.sleep(1)

    save_manifest(papers)


if __name__ == "__main__":
    main()
