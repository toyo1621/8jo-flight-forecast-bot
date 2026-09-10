from flight_forecast.migrate_prediction_publications import migration_plan


def test_prediction_publication_migration_is_additive_and_explicit():
    plan = migration_plan("project", "dataset")

    assert any("ADD COLUMN IF NOT EXISTS" in statement for statement in plan)
    assert any("prediction_publications" in statement for statement in plan)
    assert any("publication_tracking_enabled" in statement for statement in plan)
    assert not any("DELETE" in statement or "DROP" in statement for statement in plan)
