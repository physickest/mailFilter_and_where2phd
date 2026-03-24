"""
NYU CDS Joint Faculty Email Scraper
====================================
Uses Selenium (headless Chrome) to render JavaScript on each profile page
and extract emails that are not visible in raw HTML.

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

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

DIRECTORY_URL = "https://cds.nyu.edu/joint-faculty/"
OUTPUT_FILE    = "nyu_joint_faculty.csv"

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
        # Filter out generic/unrelated addresses
        for m in matches:
            if "example" not in m and "noreply" not in m:
                return m
    except Exception:
        pass

    return "N/A"


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

    # Wait for at least one card to appear
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "div.sp-team-pro-item"))
    )

    cards = driver.find_elements(By.CSS_SELECTOR, "div.sp-team-pro-item")
    logging.info(f"Found {len(cards)} faculty cards.")

    faculty = []
    for card in cards:
        try:
            name_elem  = card.find_element(By.CSS_SELECTOR, "h2.sptp-name a")
            name       = name_elem.text.strip()
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
    Iterates through each faculty member, visits their profile page,
    and attempts to extract an email address.
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

        logging.info(f"[{i}/{total}] Fetching profile: {person['Name']} …")
        try:
            driver.get(url)
            _sleep()
            person["Email"] = _extract_email_from_page(driver)
        except Exception as e:
            logging.warning(f"  Error loading {url}: {e}")
            person["Email"] = "N/A"

        logging.info(f"  → {person['Email']}")

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
            save_csv(faculty, OUTPUT_FILE)   # save names/titles immediately

        # Step 2 – optionally enrich with emails
        if not args.no_emails:
            faculty = enrich_with_emails(driver, faculty)
            save_csv(faculty, OUTPUT_FILE)
        else:
            logging.info("Email fetching skipped (--no-emails flag).")

    finally:
        driver.quit()

    # Summary
    found    = sum(1 for p in faculty if p.get("Email", "N/A") != "N/A")
    missing  = len(faculty) - found
    logging.info(f"Done. {found} emails found, {missing} still N/A.")
    logging.info(f"Results saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
