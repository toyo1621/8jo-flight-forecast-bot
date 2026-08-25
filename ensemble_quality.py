from app_config import CONFIDENCE_GRADES

EXPECTED_MEMBER_COUNTS = {
    "gfs_seamless": 31,
    "ecmwf_ifs025": 31,
}
MIN_CONFIDENCE_MEMBERS = 10
MODEL_LABELS = {
    "gfs_seamless": "GFS",
    "ecmwf_ifs025": "ECMWF",
}


def _percentile(values, fraction):
    index = round((len(values) - 1) * fraction)
    return round(values[index], 1)


def _grade(spread):
    for threshold, grade, label in CONFIDENCE_GRADES:
        if spread <= threshold:
            return grade, label
    return "E", "40ポイント超"


def build_member_weather(member, baseline_weather, prediction_fields):
    weather = dict(baseline_weather or {})
    for key, value in member.items():
        if key not in {"_model", "_member_id"} and value is not None:
            weather[key] = value
    return {key: weather[key] for key in prediction_fields if key in weather}


def _variable_coverage(members, baseline_weather, prediction_fields):
    total = len(members)
    baseline_weather = baseline_weather or {}
    coverage = {}
    for field in prediction_fields:
        values = [member.get(field) for member in members if member.get(field) is not None]
        member_count = sum(member.get(field) is not None for member in members)
        baseline_filled_count = sum(
            member.get(field) is None and baseline_weather.get(field) is not None
            for member in members
        )
        coverage[field] = {
            "member_count": member_count,
            "coverage_ratio": round(member_count / total, 3) if total else 0.0,
            "baseline_filled_count": baseline_filled_count,
            "distinct_value_count": len(set(values)),
            "varied": len(set(values)) > 1,
            "source": (
                "member"
                if member_count == total and total
                else "partially_missing"
                if member_count
                else "baseline_fixed"
                if baseline_filled_count
                else "missing"
            ),
        }
    return coverage


def _model_summary_from_results(model, members, results, baseline_weather, prediction_fields):
    expected_count = EXPECTED_MEMBER_COUNTS.get(model, len(members))
    variable_coverage = _variable_coverage(members, baseline_weather, prediction_fields)
    probabilities = []
    for result in results:
        if result.get("calculation_status", "available") != "available":
            continue
        probability = result.get("probability")
        if probability is not None:
            probabilities.append(float(probability))
    probabilities.sort()
    valid_count = len(probabilities)
    summary = {
        "label": MODEL_LABELS.get(model, model),
        "status": "unavailable" if not members else "insufficient_members",
        "member_count": len(members),
        "valid_member_count": valid_count,
        "missing_member_count": max(0, len(members) - valid_count),
        "expected_member_count": expected_count,
        "member_coverage_ratio": round(valid_count / len(members), 3) if members else 0.0,
        "expected_coverage_ratio": round(valid_count / expected_count, 3)
        if expected_count
        else 0.0,
        "variable_coverage": variable_coverage,
        "member_variables": [
            field for field, item in variable_coverage.items() if item["member_count"]
        ],
        "varied_variables": [
            field for field, item in variable_coverage.items() if item["varied"]
        ],
        "constant_member_variables": [
            field
            for field, item in variable_coverage.items()
            if item["source"] == "member" and not item["varied"]
        ],
        "baseline_fixed_variables": [
            field
            for field, item in variable_coverage.items()
            if item["source"] == "baseline_fixed"
        ],
        "partially_missing_variables": [
            field
            for field, item in variable_coverage.items()
            if item["source"] == "partially_missing"
        ],
        "low_probability": None,
        "median_probability": None,
        "high_probability": None,
        "spread": None,
        "grade": None,
        "grade_label": "評価不可",
    }
    if valid_count >= MIN_CONFIDENCE_MEMBERS:
        low = _percentile(probabilities, 0.1)
        median_probability = _percentile(probabilities, 0.5)
        high = _percentile(probabilities, 0.9)
        spread = round(high - low, 1)
        grade, grade_label = _grade(spread)
        summary.update(
            {
                "status": "available",
                "low_probability": low,
                "median_probability": median_probability,
                "high_probability": high,
                "spread": spread,
                "grade": grade,
                "grade_label": grade_label,
            }
        )
    return summary


