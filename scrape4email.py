import requests
from bs4 import BeautifulSoup
import csv
import time
import random
import logging

# Configure logging for execution monitoring
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- CONFIGURATION ---
# Replace with the actual NYU directory URL and the specific query parameters
DIRECTORY_URL = "https://cds.nyu.edu/joint-faculty/" 
OUTPUT_FILE = "nyu_target_faculty.csv"

# Standard browser headers to avoid basic 403 Forbidden blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# The categories we identified as high-priority
TARGET_CATEGORIES = ["Core Faculty", "Research Faculty"]

def fetch_page(url):
    """Fetches the HTML content with a randomized delay to respect server load."""
    try:
        # Sleep between 1.5 to 3.5 seconds to mimic human interaction
        time.sleep(random.uniform(1.5, 3.5)) 
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch {url}: {e}")
        return None

def extract_faculty_data(html_content):
    """
    Parses the DOM to extract Name, Title, and Email.
    CRITICAL: You must inspect the NYU webpage and update the CSS selectors below.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    print(soup.prettify())  # Debug: Print the DOM structure to identify correct selectors
    faculty_list = []

    # Update 'div.faculty-card-class' with the actual class wrapping each professor's profile
    cards = soup.find_all('div', class_='faculty-card-class') 
    print(f"Found {len(cards)} faculty cards on the page.")

    for card in cards:
        try:
            # Update these selectors based on the actual DOM structure
            name_elem = card.find('h3', class_='name-class')
            title_elem = card.find('span', class_='title-class')
            email_elem = card.find('a', href=lambda href: href and "mailto:" in href)

            if not (name_elem and title_elem):
                continue

            name = name_elem.get_text(strip=True)
            title = title_elem.get_text(strip=True)
            email = email_elem['href'].replace('mailto:', '').strip() if email_elem else "N/A"

            # Filter logic: Only keep if the title matches our target categories
            if any(target in title for target in TARGET_CATEGORIES):
                faculty_list.append({
                    "Name": name,
                    "Title": title,
                    "Email": email
                })
                logging.info(f"Matched: {name} - {title}")

        except Exception as e:
            logging.warning(f"Error parsing a card: {e}")
            continue

    return faculty_list

def save_to_csv(data, filename):
    """Writes the extracted dictionary to a CSV file."""
    if not data:
        logging.warning("No data extracted. Check your CSS selectors.")
        return

    keys = data[0].keys()
    with open(filename, 'w', newline='', encoding='utf-8') as output_file:
        dict_writer = csv.DictWriter(output_file, fieldnames=keys)
        dict_writer.writeheader()
        dict_writer.writerows(data)
    logging.info(f"Successfully saved {len(data)} records to {filename}")

if __name__ == "__main__":
    logging.info("Starting extraction process...")
    html = fetch_page(DIRECTORY_URL)
    
    if html:
        # Note: If the directory has pagination (Page 1, 2, 3), you will need to wrap 
        # this in a loop that modifies the URL parameters (e.g., ?page=2)
        faculty_data = extract_faculty_data(html)
        save_to_csv(faculty_data, OUTPUT_FILE)
    else:
        logging.error("Execution halted due to fetch failure.")