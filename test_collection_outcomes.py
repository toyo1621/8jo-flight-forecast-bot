from datetime import datetime
from unittest.mock import patch

import pytest

from app_config import JST
from collection_outcomes import can_replace, classify
from data_collector import (
    CollectionError,
    main,
    parse_flight_data_odpt,
    replay_collection_run,
)
from migrate_collection_outcomes import migration_sql

NOW = datetime(2026, 9, 8, 18, 1, tzinfo=JST)


def payload(status='Arrived', **overrides):
    return {
        'odpt:originAirport': 'odpt.Airport:HND',
        'odpt:arrivalAirport': 'odpt.Airport:HAC',
        'odpt:flightNumber': ['NH1895'],
        'odpt:flightStatus': 'odpt.FlightStatus:' + status,
        'odpt:scheduledArrivalTime': '16:40',
        'dc:date': '2026-09-08T18:00:00+09:00',
        'dct:valid': '2026-09-08T18:11:00+09:00',
        **overrides,
    }


@pytest.mark.parametrize('status', ['Normal', 'Delayed', 'Conditional', 'EstimatedArrival'])
def test_pending_never_becomes_operated_after_scheduled_time(status):
    assert classify(payload(status), NOW)['status'] is None
    assert parse_flight_data_odpt([payload(status)], fetched_at=NOW) == []


@pytest.mark.parametrize('status,expected', [('Arrived', '運航'), ('Cancelled', '欠航'),
                                         ('Returned', '条件付き→引返欠航'), ('Diverted', '条件付き→引返欠航')])
def test_final_status_and_provenance(status, expected):
    row = parse_flight_data_odpt([payload(status)], fetched_at=NOW, run_id='raw-1')[0]
    assert row['status'] == expected
    assert row['outcome_raw_run_id'] == 'raw-1'
    assert row['outcome_date_basis'] == 'same_day_validity_window'


def test_delayed_midnight_run_cannot_store_next_day_estimates():
    now = datetime(2026, 9, 8, 1, 52, tzinfo=JST)
    flight = payload('EstimatedArrival', **{'dc:date': '2026-09-08T01:49:38+09:00',
                                          'dct:valid': '2026-09-08T02:00:38+09:00'})
    assert parse_flight_data_odpt([flight], target_date='2026-09-07', fetched_at=now) == []
    assert parse_flight_data_odpt([flight], fetched_at=now) == []


@pytest.mark.parametrize('extra', [
    {'dc:date': 'bad'}, {'dct:valid': '2026-09-08T18:00:01+09:00'},
    {'dc:date': '2026-09-08T18:02:00+09:00'}, {'odpt:flightDate': '2026-09-09'},
    {'odpt:flightDate': 'nonsense'}, {'odpt:actualArrivalTime': '19:00'},
    {'odpt:actualArrivalTime': 'garbage'},
    {'odpt:flightStatus': {}}, {'odpt:flightDate': '20260908'},
])
def test_invalid_or_stale_inputs_are_not_results(extra):
    assert classify(payload(**extra), NOW)['status'] is None


def test_actual_arrival_can_confirm_pending_but_not_conflicting_cancellation():
    assert classify(payload('Normal', **{'odpt:actualArrivalTime': '16:50'}), NOW)['status'] == '運航'
    assert classify(payload('Cancelled', **{'odpt:actualArrivalTime': '16:50'}), NOW)['outcome_state'] == 'conflict'


def test_explicit_previous_date_is_not_relabelled():
    flight = payload(**{'odpt:flightDate': '2026-09-07'})
    assert classify(flight, NOW)['date'] == '2026-09-07'
    assert parse_flight_data_odpt([flight], target_date='2026-09-08', fetched_at=NOW) == []


def test_older_equal_and_pending_cannot_overwrite_confirmed():
    old = classify(payload('Cancelled'), NOW)
    assert not can_replace(old, old)
    assert not can_replace(old, classify(payload('Normal'), NOW))
    assert not can_replace(old, {**old, 'outcome_observed_at': '2026-09-08T17:00:00+09:00'})
    assert can_replace(old, {**old, 'outcome_observed_at': '2026-09-08T18:01:00+09:00'})
    assert not can_replace({**old, 'outcome_locked': True}, {**old, 'outcome_observed_at': '2026-09-08T18:01:00+09:00'})


def test_weather_outage_keeps_final_flight_and_run_metadata(monkeypatch):
    monkeypatch.setattr('sys.argv', ['data_collector.py', '--date', '2026-09-08'])
    monkeypatch.setenv('ODPT_API_KEY', 'fake')
    rows = parse_flight_data_odpt([payload('Cancelled')], fetched_at=NOW, run_id='raw-1')
    with (patch('data_collector.get_flight_data_odpt', return_value=rows),
          patch('data_collector.get_weather_data', side_effect=CollectionError('weather missing')),
          patch('data_collector.save_collected_data', return_value=1) as save,
          patch('data_collector.record_collection_run') as run):
        main()
    saved = save.call_args.args[0]
    assert len(saved) == 1 and saved[0]['status'] == '欠航'
    assert saved[0].get('wind_speed') is None
    assert run.call_args.args[2] == 'partial'
    assert run.call_args.kwargs['source_status']['weather_complete'] == 0


