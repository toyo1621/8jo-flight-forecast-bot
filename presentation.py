from app_config import LOW_PROBABILITY_THRESHOLD, probability_symbol


MODEL_PROBABILITY_DISPLAY = (
    ("gfs_probability", "gfs_risk", "GFS", "static/flags/us.svg", "US"),
    ("ecmwf_probability", "ecmwf_risk", "ECMWF", "static/flags/eu.svg", "EU"),
    ("jma_probability", "jma_risk", "JMA", "static/flags/jp.svg", "JP"),
)


def probability_tone(value):
    return "ok" if value >= LOW_PROBABILITY_THRESHOLD else "low"


def decorate_flight_for_display(flight):
    decorated = dict(flight)
    decorated["probability_symbol"] = probability_symbol(flight["probability"])
    decorated["is_low_probability"] = flight["probability"] < LOW_PROBABILITY_THRESHOLD
    decorated["model_probabilities"] = []
    for field, risk_field, label, flag_path, flag_alt in MODEL_PROBABILITY_DISPLAY:
        value = flight.get(field)
        if value is None:
            continue
        risk = flight.get(risk_field) or "特になし"
        decorated["model_probabilities"].append(
            {
                "label": label,
                "probability": value,
                "symbol": probability_symbol(value),
                "tone": probability_tone(value),
                "risk": risk,
                "risk_tone": "ok" if risk == "特になし" else "alert",
                "flag_path": flag_path,
                "flag_alt": flag_alt,
            }
        )
    return decorated
