from ensemble_evaluation import (
    ENSEMBLE_BUILD_PREDICTION_BUDGET,
    evaluate_ensemble_members,
)


def test_member_evaluations_are_reused_for_confidence_probability_and_risk():
    members = [
        {
            "_model": "gfs_seamless",
            "_member_id": "gfs_seamless:01",
            "wind_speed": 4.0,
        },
        {
            "_model": "gfs_seamless",
            "_member_id": "gfs_seamless:02",
            "wind_speed": 12.0,
        },
        {
            "_model": "ecmwf_ifs025",
            "_member_id": "ecmwf_ifs025:01",
            "wind_speed": 8.0,
        },
    ]
    calls = []

    def predictor(**weather):
        calls.append(weather)
        warning = "強風注意" if weather["wind_speed"] >= 10 else "特になし"
        return {"probability": weather["wind_speed"], "warning_msg": warning}

    evaluation = evaluate_ensemble_members(
        members,
        predictor=predictor,
        prediction_fields=("wind_speed",),
    )

    assert len(calls) == len(members)
    assert [item.member_id for item in evaluation.members] == [
        "gfs_seamless:01",
        "gfs_seamless:02",
        "ecmwf_ifs025:01",
    ]
    assert evaluation.model_probabilities == {
        "gfs_seamless": 8.0,
        "ecmwf_ifs025": 8.0,
    }
    assert evaluation.model_risks == {
        "gfs_seamless": "強風注意 (1/2通り)",
        "ecmwf_ifs025": "特になし",
    }


def test_evaluation_budget_covers_eleven_days_three_flights_and_sixty_two_members():
    members = [
        {
            "_model": "gfs_seamless" if index % 2 else "ecmwf_ifs025",
            "_member_id": f"member:{index:02d}",
            "wind_speed": float(index),
        }
        for index in range(62)
    ]
    calls = 0

    def predictor(**weather):
        nonlocal calls
        calls += 1
        return {"probability": weather["wind_speed"], "warning_msg": "特になし"}

    for _ in range(11 * 3):
        evaluate_ensemble_members(
            members,
            predictor=predictor,
            prediction_fields=("wind_speed",),
        )

    assert calls == ENSEMBLE_BUILD_PREDICTION_BUDGET
