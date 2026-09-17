import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def workflow(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_ci_and_evaluation_keep_required_quality_gates():
    ci = workflow("ci.yml")
    codeql = workflow("codeql.yml")
    evaluation = workflow("forecast_evaluation.yml")

    assert "python -m pytest -q" in ci
    assert "python -m ruff check ." in ci
    assert "python -m pip_audit -r requirements.lock" in ci
    assert "github/codeql-action/analyze@" in codeql
    assert "python -m flight_forecast.forecast_evaluation" in evaluation
    assert "--fail-on-insufficient-data" in evaluation


def test_third_party_actions_are_pinned_to_commits():
    for path in WORKFLOWS.glob("*.yml"):
        source = path.read_text(encoding="utf-8")
        assert re.search(r"uses:\s+[^\s@]+@[0-9a-f]{40}", source), path.name
        assert re.search(r"uses:\s+[^\s@]+@v\d+", source) is None, path.name


def test_pages_validates_and_preserves_artifact_identity_through_deployment():
    pages = workflow("pages.yml")

    assert pages.index("python -m pytest -q") < pages.index("Build static forecast")
    assert "Verify the same SHA before building" in pages
    assert "python -m flight_forecast.data_quality --format markdown" in pages
    assert "python -m flight_forecast.validate_static_site dist" in pages
    assert "dist/build-manifest.json" in pages
    assert "Upload static build for diagnostics" in pages
    assert "if-no-files-found: ignore" in pages
    assert "needs.build.outputs.artifact_id" in pages
    assert "Confirm the live artifact and publish snapshots" in pages
    assert "python -m flight_forecast.publish_prediction_snapshots" in pages


def test_optional_analytics_and_collection_monitoring_cannot_block_pages():
    pages = workflow("pages.yml")

    assert "id: access_stats\n        continue-on-error: true" in pages
    assert "steps.access_stats.outcome != 'success'" in pages
    assert "python -m flight_forecast.access_stats --mark-stale" in pages
    assert "Notify access analytics outage" in pages
    assert "id: collection_health\n        continue-on-error: true" in pages
    assert "python -m flight_forecast.collection_monitor" in pages
    assert "pages-collection-health" in pages


def test_collection_is_tested_before_writes_and_republishes_only_successful_main_runs():
    collection = workflow("data_collection.yml")

    assert "23 0,5,9,12 * * *" in collection
    assert collection.index("python -m pytest -q") < collection.index(
        "Run data collector"
    )
    assert '--date "$TARGET_DATE"' in collection
    assert '--replay-run-id "$REPLAY_RUN_ID"' in collection
    assert "python -m flight_forecast.data_quality --format markdown" in collection
    assert "python -m flight_forecast.collection_monitor --days 14" in collection
    assert "Notify collection coverage gap" in collection
    assert "actions/upload-artifact@" in collection
    assert "needs.collect-data.outputs.collected == 'success'" in collection
    assert "github.ref == 'refs/heads/main'" in collection
    assert "gh workflow run pages.yml --ref main" in collection
    assert "actions: write" not in collection.split("  republish:")[0]
