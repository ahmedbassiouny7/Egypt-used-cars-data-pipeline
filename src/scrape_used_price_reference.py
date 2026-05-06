import argparse
import logging
import os
import re
import time
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

from psycopg2.extras import execute_values
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from db_utils import get_connection, hash_values, make_batch_id


DEFAULT_BASE_URL = "https://eg.hatla2ee.com/en/car/used-prices"
DEFAULT_REMOTE_URL = "http://selenium:4444/wd/hub"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def title_from_slug(value):
    if not value:
        return None
    return unquote(value).replace("-", " ").strip().title()


def normalize_key(value):
    return clean_text(value).lower()


def numeric_price(value):
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return int(digits) if digits else None


def parse_model_year(model_name, brand=None):
    # Reference rows combine model and year in one label, for example
    # "Toyota Corolla 2020". Split that into fields for matching in transform.
    model_name = clean_text(model_name)
    match = re.search(r"\b(19\d{2}|20\d{2})\b", model_name)
    model_year = int(match.group(1)) if match else None
    model = re.sub(r"\b(19\d{2}|20\d{2})\b", "", model_name).strip()
    if brand:
        model = re.sub(rf"^{re.escape(brand)}\s+", "", model, flags=re.IGNORECASE).strip()
    model = re.sub(r"\s+", " ", model)
    return model or None, model_year


def make_driver(remote_url, page_load_timeout):
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Remote(command_executor=remote_url, options=options)
    driver.set_page_load_timeout(page_load_timeout)
    return driver


def get_brand_slug(url):
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) < 4 or parts[-2] != "used-prices":
        return None
    return parts[-1]


def get_brand_links(driver, base_url, timeout):
    logging.info("Scraping used-price brand links: %s", base_url)
    driver.get(base_url)
    wait = WebDriverWait(driver, timeout)
    wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, 'a[href*="/en/car/used-prices/"]')))

    links = {}
    for anchor in driver.find_elements(By.CSS_SELECTOR, 'a[href*="/en/car/used-prices/"]'):
        href = anchor.get_attribute("href")
        slug = get_brand_slug(href)
        if not slug:
            continue
        links[slug] = urljoin(base_url, href)

    # Use slugs as keys to remove duplicate links discovered in nav/footer areas.
    logging.info("Found %s used-price brand links.", len(links))
    return sorted(links.items())


def table_text_from_page(driver, timeout):
    wait = WebDriverWait(driver, timeout)
    # The reference-price page may be rendered as a table or as divs. These
    # selectors look for the common price headings rather than a single tag.
    selectors = [
        (
            By.XPATH,
            "//div[contains(@class, 'overflow-hidden') "
            "and contains(@class, 'border') "
            "and contains(@class, 'rounded-xl') "
            "and contains(., 'Average Price') "
            "and contains(., 'Minimum Price') "
            "and contains(., 'Maximum Price')]",
        ),
        (By.CSS_SELECTOR, "table"),
        (
            By.XPATH,
            "//*[contains(., 'Average Price') "
            "and contains(., 'Minimum Price') "
            "and contains(., 'Maximum Price') "
            "and contains(., 'EGP')]",
        ),
    ]

    for by, selector in selectors:
        try:
            wait.until(EC.presence_of_element_located((by, selector)))
            elements = driver.find_elements(by, selector)
            elements = [element for element in elements if "EGP" in element.text]
            if elements:
                return max(elements, key=lambda element: len(element.text)).text
        except TimeoutException:
            continue
    return ""


def parse_reference_rows(text, brand, brand_url, batch_id):
    rows = []
    scraped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = clean_text(text)
    text = re.sub(r"^Model\s+Average Price\s+Minimum Price\s+Maximum Price\s+", "", text, flags=re.IGNORECASE)
    # Capture rows in the form: model/year, average price, minimum, maximum.
    row_pattern = re.compile(
        r"([A-Za-z][A-Za-z0-9\s\-/]+?\b(?:19\d{2}|20\d{2}))\s+"
        r"([\d,]+\s*EGP)\s+"
        r"([\d,]+\s*EGP)\s+"
        r"([\d,]+\s*EGP)",
        flags=re.IGNORECASE,
    )

    for match in row_pattern.finditer(text):
        model_name = clean_text(match.group(1))
        avg_price = numeric_price(match.group(2))
        min_price = numeric_price(match.group(3))
        max_price = numeric_price(match.group(4))
        model, model_year = parse_model_year(model_name, brand=brand)
        # One reference record should exist per brand/model/year. If prices
        # change later, the upsert below refreshes that same reference row.
        reference_hash = hash_values(
            {
                "brand": normalize_key(brand),
                "model": normalize_key(model),
                "model_year": model_year,
            }
        )
        rows.append(
            {
                "reference_hash": reference_hash,
                "batch_id": batch_id,
                "brand": brand,
                "model": model,
                "model_year": model_year,
                "model_name": model_name,
                "average_price": avg_price,
                "minimum_price": min_price,
                "maximum_price": max_price,
                "brand_url": brand_url,
                "scraped_at": scraped_at,
            }
        )

    return rows


