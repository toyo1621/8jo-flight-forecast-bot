"""Ensure relocation preserves deployment resources and CLI entry points."""

import subprocess
import sys
from pathlib import Path

from flight_forecast.access_stats import DEFAULT_ACCESS_STATS_FILE
from flight_forecast.forecast_cache import BASE_DIR as CACHE_ROOT
from flight_forecast.web_app import BASE_DIR, create_app


def test_resources_and_cache_remain_at_repository_root():
    root = Path(__file__).resolve().parents[1]
    assert BASE_DIR == CACHE_ROOT == root
    assert DEFAULT_ACCESS_STATS_FILE == root / '.cache' / 'access_stats.json'
    app = create_app()
    assert app.jinja_env.get_template('index.html')
    with app.test_client() as client:
        assert client.get('/health').json == {'status': 'ok'}
        response = client.get('/static/styles.css')
        assert response.status_code == 200
        assert response.data == (root / 'static/styles.css').read_bytes()


def test_operational_module_entry_points():
    for module in ('data_collector', 'collection_monitor', 'data_quality',
                   'forecast_evaluation', 'publish_prediction_snapshots',
                   'migrate_collection_outcomes', 'migrate_prediction_publications'):
        result = subprocess.run(
            [sys.executable, '-m', f'flight_forecast.{module}', '--help'],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=20, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert 'usage:' in result.stdout
