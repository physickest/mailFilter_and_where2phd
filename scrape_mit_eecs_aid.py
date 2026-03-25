"""
MIT EECS Faculty AI+D Email Scraper
=====================================
Scrapes https://www.eecs.mit.edu/role/faculty-aid/

The page is rendered by a FacetWP/WordPress plugin — all faculty cards
including emails are injected by JavaScript after page load.  Selenium
waits for the cards to appear, then extracts all fields directly from
the listing (no profile-page clicks needed for most entries).

For any person still missing an email after the listing parse, the scraper
falls back to:
  1. Their individual EECS profile page
  2. Any personal/lab website linked from that profile

Requirements:
    pip install selenium webdriver-manager

Usage:
    python scrape_mit_eecs_aid.py                        # full run
    python scrape_mit_eecs_aid.py --no-emails            # names/titles only
    python scrape_mit_eecs_aid.py --resume existing.csv  # skip found emails
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

DIRECTORY_URL = "https://www.eecs.mit.edu/role/faculty-aid/"
OUTPUT_FILE   = "mit_eecs_faculty_aid.csv"
FIELDNAMES    = ["Name", "Title", "Office", "Phone", "Profile URL", "Email"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

_SKIP_DOMAINS = {
    "eecs.mit.edu", "mit.edu", "csail.mit.edu",
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


def _sleep(lo=2.0, hi=3.5):
    time.sleep(random.uniform(lo, hi))


# ---------------------------------------------------------------------------
# Generic email / personal-site helpers (used as fallback)
# ---------------------------------------------------------------------------

def _extract_email_from_page(driver) -> str:
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
# Pagination helper
# ---------------------------------------------------------------------------

def _get_total_pages(driver) -> int:
    """
    FacetWP renders a pagination widget.  We look for the last numbered
    page button to know how many pages to iterate.  Returns 1 if we
    can't find pagination (single-page result set).
    """
    from selenium.webdriver.common.by import By

    try:
        page_btns = driver.find_elements(By.CSS_SELECTOR, "a.facetwp-page")
        nums = []
        for btn in page_btns:
            txt = btn.text.strip()
            if txt.isdigit():
                nums.append(int(txt))
        return max(nums) if nums else 1
    except Exception:
        return 1


def _go_to_page(driver, page_num: int):
    """Click the FacetWP pagination button for the given page number."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    if page_num == 1:
        return  # already on page 1

    try:
        btn = driver.find_element(
            By.XPATH,
            f"//a[contains(@class,'facetwp-page') and normalize-space(text())='{page_num}']"
        )
        driver.execute_script("arguments[0].click();", btn)
        # Wait for the cards to refresh after the AJAX call
        WebDriverWait(driver, 15).until(
            EC.staleness_of(btn)
        )
        _sleep(2.0, 3.0)
    except Exception as e:
        logging.warning(f"  Could not navigate to page {page_num}: {e}")


# ---------------------------------------------------------------------------
# Card parser
# ---------------------------------------------------------------------------

