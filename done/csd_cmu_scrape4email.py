"""
CMU CSD Research & Tenure Faculty Email Scraper
=================================================
Scrapes https://csd.cmu.edu/people/research-tenure-faculty

The listing page renders faculty name, title, office, phone, and email
directly in an HTML table — no JavaScript rendering required for most data.
Emails that are missing from the listing are sought on the individual
profile page, then on any linked personal/lab website.

Requirements:
    pip install selenium webdriver-manager

Usage:
    python scrape4email.py                        # full run
    python scrape4email.py --no-emails            # names/titles only
    python scrape4email.py --resume existing.csv  # skip already-found emails
"""

import csv
import logging
import random
import re
import time
import argparse
from pathlib import Path
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

DIRECTORY_URL = "https://csd.cmu.edu/people/research-tenure-faculty"
OUTPUT_FILE   = "cmu_csd_faculty.csv"
FIELDNAMES    = ["Name", "Title", "Office", "Phone", "Profile URL", "Email"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_SKIP_DOMAINS = {
    "csd.cmu.edu", "cmu.edu", "cs.cmu.edu",
    "twitter.com", "x.com", "linkedin.com", "github.com",
    "scholar.google.com", "google.com", "youtube.com", "facebook.com",
}

# ---------------------------------------------------------------------------
# Selenium helpers
# ---------------------------------------------------------------------------

def _build_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def _sleep(lo=1.5, hi=3.0):
    time.sleep(random.uniform(lo, hi))


# ---------------------------------------------------------------------------
# Email / personal-site extraction (used as fallback)
# ---------------------------------------------------------------------------

def _extract_email_from_page(driver) -> str:
    """mailto links first, then regex over body text."""
    from selenium.webdriver.common.by import By

    try:
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href^='mailto:']")
        if anchors:
            href = anchors[0].get_attribute("href")
            return href.replace("mailto:", "").split("?")[0].strip()
    except Exception:
        pass

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        for m in EMAIL_RE.findall(body_text):
            if "example" not in m and "noreply" not in m:
                return m
    except Exception:
        pass

    return "N/A"


_PERSONAL_KEYWORDS = [
    "website", "homepage", "personal", "faculty page",
    "academic", "lab", "research page", "profile", "home page",
]


def _find_personal_website_url(driver) -> str | None:
    """Return the URL of a personal/faculty website linked from the current page."""
    from selenium.webdriver.common.by import By

    try:
        all_links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
    except Exception:
        return None

    candidates = []
    for a in all_links:
        try:
            href  = a.get_attribute("href") or ""
            text  = (a.text or "").lower().strip()
            title = (a.get_attribute("title") or "").lower()
            aria  = (a.get_attribute("aria-label") or "").lower()
        except Exception:
            continue

        if not href.startswith("http"):
            continue

        domain = urlparse(href).netloc.lstrip("www.")
        if domain in _SKIP_DOMAINS:
            continue

        combined = f"{text} {title} {aria}"
        if any(kw in combined for kw in _PERSONAL_KEYWORDS):
            return href

        if ".edu" in domain or ".ac." in domain or domain.count(".") == 1:
            candidates.append(href)

    return candidates[0] if candidates else None


def _get_email_via_profile(driver, profile_url: str) -> str:
    """
    Fallback: visit the CSD profile page, try email there, then follow
    any personal-website link.
    """
    logging.info(f"  -> Visiting profile page: {profile_url}")
    try:
        driver.get(profile_url)
        _sleep()
    except Exception as e:
        logging.warning(f"  Could not load profile: {e}")
        return "N/A"

    email = _extract_email_from_page(driver)
    if email != "N/A":
        return email

    personal_url = _find_personal_website_url(driver)
    if not personal_url:
        return "N/A"

    logging.info(f"  -> Following personal website: {personal_url}")
    try:
        driver.get(personal_url)
        _sleep()
        return _extract_email_from_page(driver)
    except Exception as e:
        logging.warning(f"  Could not load personal site: {e}")
        return "N/A"


# ---------------------------------------------------------------------------
# Listing-page scraper
# ---------------------------------------------------------------------------

