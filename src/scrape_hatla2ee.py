import argparse
import logging
import os
import re
import time
from datetime import datetime
from itertools import count
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import pandas as pd
from psycopg2.extras import execute_values
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from db_utils import RAW_COLUMNS, get_connection, make_batch_id, make_car_hash


DEFAULT_BASE_URL = "https://eg.hatla2ee.com/en/car/page"
DEFAULT_REMOTE_URL = "http://selenium:4444/wd/hub"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def first_match(pattern, text):
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


def title_from_slug(value):
    if not value:
        return None
    return unquote(value).replace("-", " ").strip().title()


def parse_brand_model_from_url(car_url):
    # Listing URLs carry useful structured data, for example:
    # /en/car/nissan/qashqai/7184566 -> brand=Nissan, model=Qashqai.
    if not car_url:
        return None, None

    parts = [part for part in urlparse(car_url).path.split("/") if part]
    try:
        car_index = parts.index("car")
    except ValueError:
        return None, None

    brand = title_from_slug(parts[car_index + 1]) if len(parts) > car_index + 1 else None
    model = title_from_slug(parts[car_index + 2]) if len(parts) > car_index + 2 else None
    return brand, model


def extract_location(text, brand=None, model=None):
    # Hatla2ee cards are scraped as one block of text. Location usually appears
    # after the price and before the repeated brand/model/call text.
    price_match = re.search(r"[\d,]+\s*EGP", text, flags=re.IGNORECASE)
    if not price_match:
        return None

    after_price = text[price_match.end():].strip()
    after_price = re.sub(r"\bCall\b.*$", "", after_price, flags=re.IGNORECASE).strip()

    stop_tokens = [token for token in [brand, model] if token]
    for token in stop_tokens:
        token_match = re.search(rf"\b{re.escape(token)}\b", after_price, flags=re.IGNORECASE)
        if token_match:
            after_price = after_price[: token_match.start()].strip()

    after_price = clean_text(after_price)
    return after_price if after_price else None


def extract_car_name(text, brand=None, model=None):
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if not year_match:
        return clean_text(f"{brand or ''} {model or ''}") or None

    before_year = text[: year_match.start()].strip()
    before_year = re.sub(r"^\d+\s*/\s*\d+\s+", "", before_year).strip()
    before_year = re.sub(r"^Turbo\s+", "", before_year, flags=re.IGNORECASE).strip()
    before_year = clean_text(before_year)
    return before_year or clean_text(f"{brand or ''} {model or ''}") or None


def extract_model_from_name(car_name, brand):
    if not car_name or not brand:
        return None
    pattern = rf"^{re.escape(brand)}\s+"
    model = re.sub(pattern, "", car_name, flags=re.IGNORECASE).strip()
    return model or None


def is_valid_listing_url(car_url):
    # Category links such as /en/car/kia can match the broad selector, so keep
    # only detail pages that include brand, model, and listing id path parts.
    if not car_url:
        return False
    parts = [part for part in urlparse(car_url).path.split("/") if part]
    try:
        car_index = parts.index("car")
    except ValueError:
        return False
    return len(parts) > car_index + 3
    return None


def make_driver(remote_url, page_load_timeout):
    options = webdriver.ChromeOptions()
    # The browser runs inside the Selenium container, so Chrome needs container-
    # friendly flags and a normal user agent for more reliable rendering.
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Remote(
        command_executor=remote_url,
        options=options,
    )
    driver.set_page_load_timeout(page_load_timeout)
    return driver


def safe_find_text(element, by, selector):
    try:
        return clean_text(element.find_element(by, selector).text)
    except Exception:
        return None


def safe_find_attr(element, by, selector, attribute):
    try:
        return element.find_element(by, selector).get_attribute(attribute)
    except Exception:
        return None


