import requests


def select_evenly(values, limit):
    if limit is None or len(values) <= limit:
        return values
    if limit <= 1:
        return values[:limit]
    indices = [round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)]
    return [values[index] for index in indices]


def meters_to_km(value):
    return round(value / 1000, 1) if value is not None else None


def _optional_hourly_value(hourly, key, index):
    values = hourly.get(key)
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


def parse_deterministic_response(payload):
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    if not isinstance(hourly, dict):
        raise ValueError("気象データの構造が正しくありません。")  # noqa: TRY004
    times = hourly.get("time", [])
    required = {
        "wind_speed_10m",
        "wind_direction_10m",
        "cloud_cover_low",
        "precipitation",
    }
    if not isinstance(times, list) or not times or any(
        not isinstance(hourly.get(key), list) or len(hourly[key]) != len(times)
        for key in required
    ):
        raise ValueError("気象データの構造が正しくありません。")

    weather_by_time = {}
    for index, timestamp in enumerate(times):
        weather_by_time[timestamp] = {
            "wind_speed": hourly["wind_speed_10m"][index],
            "wind_direction": hourly["wind_direction_10m"][index],
            "wind_gusts": _optional_hourly_value(hourly, "wind_gusts_10m", index),
            "cloud_cover_low": hourly["cloud_cover_low"][index],
            "visibility": meters_to_km(_optional_hourly_value(hourly, "visibility", index)),
            "precipitation": hourly["precipitation"][index],
            "pressure_msl": _optional_hourly_value(hourly, "pressure_msl", index),
            "surface_pressure": _optional_hourly_value(hourly, "surface_pressure", index),
        }
    return weather_by_time


def fetch_deterministic_forecast(
    model,
    latitude,
    longitude,
    endpoint,
    forecast_days,
    request_get=None,
    timeout=10,
):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility,precipitation,pressure_msl,surface_pressure",
        "wind_speed_unit": "ms",
        "timezone": "Asia/Tokyo",
        "forecast_days": forecast_days,
    }
    if model:
        params["models"] = model
    request_get = request_get or requests.get
    response = request_get(endpoint, params=params, timeout=timeout)
    response.raise_for_status()
    return parse_deterministic_response(response.json())


def _member_id(model, suffix):
    member_number = suffix.removeprefix("_member") or "base"
    return f"{model}:{member_number}"


def parse_ensemble_response(payload, model, variables, max_members=None):
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    if not isinstance(hourly, dict):
        raise ValueError("アンサンブル予報の構造が正しくありません。")  # noqa: TRY004
    times = hourly.get("time", [])
    if not variables:
        raise ValueError("アンサンブル予報の変数がありません。")
    member_key = variables[0]
    suffixes = [
        key.removeprefix(member_key)
        for key in hourly
        if key == member_key or key.startswith(f"{member_key}_member")
    ]
    suffixes = select_evenly(suffixes, max_members)
    if not isinstance(times, list) or not times or not suffixes:
        raise ValueError("アンサンブル予報の構造が正しくありません。")

    ensembles_by_time = {}
    for index, timestamp in enumerate(times):
        members = []
        for suffix in suffixes:
            keys = [f"{variable}{suffix}" for variable in variables]
            if any(
                not isinstance(hourly.get(key), list) or index >= len(hourly[key])
                for key in keys
            ):
                continue
            values = [hourly[key][index] for key in keys]
            if any(value is None for value in values):
                continue
            weather = {
                variable.removesuffix("_10m"): value
                for variable, value in zip(variables, values)
            }
            weather["_model"] = model
            weather["_member_id"] = _member_id(model, suffix)
            if "visibility" in weather:
                weather["visibility"] = meters_to_km(weather["visibility"])
            members.append(weather)
        ensembles_by_time[timestamp] = members
    return ensembles_by_time


def fetch_ensemble_model(
    model,
    variables,
    max_members,
    latitude,
    longitude,
    endpoint,
    forecast_days,
    request_get=None,
    timeout=20,
):
    response = (request_get or requests.get)(
        endpoint,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(variables),
            "models": model,
            "wind_speed_unit": "ms",
            "timezone": "Asia/Tokyo",
            "forecast_days": forecast_days,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_ensemble_response(response.json(), model, variables, max_members)