def test_no_final_flights_never_writes(monkeypatch):
    monkeypatch.setattr('sys.argv', ['data_collector.py', '--date', '2026-09-08'])
    monkeypatch.setenv('ODPT_API_KEY', 'fake')
    with (patch('data_collector.get_flight_data_odpt', return_value=[]),
          patch('data_collector.save_collected_data') as save,
          patch('data_collector.record_collection_run') as run):
        main()
    save.assert_not_called()
    assert run.call_args.args[2] == 'partial'


def test_replay_without_original_fetch_time_is_refused():
    with patch('data_collector.load_raw_collection_payloads', return_value=[
        {'source': 'odpt_flight_information_arrival', 'payload_json': '[]'}
    ]), pytest.raises(CollectionError, match='取得時刻'):
        replay_collection_run('old')


def test_migration_is_additive_and_protection_uses_existing_audit():
    sql = migration_sql('project.dataset.flights', True)
    assert sql.count('ADD COLUMN IF NOT EXISTS') == 7
    assert 'COUNT(*) = 6' in sql
    assert "outcome_source='owner_report'" in sql
    assert 'outcome_audit_20260908_owner_report' in sql
    assert 'DELETE' not in sql and 'DROP' not in sql
    with pytest.raises(ValueError):
        migration_sql('project.dataset.flights`; DELETE')


def test_migration_preview_does_not_connect_to_bigquery(monkeypatch):
    from migrate_collection_outcomes import main as migrate
    monkeypatch.setattr('sys.argv', ['migrate_collection_outcomes.py', '--protect-owner-report'])
    with patch('migrate_collection_outcomes.bigquery.Client') as client:
        migrate()
    client.assert_not_called()


def test_storage_rejects_pending_even_if_status_says_operated():
    from bigquery_storage import _normalize_item
    with pytest.raises(ValueError, match='confirmed'):
        _normalize_item({'status': '運航', 'outcome_state': 'pending'}, NOW.isoformat())
    with pytest.raises(ValueError, match='timestamp'):
        _normalize_item({'status': '運航', 'outcome_state': 'confirmed'}, NOW.isoformat())


def test_same_observation_can_fill_missing_weather_without_changing_result():
    old = classify(payload('Cancelled'), NOW)
    assert can_replace(old, {**old, 'wind_speed': 5.0})
    assert not can_replace(old, {**old, 'status': '運航', 'wind_speed': 5.0})


def test_duplicate_conflict_does_not_discard_other_flight():
    from data_collector import merge_with_daily_schedule
    rows = parse_flight_data_odpt([
        payload('Cancelled'), payload('Arrived'),
        payload('Arrived', **{'odpt:flightNumber': ['NH1891']}),
    ], fetched_at=NOW)
    assert [r['flight_number'] for r in merge_with_daily_schedule('2026-09-08', rows)] == ['ANA1891']


def test_fixture_fetch_to_archive_html_keeps_unknown_separate(monkeypatch):
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader

    from forecast_archive import build_archive_days

    monkeypatch.setattr('sys.argv', ['data_collector.py', '--date', '2026-09-08'])
    monkeypatch.setenv('ODPT_API_KEY', 'fake')
    fake_store = {}

    def save(rows):
        for row in rows:
            fake_store[(row['date'], row['flight_number'])] = row
        return len(rows)

    rows = parse_flight_data_odpt([
        payload('Arrived', **{'odpt:flightNumber': ['NH1891']}),
        payload('EstimatedArrival', **{'odpt:flightNumber': ['NH1893']}),
        payload('Cancelled'),
    ], fetched_at=NOW, run_id='synthetic')
    with (patch('data_collector.get_flight_data_odpt', return_value=rows),
          patch('data_collector.get_weather_data', side_effect=CollectionError('missing')),
          patch('data_collector.save_collected_data', side_effect=save),
          patch('data_collector.record_collection_run')):
        main()
    archive = build_archive_days([{
        'forecast_target_date': '2026-09-08', 'flight_number': n,
        'model': 'jma_seamless', 'probability': 80, 'publication_status': 'published',
        'calculation_status': 'available', 'prediction_generated_at': '2026-09-08T06:00:00+09:00',
        'outcome_status': fake_store.get(('2026-09-08', n), {}).get('status'),
    } for n in ['ANA1891', 'ANA1893', 'ANA1895']])[0]
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / 'templates'), autoescape=True)
    html = env.get_template('archive_date.html').render(day=archive, structured_data={}, access_stats={'days': []})
    assert '<p class="archive-outcome">運航</p>' in html
    assert '<p class="archive-outcome">未取得</p>' in html
    assert '<p class="archive-outcome">欠航</p>' in html
    assert len(fake_store) == 2
