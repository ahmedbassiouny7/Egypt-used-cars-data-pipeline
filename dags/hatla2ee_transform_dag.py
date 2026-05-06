from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator


default_args = {
    "owner": "data-team",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


with DAG(
    dag_id="hatla2ee_transform_used_cars_cleaned",
    description="Transform latest raw Hatla2ee rows from Postgres into the cleaned table.",
    default_args=default_args,
    start_date=datetime(2026, 5, 1),
    schedule_interval=None,
    catchup=False,
    tags=["hatla2ee", "used-cars", "transform", "cleaned"],
) as dag:
    transform_used_cars = BashOperator(
        task_id="transform_used_cars",
        # Triggered runs receive a batch_id from the scrape DAG. Manual runs omit
        # it and transform all raw rows, which is useful after cleaning changes.
        bash_command=(
            "python /opt/airflow/src/transform_used_cars.py "
            "{% if dag_run and dag_run.conf and dag_run.conf.get('batch_id') %}"
            "--batch-id {{ dag_run.conf.get('batch_id') }}"
            "{% endif %}"
        ),
    )