def scrape_listing(driver) -> list[dict]:
    """
    Load the CMU CSD faculty listing page and parse the table.

    Table structure (one row per faculty member):
      col 0 - photo / profile link
      col 1 - Name (link), Title, Office, Phone, Email (mailto)
      col 2 - Research areas
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logging.info(f"Loading listing page: {DIRECTORY_URL}")
    driver.get(DIRECTORY_URL)
    _sleep()

    # The table is a plain HTML <table> -- wait for it
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr"))
    )

    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    logging.info(f"Found {len(rows)} rows in the faculty table.")

    faculty = []
    for row in rows:
        cols = row.find_elements(By.TAG_NAME, "td")
        if len(cols) < 2:
            continue

        info_cell = cols[1]   # Name / contact column

        # Name & profile URL
        try:
            name_link   = info_cell.find_element(By.CSS_SELECTOR, "a")
            name        = name_link.text.strip()
            # CMU stores names as "Last, First" -- swap to "First Last"
            if "," in name:
                last, first = name.split(",", 1)
                name = f"{first.strip()} {last.strip()}"
            profile_url = name_link.get_attribute("href") or ""
        except Exception:
            name, profile_url = "N/A", ""

        cell_text = info_cell.text.strip()
        lines     = [ln.strip() for ln in cell_text.splitlines() if ln.strip()]

        # Title is typically the second line (after the name)
        title = lines[1] if len(lines) > 1 else "N/A"

        # Office -- look for "Gates" or a room-number pattern
        office = "N/A"
        for ln in lines:
            if "Gates" in ln or re.match(r"^\d{3,4}", ln):
                office = ln
                break

        # Phone -- look for (NNN) NNN-NNNN pattern
        phone = "N/A"
        for ln in lines:
            if re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", ln):
                phone = ln
                break

        # Email -- prefer the mailto link; fall back to regex on cell text
        email = "N/A"
        try:
            mailto = info_cell.find_element(By.CSS_SELECTOR, "a[href^='mailto:']")
            email  = mailto.get_attribute("href").replace("mailto:", "").split("?")[0].strip()
        except Exception:
            for m in EMAIL_RE.findall(cell_text):
                if "example" not in m and "noreply" not in m:
                    email = m
                    break

        faculty.append({
            "Name":        name,
            "Title":       title,
            "Office":      office,
            "Phone":       phone,
            "Profile URL": profile_url,
            "Email":       email,
        })

    logging.info(f"Parsed {len(faculty)} faculty records.")
    return faculty


# ---------------------------------------------------------------------------
# Email enrichment (fallback for rows still missing an email)
# ---------------------------------------------------------------------------

def enrich_with_emails(driver, faculty: list[dict]) -> list[dict]:
    total   = len(faculty)
    missing = [p for p in faculty if p.get("Email", "N/A") in ("N/A", "", None)]
    logging.info(f"{len(missing)} of {total} records still need an email -- visiting profile pages.")

    for i, person in enumerate(missing, 1):
        url = person.get("Profile URL", "")
        if not url:
            logging.warning(f"[{i}/{len(missing)}] No profile URL for {person['Name']}.")
            continue

        logging.info(f"[{i}/{len(missing)}] {person['Name']} ...")
        person["Email"] = _get_email_via_profile(driver, url)
        logging.info(f"  -> {person['Email']}")

    return faculty


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def load_existing_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(dict(row))
    logging.info(f"Loaded {len(rows)} existing records from {path}.")
    return rows


def save_csv(data: list[dict], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    logging.info(f"Saved {len(data)} records -> {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CMU CSD Research & Tenure Faculty Scraper")
    parser.add_argument("--no-emails", action="store_true",
                        help="Skip email fetching (names/titles only).")
    parser.add_argument("--resume", metavar="CSV",
                        help="Resume from an existing CSV; rows with emails are skipped.")
    args = parser.parse_args()

    driver = _build_driver()

    try:
        if args.resume and Path(args.resume).exists():
            faculty = load_existing_csv(args.resume)
        else:
            faculty = scrape_listing(driver)
            save_csv(faculty, OUTPUT_FILE)

        if not args.no_emails:
            faculty = enrich_with_emails(driver, faculty)
            save_csv(faculty, OUTPUT_FILE)
        else:
            logging.info("Email fetching skipped (--no-emails).")

    finally:
        driver.quit()

    found   = sum(1 for p in faculty if p.get("Email", "N/A") not in ("N/A", "", None))
    missing = len(faculty) - found
    logging.info(f"Done. {found} emails found, {missing} still N/A.")
    logging.info(f"Results saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