def parse_card(card, page_number):
    # The website has changed markup a few times, so parsing relies mostly on
    # visible card text plus the listing URL instead of one fragile CSS path.
    text = clean_text(card.text)
    href = card.get_attribute("href") if card.tag_name.lower() == "a" else None
    if not href:
        href = safe_find_attr(card, By.CSS_SELECTOR, 'a[href*="/en/car/"]', "href")
    image_url = (
        safe_find_attr(card, By.CSS_SELECTOR, "img", "src")
        or safe_find_attr(card, By.CSS_SELECTOR, "img", "data-src")
    )

    car_url = urljoin("https://eg.hatla2ee.com", href) if href else None
    brand, model = parse_brand_model_from_url(car_url)

    car_name = extract_car_name(text, brand=brand, model=model)
    if not model:
        model = extract_model_from_name(car_name, brand)

    return {
        "page": page_number,
        "car_type": "used",
        "car_name": car_name,
        "year": first_match(r"\b(19\d{2}|20\d{2})\b", text),
        "mileage": first_match(r"([\d,]+\s*KM)", text),
        "transmission": first_match(r"\b(Automatic|Manual)\b", text),
        "fuel": first_match(r"\b(Plug-in Hybrid|Natural Gas|Electric|Hybrid|Diesel|Gas)\b", text),
        "price": first_match(r"([\d,]+\s*EGP)", text),
        "location": extract_location(text, brand=brand, model=model),
        "brand": brand,
        "model": model,
        "car_url": car_url,
        "image_url": image_url,
        "scraped_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "raw_text": text,
    }


def find_cards(driver, timeout):
    wait = WebDriverWait(driver, timeout)
    # Try specific selectors first, then broader link selectors as a fallback.
    selectors = [
        (By.XPATH, "//div[contains(@class, 'auto-rows-max')]/span/div[contains(@class, 'card')]"),
        (By.CSS_SELECTOR, "div.card"),
        (By.CSS_SELECTOR, 'a[href*="/en/car/"]'),
        (By.CSS_SELECTOR, 'a[href*="/car/"]'),
    ]

    last_error = None
    for by, selector in selectors:
        try:
            wait.until(EC.presence_of_all_elements_located((by, selector)))
            cards = driver.find_elements(by, selector)
            if cards:
                return cards
        except TimeoutException as exc:
            last_error = exc

    if last_error:
        logging.warning("No listing cards found before timeout.")
        return []
    return []


def save_debug_artifacts(driver, page_number, debug_dir):
    # When selectors fail, saving the page source and screenshot makes it much
    # easier to inspect the actual HTML rendered by Selenium.
    debug_dir = Path(debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)

    html_path = debug_dir / f"page_{page_number}_debug.html"
    screenshot_path = debug_dir / f"page_{page_number}_debug.png"

    html_path.write_text(driver.page_source, encoding="utf-8")
    driver.save_screenshot(str(screenshot_path))

    logging.info("Saved debug HTML to %s", html_path)
    logging.info("Saved debug screenshot to %s", screenshot_path)
    logging.info("Current URL: %s", driver.current_url)
    logging.info("Page title: %s", driver.title)


def scrape_page(driver, base_url, page_number, timeout, debug_dir):
    url = f"{base_url}/{page_number}"
    logging.info("Scraping page %s: %s", page_number, url)
    driver.get(url)
    cards = find_cards(driver, timeout)
    logging.info("Found %s candidate cards on page %s", len(cards), page_number)
    if not cards:
        save_debug_artifacts(driver, page_number, debug_dir)

    rows = []
    for card in cards:
        row = parse_card(card, page_number)
        if is_valid_listing_url(row["car_url"]):
            rows.append(row)
    logging.info("Parsed %s usable rows on page %s", len(rows), page_number)
    return rows


def prepare_raw_rows(rows, batch_id):
    prepared_rows = []
    for row in rows:
        # car_hash is the primary key used by Postgres to skip exact duplicates.
        car_hash = make_car_hash(row)
        if not car_hash:
            continue
        prepared_rows.append(
            {
                **row,
                "car_hash": car_hash,
                "batch_id": batch_id,
            }
        )
    return prepared_rows


