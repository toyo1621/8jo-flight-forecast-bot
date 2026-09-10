"""Print additive migration SQL by default. Production apply requires approval."""
import argparse
import re

from google.cloud import bigquery

from flight_forecast.bigquery_schema import OUTCOME_COLUMNS
from flight_forecast.bigquery_storage import settings, table_path


def migration_sql(destination, protect_owner_report=False):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+\.[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+", destination):
        raise ValueError("Invalid table identifier")
    statements = [f"ALTER TABLE `{destination}` ADD COLUMN IF NOT EXISTS {name} {kind};"
                  for name, kind in OUTCOME_COLUMNS]
    if protect_owner_report:
        audit = destination.rsplit('.', 1)[0] + '.outcome_audit_20260908_owner_report'
        statements.append(f"""
        ASSERT (SELECT COUNT(*) = 6 FROM `{audit}`) AS 'Expected six owner-confirmed outcomes';
        ASSERT (SELECT COUNT(*) = 6 FROM `{destination}` T JOIN `{audit}` A
          ON CAST(T.date AS STRING)=JSON_VALUE(A.after_json, '$.date')
          AND T.flight_number=JSON_VALUE(A.after_json, '$.flight_number')
          WHERE T.status=JSON_VALUE(A.after_json, '$.status')) AS 'Outcomes changed since owner correction';
        UPDATE `{destination}` T SET outcome_locked=TRUE, outcome_state='confirmed',
          outcome_source='owner_report', outcome_observed_at=A.corrected_at,
          outcome_date_basis='owner_report', outcome_raw_run_id='outcome_audit_20260908_owner_report'
        FROM `{audit}` A
        WHERE CAST(T.date AS STRING)=JSON_VALUE(A.after_json, '$.date')
          AND T.flight_number=JSON_VALUE(A.after_json, '$.flight_number');
        """)
    return '\n'.join(statements)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--protect-owner-report', action='store_true')
    args = parser.parse_args()
    config = settings()
    sql = migration_sql(table_path(config), args.protect_owner_report)
    print(sql)
    if args.apply:
        bigquery.Client(project=config['project'], location=config['location']).query(sql).result()


if __name__ == '__main__':
    main()
