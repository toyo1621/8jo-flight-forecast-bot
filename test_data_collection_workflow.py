from pathlib import Path

WORKFLOW = Path(__file__).parent / ".github" / "workflows" / "data_collection.yml"


def test_collection_workflow_can_notify_coverage_gaps():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "issues: write" in workflow
    assert "Notify collection coverage gap" in workflow
    assert "always() && steps.collection_coverage.outcome == 'failure'" in workflow
    assert "steps.collection_coverage.outcome == 'failure'" in workflow
    assert "gh issue list --state open" in workflow


def test_collection_is_distributed_and_tested_before_writes():
    workflow = WORKFLOW.read_text(encoding='utf-8')
    assert "23 0,5,9,12 * * *" in workflow
    assert workflow.index('python -m pytest -q') < workflow.index('- name: Run data collector')
    assert '--date "$TARGET_DATE"' in workflow
    assert '--replay-run-id "$REPLAY_RUN_ID"' in workflow
    assert 'actions: write' not in workflow


def test_pages_independently_observes_collection_without_stopping_forecast():
    pages = (WORKFLOW.parent / 'pages.yml').read_text(encoding='utf-8')
    assert 'id: collection_health\n        continue-on-error: true' in pages
    assert 'python collection_monitor.py' in pages
    assert 'pages-collection-health' in pages