def insert_raw_rows(rows):
    if not rows:
        return 0

    insert_columns = ["car_hash", "batch_id", *RAW_COLUMNS]
    values = [[row.get(column) for column in insert_columns] for row in rows]

    query = f"""
        INSERT INTO raw_used_cars ({", ".join(insert_columns)})
        VALUES %s
        -- Duplicate listing states are expected across repeated scrapes.
        ON CONFLICT (car_hash) DO NOTHING
        RETURNING car_hash
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            inserted_rows = execute_values(cursor, query, values, fetch=True)
    return len(inserted_rows)


def page_numbers_to_scrape(max_pages):
    # max_pages=0 means open-ended mode. In production, a fixed safety cap is
    # safer if the website repeats the last page instead of returning empty rows.
    if max_pages and max_pages > 0:
        return range(1, max_pages + 1)
    return count(1)


def scrape_used_cars(base_url, max_pages, output_path, sleep_seconds, timeout, remote_url, batch_id, page_load_timeout, debug_dir):
    driver = make_driver(remote_url, page_load_timeout)
    # Keep a copy for the optional debug CSV, but insert page-by-page so progress
    # is visible in Postgres and a long scrape does not lose all rows on failure.
    all_prepared_rows = []
    total_parsed_count = 0
    total_inserted_count = 0
    total_skipped_count = 0

    try:
        for page_number in page_numbers_to_scrape(max_pages):
            rows = scrape_page(driver, base_url, page_number, timeout, debug_dir)
            if not rows:
                logging.info("Stopping scrape because page %s returned no rows.", page_number)
                break

            prepared_rows = prepare_raw_rows(rows, batch_id)
            inserted_count = insert_raw_rows(prepared_rows)
            skipped_count = len(prepared_rows) - inserted_count

            all_prepared_rows.extend(prepared_rows)
            total_parsed_count += len(prepared_rows)
            total_inserted_count += inserted_count
            total_skipped_count += skipped_count

            logging.info(
                "Page %s saved: inserted %s new rows, skipped %s duplicates. "
                "Batch progress: parsed %s, inserted %s, skipped %s.",
                page_number,
                inserted_count,
                skipped_count,
                total_parsed_count,
                total_inserted_count,
                total_skipped_count,
            )
            time.sleep(sleep_seconds)
    finally:
        driver.quit()

    if not all_prepared_rows:
        raise RuntimeError(
            "Scrape finished with 0 parsed rows. Check Selenium debug HTML/screenshot "
            f"in {debug_dir}."
        )

    logging.info(
        "Scrape finished for batch %s. Parsed %s rows, inserted %s new rows, skipped %s duplicates.",
        batch_id,
        total_parsed_count,
        total_inserted_count,
        total_skipped_count,
    )

    if output_path:
        df = pd.DataFrame(all_prepared_rows)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)

    return total_inserted_count


def main():
    parser = argparse.ArgumentParser(description="Scrape Hatla2ee used-car listings with Selenium.")
    parser.add_argument("--base-url", default=os.getenv("HATLA2EE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--max-pages", type=int, default=int(os.getenv("HATLA2EE_MAX_PAGES", "0")))
    parser.add_argument("--output", default=None)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--page-load-timeout", type=int, default=60)
    parser.add_argument("--remote-url", default=os.getenv("SELENIUM_REMOTE_URL", DEFAULT_REMOTE_URL))
    parser.add_argument("--batch-id", default=os.getenv("BATCH_ID") or make_batch_id())
    parser.add_argument("--debug-dir", default="/opt/airflow/data/raw/debug")
    args = parser.parse_args()

    inserted_count = scrape_used_cars(
        base_url=args.base_url,
        max_pages=args.max_pages,
        output_path=args.output,
        sleep_seconds=args.sleep,
        timeout=args.timeout,
        remote_url=args.remote_url,
        batch_id=args.batch_id,
        page_load_timeout=args.page_load_timeout,
        debug_dir=args.debug_dir,
    )
    print(f"Inserted {inserted_count} new raw rows into raw_used_cars for batch {args.batch_id}")


if __name__ == "__main__":
    main()
