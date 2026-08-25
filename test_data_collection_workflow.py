from pathlib import Path

WORKFLOW = Path(__file__).parent / ".github" / "workflows" / "data_collection.yml"


def test_collection_workflow_can_notify_coverage_gaps():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "issues: write" in workflow
    assert "Notify collection coverage gap" in workflow
    assert "always() && steps.collection_coverage.outcome == 'failure'" in workflow
    assert "steps.collection_coverage.outcome == 'failure'" in workflow
    assert "gh issue list --state open" in workflow
