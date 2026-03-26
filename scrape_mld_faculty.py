"""
CMU MLD Core Faculty Email Scraper
====================================
Scrapes https://ml.cmu.edu/people/core-faculty

The page is fully JavaScript-rendered via the SCS directory widget.
Selenium waits for the cards to appear, then extracts name, title, and
email from each card.  For any card missing an email it falls back to:
  1. The individual SCS profile page
  2. Any personal/lab website linked from that profile

Requirements:
    pip install selenium webdriver-manager

Usage:
    python scrape_mld_faculty.py                         # full run
    python scrape_mld_faculty.py --no-emails             # names/titles only
    python scrape_mld_faculty.py --resume existing.csv   # skip found emails
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

DIRECTORY_URL = "https://ml.cmu.edu/people/core-faculty"
OUTPUT_FILE   = "cmu_mld_core_faculty.csv"
FIELDNAMES    = ["Name", "Title", "Office", "Phone", "Profile URL", "Email"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_SKIP_DOMAINS = {
    "ml.cmu.edu", "csd.cmu.edu", "cmu.edu", "cs.cmu.edu", "scs.cmu.edu",
    "twitter.com", "x.com", "linkedin.com", "github.com",
    "scholar.google.com", "google.com", "youtube.com", "facebook.com",
}

_PERSONAL_KEYWORDS = [
    "website", "homepage", "personal", "faculty page",
    "academic", "lab", "research page", "profile", "home page",
]

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


def _sleep(lo=2.0, hi=4.0):
    time.sleep(random.uniform(lo, hi))


# ---------------------------------------------------------------------------
# Generic email / personal-site helpers
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


def _find_personal_website_url(driver) -> str | None:
    """Return the first personal/lab/faculty website link on the current page."""
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
            return href   # strong keyword match → use immediately

        if ".edu" in domain or ".ac." in domain or domain.count(".") == 1:
            candidates.append(href)

    return candidates[0] if candidates else None


def _get_email_via_profile(driver, profile_url: str) -> str:
    """
    Two-step fallback:
      1. Visit the SCS/MLD profile page and look for email.
      2. Follow any personal/lab website link and look there too.
    """
    logging.info(f"  -> Visiting profile: {profile_url}")
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
        logging.info("  -> No personal website link found on profile.")
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
    Load the MLD core-faculty page and wait for JS to render the SCS
    directory widget.  The widget produces one <li> per person containing:
      - an <a> with the profile URL and the person's name
      - lines of text for title, office, phone
      - an <a href="mailto:…"> for email

    The SCS widget has been observed to use several different wrapper
    class names over the years.  We try a prioritised list and fall back
    to any <li> that contains a mailto link.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logging.info(f"Loading listing page: {DIRECTORY_URL}")
    driver.get(DIRECTORY_URL)

    # The SCS widget can take several seconds to fire its XHR and render.
    # We wait up to 30 s for at least one mailto link to appear anywhere
    # on the page — that signals the widget has finished loading.
    logging.info("Waiting for JS directory widget to render ...")
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href^='mailto:']"))
        )
    except Exception:
        logging.warning("Timed out waiting for mailto links — will try to parse whatever loaded.")

    # Extra pause to let staggered rendering finish
    _sleep(2.0, 3.0)

    # ── Locate faculty cards ────────────────────────────────────────────────
    # Try known SCS widget selectors in priority order.
    CARD_SELECTORS = [
        "ul.people-list li",          # common SCS widget wrapper
        "ul.directory-list li",
        "div.directory-entry",
        "div.people-item",
        "li.person",
        "li",                         # broad fallback — filtered below
    ]

    cards = []
    for sel in CARD_SELECTORS:
        found = driver.find_elements(By.CSS_SELECTOR, sel)
        # Keep only elements that contain a mailto link (confirms it's a person card)
        found = [c for c in found if c.find_elements(By.CSS_SELECTOR, "a[href^='mailto:']")]
        if found:
            logging.info(f"Using selector '{sel}' — found {len(found)} faculty cards.")
            cards = found
            break

    if not cards:
        logging.warning("No faculty cards with mailto links found. Dumping page source snippet for debugging.")
        print(driver.page_source[:3000])
        return []

    faculty = []
    for card in cards:
        # ── Name & profile URL ──────────────────────────────────────────
        name, profile_url = "N/A", ""
        try:
            # The first <a> that is NOT a mailto is the name/profile link
            for a in card.find_elements(By.TAG_NAME, "a"):
                href = a.get_attribute("href") or ""
                if not href.startswith("mailto:") and href.startswith("http"):
                    name        = a.text.strip() or "N/A"
                    profile_url = href
                    break
        except Exception:
            pass

        # ── Full text of the card for field extraction ──────────────────
        card_text = card.text.strip()
        lines     = [ln.strip() for ln in card_text.splitlines() if ln.strip()]

        # Remove the name line so remaining lines are title / office / phone
        detail_lines = [ln for ln in lines if ln != name]

        title = detail_lines[0] if detail_lines else "N/A"

        office = "N/A"
        for ln in detail_lines:
            if "Gates" in ln or "Newell" in ln or re.match(r"^\d{3,4}\b", ln):
                office = ln
                break

        phone = "N/A"
        for ln in detail_lines:
            if re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", ln):
                phone = ln
                break

        # ── Email — prefer mailto link ──────────────────────────────────
        email = "N/A"
        try:
            mailto = card.find_element(By.CSS_SELECTOR, "a[href^='mailto:']")
            email  = mailto.get_attribute("href").replace("mailto:", "").split("?")[0].strip()
        except Exception:
            for m in EMAIL_RE.findall(card_text):
                if "example" not in m and "noreply" not in m:
                    email = m
                    break

        if name == "N/A" and email == "N/A":
            continue   # skip empty/ghost rows

        faculty.append({
            "Name":        name,
            "Title":       title,
            "Office":      office,
            "Phone":       phone,
            "Profile URL": profile_url,
            "Email":       email,
        })

    logging.info(f"Parsed {len(faculty)} faculty records from the listing page.")
    return faculty


# ---------------------------------------------------------------------------
# Email enrichment fallback
# ---------------------------------------------------------------------------

def enrich_with_emails(driver, faculty: list[dict]) -> list[dict]:
    missing = [p for p in faculty if p.get("Email", "N/A") in ("N/A", "", None)]
    logging.info(
        f"{len(missing)} of {len(faculty)} records missing email — "
        "will visit profile pages."
    )

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
    parser = argparse.ArgumentParser(description="CMU MLD Core Faculty Scraper")
    parser.add_argument("--no-emails", action="store_true",
                        help="Skip email enrichment (names/titles only).")
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
            logging.info("Email enrichment skipped (--no-emails).")

    finally:
        driver.quit()

    found   = sum(1 for p in faculty if p.get("Email", "N/A") not in ("N/A", "", None))
    missing = len(faculty) - found
    logging.info(f"Done. {found} emails found, {missing} still N/A.")
    logging.info(f"Results saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
