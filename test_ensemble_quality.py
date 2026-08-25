from ensemble_quality import evaluate_ensemble_confidence


def _predictor(**weather):
    return {"probability": weather.get("wind_speed", 50.0)}


def _members(model, count, include_cloud=False):
    return [
        {
            "_model": model,
            "wind_speed": float(index + 10),
            **({"cloud_cover_low": float(index)} if include_cloud else {}),
        }
        for index in range(count)
    ]


def _evaluate(members, baseline=None):
    return evaluate_ensemble_confidence(
        members,
        baseline_weather=baseline,
        predictor=_predictor,
        prediction_fields=("wind_speed", "cloud_cover_low"),
    )


def test_zero_members_are_unavailable_without_a_grade():
    result = _evaluate([])

    assert result["source"] == "unavailable"
    assert result["grade"] is None
    assert result["models"]["gfs_seamless"]["member_count"] == 0


def test_one_model_is_marked_partial_and_keeps_model_identity():
    result = _evaluate(_members("gfs_seamless", 10))

    assert result["source"] == "ensemble_partial"
    assert result["available_models"] == ["GFS"]
    assert result["missing_models"] == ["ECMWF"]
    assert result["models"]["gfs_seamless"]["valid_member_count"] == 10


def test_member_shortage_does_not_produce_a_to_e_grade():
    result = _evaluate(_members("gfs_seamless", 9))

    assert result["source"] == "unavailable"
    assert result["grade"] is None
    assert result["models"]["gfs_seamless"]["status"] == "insufficient_members"


def test_variable_missingness_records_jma_baseline_fixed_fields():
    result = _evaluate(
        _members("gfs_seamless", 10),
        baseline={"cloud_cover_low": 20.0},
    )

    gfs = result["models"]["gfs_seamless"]
    assert "wind_speed" in gfs["varied_variables"]
    assert gfs["baseline_fixed_variables"] == ["cloud_cover_low"]
    assert gfs["variable_coverage"]["cloud_cover_low"] == {
        "member_count": 0,
        "coverage_ratio": 0.0,
        "baseline_filled_count": 10,
        "distinct_value_count": 0,
        "varied": False,
        "source": "baseline_fixed",
    }
    assert "GFSに欠測変数があります。" in result["caution"]


def test_both_models_are_reported_separately_without_pooling():
    result = _evaluate(
        _members("gfs_seamless", 10) + _members("ecmwf_ifs025", 10, include_cloud=True)
    )

    assert result["source"] == "ensemble"
    assert result["summary_basis"] == "worst_available_model_without_pooling"
    assert result["models"]["gfs_seamless"]["grade"] is not None
    assert result["models"]["ecmwf_ifs025"]["grade"] is not None
