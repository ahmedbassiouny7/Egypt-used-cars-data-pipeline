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

### What This Project Shows

- End-to-end orchestration with separate scrape and transform DAGs
- Selenium scraping against dynamic website pages
- Raw-to-cleaned warehouse design in PostgreSQL
- Idempotent loading with hash-based duplicate prevention
- Price quality handling using reference prices, IQR outlier checks, and fallback averages
- BI-ready output for Power BI or CSV export

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
| `reference_hash` | Primary key for one reference-price brand/model/year |
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
| `reference_missing_price` | Missing or invalid scraped price filled from Hatla2ee reference average |
| `reference_low_outlier` | Price was far below the reference range and replaced with reference average |
| `reference_high_outlier` | Price was far above the reference range and replaced with reference average |
| `iqr_model_year_median` | Group outlier replaced with median for same brand/model/year |
| `iqr_model_median` | Group outlier replaced with median for same brand/model |
| `iqr_brand_median` | Group outlier replaced with median for same brand |
| `absolute_high_brand_median` | Extremely high price replaced with brand median |
| `absolute_high_global_median` | Extremely high price replaced with global median |
| `model_avg` | Filled using average price for the same model |
| `brand_avg` | Filled using average price for the same brand |
| `global_avg` | Filled using average valid price across all cars |
| `null` | Original price was valid and not imputed |

## Code Map

| File | Responsibility |
|---|---|
| `src/scrape_hatla2ee.py` | Scrapes used-car listing pages and inserts raw rows page-by-page |
| `src/scrape_used_price_reference.py` | Scrapes Hatla2ee used-price reference tables |
| `src/transform_used_cars.py` | Cleans raw rows and loads `cleaned_used_cars` |
| `src/db_utils.py` | Database connection and stable hash helpers |
| `dags/hatla2ee_scrape_dag.py` | Runs reference scrape, listing scrape, then triggers transform DAG |
| `dags/hatla2ee_transform_dag.py` | Runs the cleaning script, optionally for one batch |
| `sql/001_create_cars_tables.sql` | Creates warehouse tables and indexes |

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

Note: a fixed page cap is recommended for full runs because some websites repeat the last page instead of returning a clean empty page.

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

Show price quality flags:

```bash
docker compose exec cars-postgres psql -U cars_user -d cars_dw -c "SELECT price_imputation_level, COUNT(*) FROM cleaned_used_cars GROUP BY price_imputation_level ORDER BY COUNT(*) DESC;"
```

Inspect the most expensive cleaned records:

```bash
docker compose exec cars-postgres psql -U cars_user -d cars_dw -c "SELECT car_name, year, price_original, price, price_imputation_level, car_url FROM cleaned_used_cars ORDER BY price DESC LIMIT 20;"
```

Stop the stack:

```bash
docker compose down
```

Reset all Docker database volumes:

```bash
docker compose down -v
```

## CI/CD Plan With Jenkins And Docker Hub

The CI/CD goal is to package the Airflow project as a Docker image and push it to Docker Hub. This image should contain the project code needed by Airflow:

```text
dags/
src/
sql/
Python dependencies
```

The image is for the Airflow project only. PostgreSQL, Selenium, and other services still run from their normal public Docker images.

### Target Image

Example Docker Hub image name:

```text
<dockerhub-username>/egypt-used-cars-airflow
```

Recommended tags:

```text
<dockerhub-username>/egypt-used-cars-airflow:<jenkins-build-number>
<dockerhub-username>/egypt-used-cars-airflow:latest
```

### Jenkins Pipeline Flow

The Jenkins pipeline should run these stages:

| Stage | Purpose |
|---|---|
| Checkout | Pull the latest project code from GitHub |
| Validate | Check Docker Compose and basic project structure |
| Build Image | Build the custom Airflow Docker image |
| Test Image | Run Python syntax checks for `src/` and `dags/` inside the image |
| Docker Login | Login to Docker Hub using Jenkins credentials |
| Push Image | Push both build-number and `latest` tags to Docker Hub |

