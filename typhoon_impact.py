VALID_TYPHOON_RISK_LEVELS = frozenset({"low", "medium", "high", "severe"})


def _clean_mapping(value):
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(key, (str, int, float))
        and (item is None or isinstance(item, (str, int, float, bool)))
    }


def normalize_typhoon_impact(value):
    """Normalize live and legacy cached impact values without guessing missing factors."""
    if isinstance(value, str):
        return {"risk_level": value} if value in VALID_TYPHOON_RISK_LEVELS else {}
    if not isinstance(value, dict):
        return {}

    risk_level = value.get("risk_level", value.get("riskLevel"))
    if risk_level not in VALID_TYPHOON_RISK_LEVELS:
        return {}

    normalized = {"risk_level": risk_level}
    for key in (
        "score",
        "source_mode",
        "source_provider",
        "typhoon_provider",
        "score_config_version",
    ):
        item = value.get(key)
        if item is not None and isinstance(item, (str, int, float, bool)):
            normalized[key] = item

    factors = _clean_mapping(value.get("factors"))
    inputs = _clean_mapping(value.get("inputs"))
    if factors:
        normalized["factors"] = factors
    if inputs:
        normalized["inputs"] = inputs
    reasons = value.get("reasons")
    if isinstance(reasons, list):
        normalized["reasons"] = [str(reason) for reason in reasons[:5]]
    for key in ("factor_weights", "factor_max_values"):
        cleaned = _clean_mapping(value.get(key))
        if cleaned:
            normalized[key] = cleaned
    normalized["factor_breakdown_available"] = bool(factors and inputs)
    return normalized


def typhoon_risk_level(value):
    return normalize_typhoon_impact(value).get("risk_level")


def has_factor_breakdown(value):
    return bool(normalize_typhoon_impact(value).get("factor_breakdown_available"))
