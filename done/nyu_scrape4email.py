"""
NYU CDS Joint Faculty Email Scraper
====================================
Uses Selenium (headless Chrome) to render JavaScript on each profile page,
follows links to the professor's personal/faculty website, and extracts
emails that are not visible in raw HTML.

Requirements:
    pip install selenium webdriver-manager

Usage:
    python scrape4email.py

    # Fast run (names/titles only, no email fetch):
    python scrape4email.py --no-emails

    # Resume from an existing CSV (skips rows that already have an email):
    python scrape4email.py --resume nyu_joint_faculty.csv
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
OUTPUT_FILE    = "cmu_joint_faculty.csv"

# ---------------------------------------------------------------------------
# Selenium helpers
# ---------------------------------------------------------------------------

def _build_driver():
    """Creates a headless Chrome WebDriver with webdriver-manager."""
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


def _sleep():
    time.sleep(random.uniform(1.5, 3.0))


# ---------------------------------------------------------------------------
# Scraping logic
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Domains that are NOT personal/faculty websites — skip these as "personal site" candidates
_SKIP_DOMAINS = {
    "cds.nyu.edu", "nyu.edu", "twitter.com", "x.com",
    "linkedin.com", "github.com", "scholar.google.com",
    "google.com", "youtube.com", "facebook.com",
}


def _extract_email_from_page(driver) -> str:
    """
    Tries multiple strategies to find an email on the currently loaded page:
      1. <a href="mailto:…"> links (most reliable)
      2. Plain-text regex scan of the rendered body
    Returns the first match or 'N/A'.
    """
    from selenium.webdriver.common.by import By

    # Strategy 1 – mailto links
    try:
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href^='mailto:']")
        if anchors:
            href = anchors[0].get_attribute("href")
            return href.replace("mailto:", "").split("?")[0].strip()
    except Exception:
        pass

    # Strategy 2 – regex over visible page text
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        matches = EMAIL_RE.findall(body_text)
        for m in matches:
            if "example" not in m and "noreply" not in m:
                return m
    except Exception:
        pass

    return "N/A"


def _find_personal_website_url(driver) -> str | None:
    """
    On a CDS profile page, looks for a link to the professor's personal or
    department faculty website.

    Strategy:
      1. Look for an <a> whose visible text or title contains keywords like
         "website", "homepage", "personal site", "faculty page", etc.
      2. Fall back to any external link (not in _SKIP_DOMAINS) that is likely
         a university or personal academic page.

    Returns the URL string, or None if nothing suitable is found.
    """
    from selenium.webdriver.common.by import By

    PERSONAL_KEYWORDS = [
        "website", "homepage", "personal", "faculty page",
        "academic", "lab", "research page", "profile",
    ]

    try:
        all_links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
    except Exception:
        return None

    candidates = []
    for a in all_links:
        try:
            href = a.get_attribute("href") or ""
            text = (a.text or "").lower().strip()
            title = (a.get_attribute("title") or "").lower()
            aria  = (a.get_attribute("aria-label") or "").lower()
        except Exception:
            continue

        if not href.startswith("http"):
            continue

        parsed = urlparse(href)
        domain = parsed.netloc.lstrip("www.")

        if domain in _SKIP_DOMAINS:
            continue

        combined = f"{text} {title} {aria}"

        # Explicit keyword match → high priority
        if any(kw in combined for kw in PERSONAL_KEYWORDS):
            return href

        # Any external link that looks like a university or personal domain
        if (
            ".edu" in domain
            or ".ac." in domain
            or domain.count(".") == 1          # e.g. surname.com
        ):
            candidates.append(href)

    return candidates[0] if candidates else None


def _get_email_for_person(driver, profile_url: str) -> str:
    """
    Full two-step process for one faculty member:
      1. Load the CDS profile page.
      2. Try to find an email directly on the profile page.
      3. If not found, look for a link to the personal/faculty website and
         try to extract an email there.
    """
    logging.info(f"  Loading CDS profile: {profile_url}")
    driver.get(profile_url)
    _sleep()

    # Step A — try the profile page itself first
    email = _extract_email_from_page(driver)
    if email != "N/A":
        logging.info(f"  → Email found on CDS profile: {email}")
        return email

    # Step B — look for a personal/faculty website link
    personal_url = _find_personal_website_url(driver)
    if not personal_url:
        logging.info("  → No personal website link found on profile page.")
        return "N/A"

    logging.info(f"  Following personal website: {personal_url}")
    try:
        driver.get(personal_url)
        _sleep()
        email = _extract_email_from_page(driver)
    except Exception as e:
        logging.warning(f"  Error loading personal site {personal_url}: {e}")
        return "N/A"

    if email != "N/A":
        logging.info(f"  → Email found on personal site: {email}")
    else:
        logging.info("  → No email found on personal site either.")

    return email


def scrape_listing(driver) -> list[dict]:
    """
    Loads the joint-faculty listing page and returns a list of dicts with
    Name, Title, and Profile URL (email left blank for later).
    """
    logging.info(f"Loading listing page: {DIRECTORY_URL}")
    driver.get(DIRECTORY_URL)
    _sleep()

    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.sp-team-pro-item"))
    )

    cards = driver.find_elements(By.CSS_SELECTOR, "div.sp-team-pro-item")
    logging.info(f"Found {len(cards)} faculty cards.")

    faculty = []
    for card in cards:
        try:
            name_elem   = card.find_element(By.CSS_SELECTOR, "h2.sptp-name a")
            name        = name_elem.text.strip()
            profile_url = name_elem.get_attribute("href") or ""
        except Exception:
            try:
                name = card.find_element(By.CSS_SELECTOR, "h2.sptp-name").text.strip()
                profile_url = ""
            except Exception:
                continue

        try:
            title = card.find_element(By.CSS_SELECTOR, "p.sptp-profession-text").text.strip()
        except Exception:
            title = "N/A"

        faculty.append({"Name": name, "Title": title, "Profile URL": profile_url, "Email": "N/A"})

    return faculty


def enrich_with_emails(driver, faculty: list[dict]) -> list[dict]:
    """
    Iterates through each faculty member, visits their CDS profile page,
    follows the personal website link if needed, and extracts an email.
    """
    total = len(faculty)
    for i, person in enumerate(faculty, 1):
        if person.get("Email", "N/A") not in ("N/A", "", None):
            logging.info(f"[{i}/{total}] Skipping {person['Name']} (already has email).")
            continue

        url = person.get("Profile URL", "")
        if not url:
            logging.warning(f"[{i}/{total}] No profile URL for {person['Name']}.")
            continue

        logging.info(f"[{i}/{total}] Processing: {person['Name']} …")
        try:
            person["Email"] = _get_email_for_person(driver, url)
        except Exception as e:
            logging.warning(f"  Unexpected error for {person['Name']}: {e}")
            person["Email"] = "N/A"

    return faculty


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

FIELDNAMES = ["Name", "Title", "Profile URL", "Email"]


def load_existing_csv(path: str) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    logging.info(f"Loaded {len(rows)} existing records from {path}.")
    return rows


def save_csv(data: list[dict], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    logging.info(f"Saved {len(data)} records → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="NYU CDS Joint Faculty Email Scraper")
    parser.add_argument("--no-emails", action="store_true",
                        help="Skip email fetching (names/titles only, much faster).")
    parser.add_argument("--resume", metavar="CSV",
                        help="Path to an existing CSV; rows that already have an email are skipped.")
    args = parser.parse_args()

    driver = _build_driver()

    try:
        # Step 1 – get faculty list (from existing CSV or by scraping)
        if args.resume and Path(args.resume).exists():
            faculty = load_existing_csv(args.resume)
        else:
            faculty = scrape_listing(driver)
            save_csv(faculty, OUTPUT_FILE)

        # Step 2 – optionally enrich with emails
        if not args.no_emails:
            faculty = enrich_with_emails(driver, faculty)
            save_csv(faculty, OUTPUT_FILE)
        else:
            logging.info("Email fetching skipped (--no-emails flag).")

    finally:
        driver.quit()

    # Summary
    found   = sum(1 for p in faculty if p.get("Email", "N/A") not in ("N/A", "", None))
    missing = len(faculty) - found
    logging.info(f"Done. {found} emails found, {missing} still N/A.")
    logging.info(f"Results saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