def _combine_model_summaries(models, unrecognized_member_count=0):
    available = [item for item in models.values() if item["status"] == "available"]
    partial = [item for item in models.values() if item["status"] != "available"]
    if not available:
        return {
            "grade": None,
            "label": "評価不可",
            "source": "unavailable",
            "confidence_kind": "scenario_spread",
            "summary_basis": "no_model_with_minimum_members",
            "models": models,
            "available_models": [],
            "missing_models": [item["label"] for item in partial],
            "unrecognized_member_count": unrecognized_member_count,
            "member_count": sum(item["valid_member_count"] for item in models.values()),
            "valid_member_count": sum(item["valid_member_count"] for item in models.values()),
            "expected_member_count": sum(EXPECTED_MEMBER_COUNTS.values()),
            "coverage_ratio": 0.0,
            "caution": "必要なアンサンブルmember数が不足しています。",
        }

    worst = max(available, key=lambda item: "ABCDE".index(item["grade"]))
    valid_count = sum(item["valid_member_count"] for item in models.values())
    expected_count = sum(EXPECTED_MEMBER_COUNTS.values())
    cautions = []
    if unrecognized_member_count:
        cautions.append(f"識別できないmemberが{unrecognized_member_count}件あります。")
    if partial:
        cautions.append("一部モデルの取得またはmember数が不足しています。")
    for item in available:
        if item["baseline_fixed_variables"] or item["partially_missing_variables"]:
            cautions.append(f"{item['label']}に欠測変数があります。")
    return {
        "grade": worst["grade"],
        "label": worst["grade_label"],
        "spread": max(item["spread"] for item in available),
        "low_probability": min(item["low_probability"] for item in available),
        "median_probability": None,
        "high_probability": max(item["high_probability"] for item in available),
        "member_count": valid_count,
        "valid_member_count": valid_count,
        "expected_member_count": expected_count,
        "coverage_ratio": round(valid_count / expected_count, 3),
        "source": "ensemble" if len(available) == len(models) else "ensemble_partial",
        "confidence_kind": "scenario_spread",
        "summary_basis": "worst_available_model_without_pooling",
        "model_weighting": "equal_model_worst_case",
        "minimum_members_per_model": MIN_CONFIDENCE_MEMBERS,
        "models": models,
        "available_models": [item["label"] for item in available],
        "missing_models": [item["label"] for item in partial],
        "unrecognized_member_count": unrecognized_member_count,
        "caution": " ".join(cautions) if cautions else None,
    }


def summarize_ensemble_results(evaluations, baseline_weather=None, prediction_fields=()):
    """Summarize already-computed member results without invoking the predictor."""
    grouped_members = {model: [] for model in EXPECTED_MEMBER_COUNTS}
    grouped_results = {model: [] for model in EXPECTED_MEMBER_COUNTS}
    unrecognized_member_count = 0
    for evaluation in evaluations or []:
        model = evaluation.model
        if model in grouped_members:
            grouped_members[model].append(evaluation.member)
            grouped_results[model].append(evaluation.result)
        else:
            unrecognized_member_count += 1

    models = {
        model: _model_summary_from_results(
            model,
            grouped_members[model],
            grouped_results[model],
            baseline_weather,
            prediction_fields,
        )
        for model in grouped_members
    }
    return _combine_model_summaries(models, unrecognized_member_count)


def evaluate_ensemble_confidence(
    ensemble_members,
    baseline_weather=None,
    flight_number=None,
    predictor=None,
    prediction_fields=(),
):
    """Compatibility wrapper for callers that only need confidence metadata."""
    from ensemble_evaluation import evaluate_ensemble_members

    return evaluate_ensemble_members(
        ensemble_members,
        baseline_weather=baseline_weather,
        flight_number=flight_number,
        predictor=predictor,
        prediction_fields=prediction_fields,
    ).confidence
