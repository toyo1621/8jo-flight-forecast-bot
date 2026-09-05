from pathlib import Path

WORKFLOW = Path(__file__).parent / ".github" / "workflows" / "pages.yml"


def test_access_stats_failure_cannot_block_forecast_pages_build():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "name: Restore access stats cache" in workflow
    assert "id: access_stats" in workflow
    assert "continue-on-error: true" in workflow
    assert "run: python access_stats.py --mark-stale" in workflow
    assert "steps.access_stats.outcome != 'success'" in workflow
    assert "Add access stats status to summary" in workflow
    assert "issues: write" in workflow
    assert "Notify access analytics outage" in workflow
    assert "python validate_static_site.py dist" in workflow
    assert "build-manifest.json" in workflow
    assert "name: Upload static build for diagnostics" in workflow
    assert "if-no-files-found: ignore" in workflow
    assert "needs.build.outputs.artifact_id" in workflow
    assert "publish_prediction_snapshots.py" in workflow
    assert "Confirm the live artifact and publish snapshots" in workflow
    assert "Verify the same SHA before building" in workflow
    assert "python -m pytest -q" in workflow
    assert "python -m ruff check ." in workflow