### Jenkins Credentials

Create a Jenkins credential for Docker Hub:

| Field | Value |
|---|---|
| Kind | Username with password |
| ID | `dockerhub-creds` |
| Username | Docker Hub username |
| Password | Docker Hub access token |

Use a Docker Hub access token instead of the account password.

### Jenkins Parameters

Recommended Jenkins job parameters:

| Parameter | Example |
|---|---|
| `DOCKERHUB_NAMESPACE` | `your-dockerhub-username` |
| `IMAGE_NAME` | `egypt-used-cars-airflow` |

### Example Jenkinsfile Logic

```groovy
pipeline {
    agent any

    environment {
        DOCKERHUB_CREDENTIALS_ID = 'dockerhub-creds'
        IMAGE_NAME = "${params.DOCKERHUB_NAMESPACE}/egypt-used-cars-airflow"
        IMAGE_TAG = "${IMAGE_NAME}:${env.BUILD_NUMBER}"
        LATEST_TAG = "${IMAGE_NAME}:latest"
    }

    parameters {
        string(name: 'DOCKERHUB_NAMESPACE', defaultValue: 'your-dockerhub-username')
    }

    stages {
        stage('Validate') {
            steps {
                sh 'docker compose config --quiet'
            }
        }

        stage('Build Image') {
            steps {
                sh 'docker build -f Dockerfile.airflow -t $IMAGE_TAG -t $LATEST_TAG .'
            }
        }

        stage('Test Image') {
            steps {
                sh 'docker run --rm $IMAGE_TAG python -m py_compile /opt/airflow/src/*.py /opt/airflow/dags/*.py'
            }
        }

        stage('Push To Docker Hub') {
            steps {
                withCredentials([usernamePassword(credentialsId: DOCKERHUB_CREDENTIALS_ID, usernameVariable: 'DOCKERHUB_USER', passwordVariable: 'DOCKERHUB_TOKEN')]) {
                    sh '''
                        echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USER" --password-stdin
                        docker push "$IMAGE_TAG"
                        docker push "$LATEST_TAG"
                        docker logout
                    '''
                }
            }
        }
    }
}
```

### Local Build Before Jenkins

Before running Jenkins, test the image locally:

```bash
docker build -f Dockerfile.airflow -t egypt-used-cars-airflow:local .
```

Then run a syntax check inside the image:

```bash
docker run --rm egypt-used-cars-airflow:local python -m py_compile /opt/airflow/src/*.py /opt/airflow/dags/*.py
```

### Using The Pushed Image

After Jenkins pushes the image, Docker Compose can use the Docker Hub image instead of building locally:

```env
AIRFLOW_IMAGE_NAME=<dockerhub-username>/egypt-used-cars-airflow:latest
```

Then restart the Airflow services:

```bash
docker compose up -d --force-recreate airflow-webserver airflow-scheduler
```

## GitHub Notes

Do not commit local runtime state:

- `.env`
- `logs/`
- `data/raw/debug/`
- latest debug scrape CSV files
- Docker volumes

Commit useful project assets:

- Source code in `src/`
- DAGs in `dags/`
- SQL schema in `sql/`
- `.env.example`
- README and dashboard screenshots
- Small sample CSVs only if they are safe to share

## Troubleshooting

| Problem | What To Check |
|---|---|
| Airflow UI does not open | `docker compose ps` and `docker compose logs airflow-webserver` |
| Scrape keeps going past expected pages | Set `HATLA2EE_MAX_PAGES` to a fixed cap such as `570` |
| Raw table has rows but cleaned table is empty | Check `hatla2ee_transform_used_cars_cleaned` task logs |
| Power BI cannot connect | Use server `localhost`, port `5433`, database `cars_dw` |
| Website selectors break | Check debug HTML/screenshots in `data/raw/debug/` |

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
