"""Monitor Kenya Law Gazette for budget-related notices."""
import argparse
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GAZETTE_URL = "http://kenyalaw.org/kenya_gazette/"
KENYALAW_URL = "http://kenyalaw.org/kl/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CountyBudgetWatchdog/1.0)"}

BUDGET_KEYWORDS = re.compile(
    r"supplementary estimates|county allocation|revenue|appropriation|nairobi city county|budget",
    re.IGNORECASE,
)

GAZETTE_DIR = Path(__file__).parent.parent / "data" / "gazette"
MANIFEST_FILE = Path(__file__).parent.parent / "data" / "manifests" / "gazette_notices.json"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=4, max=30))
def fetch_page(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.text


def parse_date(text: str) -> date | None:
    for fmt in ("%d %B %Y", "%B %d, %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    return None


def scrape_gazette(since: date) -> list[dict]:
    notices = []
    try:
        html = fetch_page(GAZETTE_URL)
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = urljoin(GAZETTE_URL, a["href"])
            if BUDGET_KEYWORDS.search(text) or BUDGET_KEYWORDS.search(href):
                notices.append({
                    "source": "kenya_gazette",
                    "title": text,
                    "url": href,
                    "date": None,
                    "scraped_at": datetime.utcnow().isoformat(),
                })
    except Exception as exc:
        log.warning("kenya_gazette scrape error: %s", exc)

    try:
        html = fetch_page(KENYALAW_URL)
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = urljoin(KENYALAW_URL, a["href"])
            if BUDGET_KEYWORDS.search(text):
                notices.append({
                    "source": "kenyalaw",
                    "title": text,
                    "url": href,
                    "date": None,
                    "scraped_at": datetime.utcnow().isoformat(),
                })
    except Exception as exc:
        log.warning("kenyalaw scrape error: %s", exc)

    return notices


def load_manifest() -> list[dict]:
    if MANIFEST_FILE.exists():
        with open(MANIFEST_FILE) as f:
            return json.load(f)
    return []


def save_manifest(notices: list[dict]) -> None:
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    known_urls = {n["url"] for n in load_manifest()}
    all_notices = load_manifest()
    new_count = 0
    for n in notices:
        if n["url"] not in known_urls:
            all_notices.append(n)
            new_count += 1
    with open(MANIFEST_FILE, "w") as f:
        json.dump(all_notices, f, indent=2)
    log.info("Saved %d new gazette notices (total: %d)", new_count, len(all_notices))


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor Kenya Gazette for budget notices")
    parser.add_argument("--since", help="Only notices after this date (YYYY-MM-DD)", default=None)
    args = parser.parse_args()

    since = (
        date.fromisoformat(args.since)
        if args.since
        else date.today() - timedelta(days=30)
    )
    log.info("Fetching gazette notices since %s", since)

    GAZETTE_DIR.mkdir(parents=True, exist_ok=True)
    notices = scrape_gazette(since)
    save_manifest(notices)
    log.info("Done. Found %d matching notices.", len(notices))


if __name__ == "__main__":
    main()
