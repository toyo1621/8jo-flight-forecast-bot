from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "pages.yml"


def test_access_stats_failure_cannot_block_forecast_pages_build():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "name: Restore access stats cache" in workflow
    assert "id: access_stats" in workflow
    assert "continue-on-error: true" in workflow
    assert "run: python -m flight_forecast.access_stats --mark-stale" in workflow
    assert "steps.access_stats.outcome != 'success'" in workflow
    assert "Add access stats status to summary" in workflow
    assert "issues: write" in workflow
    assert "Notify access analytics outage" in workflow
    assert "python -m flight_forecast.validate_static_site dist" in workflow
    assert "build-manifest.json" in workflow
    assert "name: Upload static build for diagnostics" in workflow
    assert "if-no-files-found: ignore" in workflow
    assert "needs.build.outputs.artifact_id" in workflow
    assert "python -m flight_forecast.publish_prediction_snapshots" in workflow
    assert "Confirm the live artifact and publish snapshots" in workflow
    assert "Verify the same SHA before building" in workflow
    assert "python -m pytest -q" in workflow
    assert "python -m ruff check ." in workflow


def test_pages_schedule_is_hourly_by_day_and_three_hourly_overnight_in_jst():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'cron: "17 0-12,21-23 * * *"' in workflow
    assert 'cron: "17 15,18 * * *"' in workflow


def test_scheduled_pages_refresh_keeps_full_quality_checks_out_of_the_hot_path():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    full_check_condition = (
        "github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && inputs.full_quality_checks)"
    )

    assert workflow.count(full_check_condition) == 3
    assert "Install publication dependencies for a lightweight refresh" in workflow
    assert "pip install -r requirements.txt -c requirements.lock" in workflow
    assert "Require a successful CI run for a lightweight refresh" in workflow
    assert "actions/workflows/ci.yml/runs?head_sha=$GITHUB_SHA" in workflow
    assert "python -m flight_forecast.validate_static_site dist" in workflow
    assert "python -m flight_forecast.data_quality" in workflow


def test_pages_artifacts_are_retained_for_thirty_days():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert workflow.count("retention-days: 30") == 5
    assert "name: Upload static build for diagnostics\n        if: failure()" in workflow