def scrape_brand_reference(driver, brand_slug, brand_url, batch_id, timeout):
    brand = title_from_slug(brand_slug)
    logging.info("Scraping reference prices for %s: %s", brand, brand_url)
    driver.get(brand_url)

    final_slug = get_brand_slug(driver.current_url)
    # Some brand pages redirect to another URL when there is no reference table.
    if final_slug != brand_slug:
        logging.warning(
            "Skipping %s because %s redirected to %s.",
            brand,
            brand_url,
            driver.current_url,
        )
        return []

    text = table_text_from_page(driver, timeout)
    rows = parse_reference_rows(text, brand, brand_url, batch_id)
    if not rows:
        logging.info("No rows parsed for %s. Table text preview: %s", brand, clean_text(text)[:500])
    logging.info("Parsed %s reference rows for %s.", len(rows), brand)
    return rows


def ensure_reference_table():
    query = """
        CREATE TABLE IF NOT EXISTS used_car_price_reference (
            reference_hash TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            brand TEXT,
            model TEXT,
            model_year INTEGER,
            model_name TEXT,
            average_price NUMERIC,
            minimum_price NUMERIC,
            maximum_price NUMERIC,
            brand_url TEXT,
            scraped_at TIMESTAMP,
            loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_used_car_price_reference_brand_model_year
            ON used_car_price_reference (brand, model, model_year);
        CREATE INDEX IF NOT EXISTS idx_used_car_price_reference_loaded_at
            ON used_car_price_reference (loaded_at);
    """
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)


def insert_reference_rows(rows):
    if not rows:
        return 0

    # Deduplicate rows within the same page before hitting Postgres.
    rows = list({row["reference_hash"]: row for row in rows}.values())

    columns = [
        "reference_hash",
        "batch_id",
        "brand",
        "model",
        "model_year",
        "model_name",
        "average_price",
        "minimum_price",
        "maximum_price",
        "brand_url",
        "scraped_at",
    ]
    values = [[row.get(column) for column in columns] for row in rows]
    query = f"""
        INSERT INTO used_car_price_reference ({", ".join(columns)})
        VALUES %s
        -- Reference prices are refreshed in place because transformation should
        -- use the latest known reference for each brand/model/year.
        ON CONFLICT (reference_hash) DO UPDATE SET
            batch_id = EXCLUDED.batch_id,
            model_name = EXCLUDED.model_name,
            average_price = EXCLUDED.average_price,
            minimum_price = EXCLUDED.minimum_price,
            maximum_price = EXCLUDED.maximum_price,
            brand_url = EXCLUDED.brand_url,
            scraped_at = EXCLUDED.scraped_at,
            loaded_at = CURRENT_TIMESTAMP
        RETURNING reference_hash
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            inserted_rows = execute_values(cursor, query, values, fetch=True)
    return len(inserted_rows)


def scrape_used_price_reference(base_url, max_brands, brand_slug, sleep_seconds, timeout, remote_url, batch_id, page_load_timeout):
    ensure_reference_table()
    driver = make_driver(remote_url, page_load_timeout)
    total_rows = 0

    try:
        # brand_slug is useful for debugging one brand. Otherwise the DAG scrapes
        # every brand link, optionally capped by HATLA2EE_PRICE_REFERENCE_MAX_BRANDS.
        if brand_slug:
            brand_links = [(brand_slug, f"{base_url.rstrip('/')}/{brand_slug}")]
        else:
            brand_links = get_brand_links(driver, base_url, timeout)
            if max_brands and max_brands > 0:
                brand_links = brand_links[:max_brands]

        for brand_slug, brand_url in brand_links:
            rows = scrape_brand_reference(driver, brand_slug, brand_url, batch_id, timeout)
            inserted_count = insert_reference_rows(rows)
            total_rows += inserted_count
            logging.info(
                "Saved %s reference rows for %s. Total saved this run: %s.",
                inserted_count,
                title_from_slug(brand_slug),
                total_rows,
            )
            time.sleep(sleep_seconds)
    finally:
        driver.quit()

    if total_rows == 0:
        raise RuntimeError("Reference price scrape finished with 0 rows.")
    return total_rows


def main():
    parser = argparse.ArgumentParser(description="Scrape Hatla2ee used-car brand/model reference prices.")
    parser.add_argument("--base-url", default=os.getenv("HATLA2EE_USED_PRICES_URL", DEFAULT_BASE_URL))
    parser.add_argument("--max-brands", type=int, default=int(os.getenv("HATLA2EE_PRICE_REFERENCE_MAX_BRANDS", "0")))
    parser.add_argument("--brand-slug", default=None)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--page-load-timeout", type=int, default=60)
    parser.add_argument("--remote-url", default=os.getenv("SELENIUM_REMOTE_URL", DEFAULT_REMOTE_URL))
    parser.add_argument("--batch-id", default=os.getenv("BATCH_ID") or make_batch_id())
    args = parser.parse_args()

    row_count = scrape_used_price_reference(
        base_url=args.base_url,
        max_brands=args.max_brands,
        brand_slug=args.brand_slug,
        sleep_seconds=args.sleep,
        timeout=args.timeout,
        remote_url=args.remote_url,
        batch_id=args.batch_id,
        page_load_timeout=args.page_load_timeout,
    )
    print(f"Saved {row_count} used-car price reference rows for batch {args.batch_id}")


if __name__ == "__main__":
    main()
