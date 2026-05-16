"""Create BigQuery dataset and tables from schema definitions."""
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import bigquery

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROJECT_ID = os.getenv("GCP_PROJECT_ID", "gdg-ai-2026-496507")
DATASET_ID = os.getenv("BQ_DATASET", "county_budget")
LOCATION = os.getenv("BQ_LOCATION", "US")
SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"

TABLES = [
    "budget_line_items",
    "gazette_amendments",
    "sms_digests",
    "subscribers",
]


def load_schema(table_name: str) -> list[bigquery.SchemaField]:
    schema_file = SCHEMAS_DIR / f"{table_name}.json"
    with open(schema_file) as f:
        raw = json.load(f)
    return [
        bigquery.SchemaField(
            name=field["name"],
            field_type=field["type"],
            mode=field.get("mode", "NULLABLE"),
        )
        for field in raw
    ]


def setup_bigquery() -> None:
    client = bigquery.Client(project=PROJECT_ID)

    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
    dataset_ref.location = LOCATION
    try:
        client.create_dataset(dataset_ref, exists_ok=True)
        log.info("Dataset ready: %s.%s", PROJECT_ID, DATASET_ID)
    except Exception as exc:
        log.error("Failed to create dataset: %s", exc)
        raise

    for table_name in TABLES:
        schema = load_schema(table_name)
        table_ref = client.dataset(DATASET_ID).table(table_name)
        table = bigquery.Table(table_ref, schema=schema)
        field_names = [s.name for s in schema]
        partition_field = next(
            (f for f in ("extracted_at", "subscribed_at", "generated_at") if f in field_names),
            None,
        )
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=partition_field,
        )
        try:
            client.create_table(table, exists_ok=True)
            log.info("Table ready: %s.%s.%s", PROJECT_ID, DATASET_ID, table_name)
        except Exception as exc:
            log.error("Failed to create table %s: %s", table_name, exc)


if __name__ == "__main__":
    setup_bigquery()
