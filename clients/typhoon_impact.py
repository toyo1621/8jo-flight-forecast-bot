import requests

from typhoon_impact import normalize_typhoon_impact


def parse_typhoon_impact_response(payload, source, valid_levels):
    if not isinstance(payload, dict) or payload.get("source") != source:
        raise ValueError("台風影響度APIのデータソースが正しくありません。")

    days = payload.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("台風影響度APIの日別データがありません。")

    source_details = payload.get("sourceDetails") or {}
    score_config = payload.get("scoreConfig") or {}
    if not isinstance(source_details, dict) or not isinstance(score_config, dict):
        raise ValueError("台風影響度APIのメタデータが正しくありません。")  # noqa: TRY004
    factor_weights = (score_config.get("targetWeights") or {}).get("flight", {})
    factor_max_values = score_config.get("factorMaxValues", {})
    impacts = {}
    for day in days:
        if not isinstance(day, dict):
            continue
        targets = day.get("targets")
        target = targets.get("flight", {}) if isinstance(targets, dict) else {}
        if not isinstance(target, dict):
            target = {}
        level = target.get("riskLevel")
        date_string = day.get("date")
        if isinstance(date_string, str) and level in valid_levels:
            impacts[date_string] = normalize_typhoon_impact(
                {
                    "risk_level": level,
                    "score": target.get("score"),
                    "factors": target.get("factors", {}),
                    "inputs": target.get("inputs", {}),
                    "reasons": target.get("reasons", []),
                    "source_mode": source_details.get("mode"),
                    "source_provider": source_details.get("weatherProvider"),
                    "typhoon_provider": source_details.get("typhoonProvider"),
                    "score_config_version": score_config.get("version"),
                    "factor_weights": factor_weights,
                    "factor_max_values": factor_max_values,
                }
            )
    if not impacts:
        raise ValueError("台風影響度APIに利用可能な飛行機向け影響度がありません。")
    return impacts


def fetch_typhoon_impacts(endpoint, source, valid_levels, request_get=None, timeout=15):
    response = (request_get or requests.get)(
        endpoint,
        params={"source": source},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_typhoon_impact_response(response.json(), source, valid_levels)
