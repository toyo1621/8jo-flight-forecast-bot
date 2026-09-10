"""Plan or apply the additive BigQuery schema needed for publication state."""

import argparse

from flight_forecast.bigquery_schema import (
    PREDICTION_PUBLICATION_TABLE,
    PREDICTION_SNAPSHOT_TABLE,
    ensure_prediction_snapshot_destination,
)
from flight_forecast.bigquery_storage import settings


def migration_plan(project, dataset):
    return [
        f"ALTER TABLE `{project}.{dataset}.{PREDICTION_SNAPSHOT_TABLE}` ADD COLUMN IF NOT EXISTS run_attempt INT64",
        f"ALTER TABLE `{project}.{dataset}.{PREDICTION_SNAPSHOT_TABLE}` ADD COLUMN IF NOT EXISTS model_input_json STRING",
        f"ALTER TABLE `{project}.{dataset}.{PREDICTION_SNAPSHOT_TABLE}` ADD COLUMN IF NOT EXISTS input_fingerprint STRING",
        f"ALTER TABLE `{project}.{dataset}.{PREDICTION_SNAPSHOT_TABLE}` ADD COLUMN IF NOT EXISTS content_fingerprint STRING",
        f"ALTER TABLE `{project}.{dataset}.{PREDICTION_SNAPSHOT_TABLE}` ADD COLUMN IF NOT EXISTS publication_tracking_enabled BOOL",
        f"CREATE TABLE IF NOT EXISTS `{project}.{dataset}.{PREDICTION_PUBLICATION_TABLE}` (...)",
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="BigQueryへDDLを適用する。未指定時は計画表示だけにする。",
    )
    args = parser.parse_args()
    config = settings()
    if not args.apply:
        print("適用しないdry-runです。")
        print("\n".join(migration_plan(config["project"], config["dataset"])))
        return

    from google.cloud import bigquery

    client = bigquery.Client(project=config["project"], location=config["location"])
    ensure_prediction_snapshot_destination(client, config["dataset"], config["location"])
    print("公開状態スキーマの適用が完了しました。")


if __name__ == "__main__":
    main()
