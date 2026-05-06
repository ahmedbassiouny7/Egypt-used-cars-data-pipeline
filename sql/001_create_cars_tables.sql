-- Raw table: keeps the listing values as scraped. Most fields stay as TEXT so
-- the transformation layer can own parsing and data quality rules.
CREATE TABLE IF NOT EXISTS raw_used_cars (
    car_hash TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    page INTEGER,
    car_type TEXT,
    car_name TEXT,
    year TEXT,
    mileage TEXT,
    transmission TEXT,
    fuel TEXT,
    price TEXT,
    location TEXT,
    brand TEXT,
    model TEXT,
    car_url TEXT,
    image_url TEXT,
    scraped_at TIMESTAMP,
    raw_text TEXT,
    loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_raw_used_cars_batch_id ON raw_used_cars (batch_id);
CREATE INDEX IF NOT EXISTS idx_raw_used_cars_loaded_at ON raw_used_cars (loaded_at);

-- Reference table: one row per brand/model/year from Hatla2ee's used-price
-- reference pages. Transformation uses it before falling back to scraped averages.
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

-- Cleaned table: Power BI-ready analytical records. car_hash remains the key so
-- exact duplicate listing states are skipped across repeated DAG runs.
CREATE TABLE IF NOT EXISTS cleaned_used_cars (
    car_hash TEXT PRIMARY KEY,
    car_name TEXT,
    year INTEGER,
    mileage INTEGER,
    transmission TEXT,
    fuel TEXT,
    price_original NUMERIC,
    price NUMERIC,
    price_was_imputed BOOLEAN NOT NULL DEFAULT FALSE,
    price_imputation_level TEXT,
    location TEXT,
    city TEXT,
    governorate TEXT,
    brand TEXT,
    model TEXT,
    car_url TEXT,
    scraped_at TIMESTAMP,
    batch_id TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cleaned_used_cars_brand ON cleaned_used_cars (brand);
CREATE INDEX IF NOT EXISTS idx_cleaned_used_cars_governorate ON cleaned_used_cars (governorate);
CREATE INDEX IF NOT EXISTS idx_cleaned_used_cars_created_at ON cleaned_used_cars (created_at);
