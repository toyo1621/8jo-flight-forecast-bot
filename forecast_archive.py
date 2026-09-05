from collections import defaultdict
from datetime import date, datetime

from app_config import FLIGHTS, JST, probability_symbol
from flight_metadata import NON_OPERATED_STATUSES, OPERATED_STATUSES, normalize_status

MODEL_LABELS = {
    "jma_seamless": "JMA",
    "gfs_seamless": "GFS",
    "ecmwf_ifs025": "ECMWF",
}
WEEKDAYS = "月火水木金土日"


def _iso_date(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _format_timestamp(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=JST)
    return value.astimezone(JST).strftime("%Y/%m/%d %H:%M")


def _score(value):
    if value is None:
        return None
    return round(float(value))


def _reflection(score, outcome):
    if score is None:
        return "公開時のJMA参考スコアを算出できなかったため、結果との比較は行いません。"
    if outcome not in OPERATED_STATUSES | NON_OPERATED_STATUSES:
        return "実際の運航結果が未取得のため、振り返りはまだ確定していません。"
    if outcome in OPERATED_STATUSES:
        if score >= 75:
            return "参考スコアは高めで、実際も運航しました。"
        if score < 60:
            return "参考スコアは慎重な値でしたが、実際は運航しました。スコアは運航可否を断定する値ではありません。"
        return "参考スコアは中間的な値で、実際は運航しました。"
    if score >= 75:
        return "参考スコアは高めでしたが、実際は欠航または引き返しでした。予測の限界が表れた事例です。"
    if score < 60:
        return "参考スコアは慎重な値で、実際も欠航または引き返しでした。"
    return "参考スコアは中間的な値で、実際は欠航または引き返しでした。"


def build_archive_days(rows):
    grouped = defaultdict(lambda: defaultdict(dict))
    outcomes = {}
    for row in rows:
        date_string = _iso_date(row["forecast_target_date"])
        flight_number = row["flight_number"]
        grouped[date_string][flight_number][row["model"]] = row
        if row.get("outcome_status") is not None:
            outcomes[(date_string, flight_number)] = row

    archive_days = []
    for date_string in sorted(grouped, reverse=True):
        parsed_date = date.fromisoformat(date_string)
        flights = []
        for spec in FLIGHTS:
            number = spec["number"]
            model_rows = grouped[date_string].get(number, {})
            primary = model_rows.get("jma_seamless")
            outcome_row = outcomes.get((date_string, number), {})
            outcome = normalize_status(outcome_row.get("outcome_status"))
            primary_score = _score(primary.get("probability")) if primary else None
            models = []
            for model_name, label in MODEL_LABELS.items():
                model_row = model_rows.get(model_name)
                value = _score(model_row.get("probability")) if model_row else None
                models.append(
                    {
                        "label": label,
                        "score": value,
                        "symbol": probability_symbol(value),
                        "status": model_row.get("calculation_status") if model_row else "missing",
                    }
                )
            flights.append(
                {
                    "number": number,
                    "time": spec["time"],
                    "score": primary_score,
                    "symbol": probability_symbol(primary_score),
                    "published_at": _format_timestamp(
                        primary.get("prediction_generated_at") if primary else None
                    ),
                    "models": models,
                    "outcome": outcome,
                    "outcome_reason": outcome_row.get("status_reason"),
                    "outcome_confirmed": outcome in OPERATED_STATUSES | NON_OPERATED_STATUSES,
                    "reflection": _reflection(primary_score, outcome),
                }
            )
        confirmed = sum(flight["outcome_confirmed"] for flight in flights)
        operated = sum(flight["outcome"] in OPERATED_STATUSES for flight in flights)
        archive_days.append(
            {
                "date": date_string,
                "date_label": f"{parsed_date.month}/{parsed_date.day}",
                "long_date_label": f"{parsed_date.year}年{parsed_date.month}月{parsed_date.day}日",
                "weekday": WEEKDAYS[parsed_date.weekday()],
                "flights": flights,
                "confirmed_count": confirmed,
                "operated_count": operated,
                "last_modified": max(
                    (
                        row.get("prediction_generated_at")
                        for models in grouped[date_string].values()
                        for row in models.values()
                        if row.get("prediction_generated_at")
                    ),
                    default=None,
                ),
            }
        )
    return archive_days