def _parse_cards(driver) -> list[dict]:
    """
    Parse all person cards currently visible on the page.

    MIT EECS uses article.people-list-item (or similar) cards containing:
      - <h3> or <h2> with an <a> linking to the profile (name)
      - <p class="title"> or similar for the role/title
      - <p> containing office (e.g. "32-G920")
      - <p> containing phone  (e.g. "(617) 253-6042")
      - <a href="mailto:…"> for email
    """
    from selenium.webdriver.common.by import By

    # MIT EECS FacetWP cards — try selectors in priority order
    CARD_SELECTORS = [
        "article.people-list-item",
        "div.people-list-item",
        "li.people-list-item",
        "div.person-card",
        "article.person",
        # Broad fallback: any element with a mailto inside
    ]

    cards = []
    for sel in CARD_SELECTORS:
        found = driver.find_elements(By.CSS_SELECTOR, sel)
        if found:
            logging.info(f"Card selector '{sel}' matched {len(found)} elements.")
            cards = found
            break

    # Last-resort: find all elements that contain a mailto link
    if not cards:
        logging.warning("No named card selector matched — falling back to mailto-containing elements.")
        all_with_mailto = driver.find_elements(By.CSS_SELECTOR, "a[href^='mailto:']")
        # Walk up to a reasonable container (parent or grandparent)
        seen = set()
        containers = []
        for a in all_with_mailto:
            try:
                parent = a.find_element(By.XPATH, "./ancestor::*[self::article or self::li or self::div][1]")
                pid = parent.id
                if pid not in seen:
                    seen.add(pid)
                    containers.append(parent)
            except Exception:
                pass
        cards = containers
        logging.info(f"Fallback: found {len(cards)} containers via mailto ancestry.")

    results = []
    for card in cards:
        # ── Name & profile URL ──────────────────────────────────────────
        name, profile_url = "N/A", ""
        try:
            # First non-mailto anchor with text is the name link
            for a in card.find_elements(By.TAG_NAME, "a"):
                href = a.get_attribute("href") or ""
                if not href.startswith("mailto:") and href.startswith("http"):
                    raw_name = a.text.strip()
                    if raw_name:
                        name        = raw_name
                        profile_url = href
                        break
        except Exception:
            pass

        card_text = card.text.strip()
        lines     = [ln.strip() for ln in card_text.splitlines() if ln.strip()]

        # Remove the name line
        detail_lines = [ln for ln in lines if ln != name]

        # Title — first detail line
        title = detail_lines[0] if detail_lines else "N/A"

        # Office — MIT rooms look like "32-G920" or "38-444"
        office = "N/A"
        for ln in detail_lines:
            if re.match(r"^\d{2,3}-[A-Z]?\d{3,4}", ln) or "Office:" in ln:
                office = ln.replace("Office:", "").strip()
                break

        # Phone — (617) NNN-NNNN
        phone = "N/A"
        for ln in detail_lines:
            if re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", ln):
                phone = ln
                break

        # Email — prefer mailto link; fall back to regex
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
            continue

        results.append({
            "Name":        name,
            "Title":       title,
            "Office":      office,
            "Phone":       phone,
            "Profile URL": profile_url,
            "Email":       email,
        })

    return results


# ---------------------------------------------------------------------------
# Main listing scraper (handles FacetWP pagination)
# ---------------------------------------------------------------------------

def scrape_listing(driver) -> list[dict]:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    logging.info(f"Loading listing page: {DIRECTORY_URL}")
    driver.get(DIRECTORY_URL)

    # FacetWP fires an AJAX call on page load.  Wait for email links or
    # any people-list content to appear (up to 30 s).
    logging.info("Waiting for FacetWP to render faculty cards ...")
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href^='mailto:']"))
        )
    except Exception:
        logging.warning("Timed out waiting for mailto links.  Attempting parse anyway.")

    _sleep(1.5, 2.5)  # let any staggered rendering settle

    total_pages = _get_total_pages(driver)
    logging.info(f"Detected {total_pages} page(s) of results.")

    all_faculty = []

    for page in range(1, total_pages + 1):
        if page > 1:
            logging.info(f"Navigating to page {page} ...")
            _go_to_page(driver, page)

        page_records = _parse_cards(driver)
        logging.info(f"  Page {page}: parsed {len(page_records)} records.")
        all_faculty.extend(page_records)

    logging.info(f"Total records parsed from listing: {len(all_faculty)}")
    return all_faculty


# ---------------------------------------------------------------------------
# Email enrichment fallback
# ---------------------------------------------------------------------------

def enrich_with_emails(driver, faculty: list[dict]) -> list[dict]:
    missing = [p for p in faculty if p.get("Email", "N/A") in ("N/A", "", None)]
    logging.info(
        f"{len(missing)} of {len(faculty)} records missing email — "
        "visiting profile pages as fallback."
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
    parser = argparse.ArgumentParser(description="MIT EECS Faculty AI+D Scraper")
    parser.add_argument("--no-emails", action="store_true",
                        help="Skip email enrichment (names/titles only).")
    parser.add_argument("--resume", metavar="CSV",
                        help="Resume from existing CSV; rows with emails are skipped.")
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
