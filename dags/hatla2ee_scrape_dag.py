from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator


default_args = {
    "owner": "data-team",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


with DAG(
    dag_id="hatla2ee_scrape_used_cars_raw",
    description="Scrape Hatla2ee used cars and append raw rows into Postgres.",
    default_args=default_args,
    start_date=datetime(2026, 5, 1),
    schedule_interval="@daily",
    catchup=False,
    tags=["hatla2ee", "used-cars", "scrape", "raw"],
) as dag:
    # The reference table is refreshed first because the transform step uses it
    # to repair missing or suspicious listing prices.
    scrape_price_reference = BashOperator(
        task_id="scrape_price_reference",
        bash_command=(
            "python /opt/airflow/src/scrape_used_price_reference.py "
            "--batch-id {{ ts_nodash }}"
        ),
    )

    scrape_used_cars = BashOperator(
        task_id="scrape_used_cars",
        bash_command=(
            "python /opt/airflow/src/scrape_hatla2ee.py "
            "--batch-id {{ ts_nodash }}"
        ),
    )

    # Keep scraping and transformation as separate DAGs. This makes it possible
    # to rerun only the transform if cleaning rules change.
    trigger_transform_used_cars = TriggerDagRunOperator(
        task_id="trigger_transform_used_cars",
        trigger_dag_id="hatla2ee_transform_used_cars_cleaned",
        conf={"batch_id": "{{ ts_nodash }}"},
        wait_for_completion=False,
    )

    scrape_price_reference >> scrape_used_cars >> trigger_transform_used_cars
