# Egypt Used Cars Data Pipeline

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Apache Airflow](https://img.shields.io/badge/Apache%20Airflow-2.9.3-017CEE?style=for-the-badge&logo=apacheairflow&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-13-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Selenium](https://img.shields.io/badge/Selenium-Chrome-43B02A?style=for-the-badge&logo=selenium&logoColor=white)
![Power BI](https://img.shields.io/badge/Power%20BI-Dashboard-F2C811?style=for-the-badge&logo=powerbi&logoColor=black)

Automated data engineering pipeline for scraping, storing, cleaning, and analyzing Egypt used-car listings from Hatla2ee.

The project is designed as a small production-style workflow: Selenium handles dynamic scraping, Airflow schedules the jobs, PostgreSQL stores raw and cleaned data, Python/pandas performs transformation, and Power BI connects to the final analytical table.

## Project Overview

```text
Hatla2ee Website
    |
    | Selenium scraping
    v
Airflow scrape DAG
    |
    |--> used_car_price_reference
    |--> raw_used_cars
    |
    v
Airflow transform DAG
    |
    | Python + pandas cleaning
    v
cleaned_used_cars
    |
    v
Power BI Dashboard
```

## Tech Stack

| Layer | Tool | Purpose |
|---|---|---|
| Orchestration | Apache Airflow | Run scraping and transformation DAGs |
| Runtime | Docker Compose | Run Airflow, Selenium, and PostgreSQL locally |
| Scraping | Selenium + Chrome | Scrape dynamic listing pages and reference prices |
| Storage | PostgreSQL 13 | Store raw, reference, and cleaned data |
| Transformation | Python + pandas | Clean prices, mileage, year, location, and quality flags |
| BI | Power BI | Build the final business dashboard |

## Repository Structure

```text
DEP/
|-- dags/
|   |-- hatla2ee_scrape_dag.py
|   `-- hatla2ee_transform_dag.py
|-- data/
|   |-- raw/
|   `-- backups/
|-- logs/
|-- sql/
|   `-- 001_create_cars_tables.sql
|-- src/
|   |-- db_utils.py
|   |-- scrape_hatla2ee.py
|   |-- scrape_used_price_reference.py
|   `-- transform_used_cars.py
|-- docker-compose.yml
|-- .env.example
`-- README.md
```

## Pipeline DAGs

| DAG | Purpose | Schedule |
|---|---|---|
| `hatla2ee_scrape_used_cars_raw` | Scrapes reference prices and raw used-car listings | Daily |
| `hatla2ee_transform_used_cars_cleaned` | Cleans raw data and loads the analytical table | Triggered by scrape DAG |

Run order:

1. `scrape_price_reference`
2. `scrape_used_cars`
3. `trigger_transform_used_cars`
4. `transform_used_cars`

## Database Tables

| Table | Description |
|---|---|
| `raw_used_cars` | Raw scraped listing rows, stored mostly as text |
| `used_car_price_reference` | Hatla2ee brand/model/year reference prices |
| `cleaned_used_cars` | Cleaned analytical table used by Power BI |

Important fields:

| Field | Meaning |
|---|---|
| `car_hash` | Primary key generated from car attributes |
| `batch_id` | Airflow run timestamp used to identify a scrape batch |
| `loaded_at` | Raw table insert timestamp |
| `created_at` | Cleaned table insert timestamp |
| `price_was_imputed` | Whether the price was estimated during cleaning |
| `price_imputation_level` | The imputation method: model, brand, or global average |

Duplicate handling:

```sql
ON CONFLICT (car_hash) DO NOTHING
```

This prevents exact duplicate car records from being inserted again.

## Cleaning Logic

The transformation script cleans the raw scraped table into a Power BI-ready table.

Main steps:

| Column | Cleaning |
|---|---|
| `price` | Extract digits, convert to numeric, impute invalid prices |
| `mileage` | Extract digits and convert to numeric |
| `year` | Convert to integer |
| `location` | Normalize text and split into city/governorate |
| `brand` / `model` | Standardize text |
| `car_hash` | Preserve unique analytical key |

Price imputation levels:

| Level | Meaning |
|---|---|
| `model_avg` | Filled using average price for the same model |
| `brand_avg` | Filled using average price for the same brand |
| `global_avg` | Filled using average valid price across all cars |
| `null` | Original price was valid and not imputed |

## Quick Start

Copy the example environment file:

```bash
copy .env.example .env
```

Start the full stack:

```bash
docker compose up -d
```

Open Airflow:

```text
http://localhost:8080
```

Default login:

```text
username: airflow
password: airflow
```

View the Selenium browser session:

```text
http://localhost:7900
```

## Scraping Settings

Edit `.env` before starting the containers.

```env
HATLA2EE_MAX_PAGES=5
```

Recommended values:

| Value | Usage |
|---|---|
| `5` | Quick testing |
| `570` | Larger full-site scrape safety cap |
| `0` | Auto mode, scrape until the first empty page |

Reference price scraper:

```env
HATLA2EE_PRICE_REFERENCE_MAX_BRANDS=0
```

| Value | Usage |
|---|---|
| `0` | Scrape all reference-price brands |
| `2` | Quick testing |

## PostgreSQL Connection

Power BI and database clients can connect using:

| Setting | Value |
|---|---|
| Server | `localhost` |
| Port | `5433` |
| Database | `cars_dw` |
| Username | `cars_user` |
| Password | `cars_password` |
| Main table | `cleaned_used_cars` |

Power BI target table:

```text
cleaned_used_cars
```

## Useful Commands

Check running containers:

```bash
docker compose ps
```

Check raw row count:

```bash
docker compose exec cars-postgres psql -U cars_user -d cars_dw -c "SELECT COUNT(*) FROM raw_used_cars;"
```

Check cleaned row count:

```bash
docker compose exec cars-postgres psql -U cars_user -d cars_dw -c "SELECT COUNT(*) FROM cleaned_used_cars;"
```

Check rows by batch:

```bash
docker compose exec cars-postgres psql -U cars_user -d cars_dw -c "SELECT batch_id, COUNT(*) FROM raw_used_cars GROUP BY batch_id ORDER BY batch_id DESC;"
```

Export cleaned data to CSV:

```bash
docker compose exec -T cars-postgres psql -U cars_user -d cars_dw -c "COPY cleaned_used_cars TO STDOUT WITH CSV HEADER" > data/backups/cleaned_used_cars.csv
```

Stop the stack:

```bash
docker compose down
```

Reset all Docker database volumes:

```bash
docker compose down -v
```

## Power BI Dashboard Ideas

Recommended KPI cards:

| KPI | Description |
|---|---|
| Total Listings | Count of cleaned cars |
| Average Price | Average cleaned price |
| Median Price | Better central price measure |
| Average Mileage | Mileage level across selected cars |
| Imputed Price % | Data quality indicator |
| Unique Brands | Market coverage |

Recommended visuals:

| Visual | Purpose |
|---|---|
| Top brands by listings | Understand supply concentration |
| Median price by brand | Compare brand price positioning |
| Listings by governorate | Show geographic market spread |
| Price bands | Segment market by affordability |
| Price vs mileage | Understand depreciation patterns |
| Listings by model year | Understand age distribution |
| Imputation level chart | Show data quality transparently |

## Project Status

Completed:

- Dockerized Airflow stack
- Selenium scraper
- Reference price scraper
- PostgreSQL warehouse
- Raw and cleaned tables
- Hash-based duplicate prevention
- Python transformation pipeline
- Power BI-ready cleaned table
- CSV export backup

Next:

- Build the Power BI dashboard
- Add DAX measures
- Add dashboard screenshots to this README
- Optionally add a small data dictionary section
