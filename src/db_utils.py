import hashlib
import json
import os
from datetime import datetime

import psycopg2


RAW_COLUMNS = [
    "page",
    "car_type",
    "car_name",
    "year",
    "mileage",
    "transmission",
    "fuel",
    "price",
    "location",
    "brand",
    "model",
    "car_url",
    "image_url",
    "scraped_at",
    "raw_text",
]

# These fields define the identity of a listing state. Scraping metadata such as
# page, batch_id, scraped_at, image_url, and raw_text is excluded so the same car
# attributes produce the same key across different scraping runs.
CAR_HASH_COLUMNS = [
    "car_name",
    "year",
    "mileage",
    "transmission",
    "fuel",
    "price",
    "location",
    "brand",
    "model",
    "car_url",
]


def get_connection():
    return psycopg2.connect(
        host=os.getenv("CARS_DB_HOST", "localhost"),
        port=os.getenv("CARS_DB_PORT", "5433"),
        dbname=os.getenv("CARS_DB_NAME", "cars_dw"),
        user=os.getenv("CARS_DB_USER", "cars_user"),
        password=os.getenv("CARS_DB_PASSWORD", "cars_password"),
    )


def make_batch_id():
    return datetime.utcnow().strftime("%Y%m%d%H%M%S")


def normalize_for_hash(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def hash_values(values):
    # JSON with sorted keys gives a stable payload before hashing, even if the
    # source dictionary order changes.
    payload = json.dumps(
        {key: normalize_for_hash(value) for key, value in values.items()},
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def make_car_hash(row):
    return hash_values({column: row.get(column) for column in CAR_HASH_COLUMNS})
