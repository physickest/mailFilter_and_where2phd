import requests
from bs4 import BeautifulSoup
import csv
import time
import random
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

DIRECTORY_URL = "https://cds.nyu.edu/joint-faculty/"
OUTPUT_FILE = "nyu_joint_faculty.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def fetch_page(url):
    """Fetches HTML content with a randomized delay."""
    try:
        time.sleep(random.uniform(1.5, 3.5))
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch {url}: {e}")
        return None

def fetch_profile_email(profile_url):
    """
    Visits an individual faculty profile page to look for an email address.
    Returns the email string or 'N/A'.
    """
    try:
        time.sleep(random.uniform(1.0, 2.5))
        response = requests.get(profile_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        email_elem = soup.find('a', href=lambda h: h and 'mailto:' in h)
        if email_elem:
            return email_elem['href'].replace('mailto:', '').strip()
    except Exception as e:
        logging.warning(f"Could not fetch profile {profile_url}: {e}")
    return "N/A"

def extract_faculty_data(html_content, fetch_emails=True):
    """
    Parses the WP Team Pro grid used on the NYU CDS joint-faculty page.
    Each card is a  div.sp-team-pro-item  containing:
      - h2.sptp-name > a  → name + profile URL
      - p.sptp-profession-text  → title / role
    Emails are NOT on the listing page; set fetch_emails=True to follow each
    profile link and scrape them there (slower but more complete).
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    cards = soup.find_all('div', class_='sp-team-pro-item')
    logging.info(f"Found {len(cards)} faculty cards on the page.")

    faculty_list = []

    for card in cards:
        try:
            name_elem = card.find('h2', class_='sptp-name')
            title_elem = card.find('p', class_='sptp-profession-text')

            if not name_elem:
                continue

            name_link = name_elem.find('a')
            name = name_link.get_text(strip=True) if name_link else name_elem.get_text(strip=True)
            profile_url = name_link['href'] if name_link and name_link.get('href') else ""
            title = title_elem.get_text(strip=True) if title_elem else "N/A"

            email = "N/A"
            if fetch_emails and profile_url:
                logging.info(f"Fetching profile for {name} …")
                email = fetch_profile_email(profile_url)

            faculty_list.append({
                "Name": name,
                "Title": title,
                "Profile URL": profile_url,
                "Email": email,
            })
            logging.info(f"Captured: {name} | {title} | {email}")

        except Exception as e:
            logging.warning(f"Error parsing a card: {e}")
            continue

    return faculty_list

def save_to_csv(data, filename):
    """Writes the extracted list of dicts to a CSV file."""
    if not data:
        logging.warning("No data to save.")
        return

    keys = data[0].keys()
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(data)
    logging.info(f"Saved {len(data)} records to {filename}")

if __name__ == "__main__":
    # Set fetch_emails=False for a fast name/title-only run;
    # set to True to follow each profile page and attempt to scrape emails.
    FETCH_EMAILS = True

    logging.info("Starting extraction …")
    html = fetch_page(DIRECTORY_URL)

    if html:
        faculty_data = extract_faculty_data(html, fetch_emails=FETCH_EMAILS)
        save_to_csv(faculty_data, OUTPUT_FILE)
    else:
        logging.error("Halted: could not fetch the listing page.")
