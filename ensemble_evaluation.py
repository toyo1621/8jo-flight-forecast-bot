from collections import Counter
from dataclasses import dataclass
from statistics import median

from ensemble_quality import build_member_weather, summarize_ensemble_results

RISK_LABELS = (
    "南風注意",
    "視程不良リスク",
    "降水注意",
    "低層雲の影響注意",
    "突風注意",
    "強風注意",
    "台風接近リスク",
)
ENSEMBLE_BUILD_PREDICTION_BUDGET = 11 * 3 * 62


@dataclass(frozen=True)
class EnsembleMemberEvaluation:
    model: str
    member_id: str
    member: dict
    weather: dict
    result: dict


@dataclass(frozen=True)
class EnsembleEvaluation:
    members: tuple
    confidence: dict
    model_probabilities: dict
    model_risks: dict


def risk_labels(warning_msg):
    if not warning_msg or warning_msg in {"なし", "特になし"}:
        return []
    labels = []
    for warning in str(warning_msg).split("、"):
        for label in RISK_LABELS:
            if warning.startswith(label):
                labels.append(label)
                break
    return labels


def format_risk_summary(counts, total):
    if not counts:
        return "特になし"
    return "、".join(
        f"{label} ({counts[label]}/{total}通り)"
        for label in RISK_LABELS
        if counts.get(label)
    )


def _model_probabilities(evaluations):
    probabilities = {}
    for evaluation in evaluations:
        if evaluation.model is None:
            continue
        result = evaluation.result
        if result.get("calculation_status", "available") != "available":
            continue
        probability = result.get("probability")
        if probability is not None:
            probabilities.setdefault(evaluation.model, []).append(float(probability))
    return {
        model: round(median(values), 1)
        for model, values in probabilities.items()
        if values
    }


def _model_risks(evaluations):
    risk_counts = {}
    totals = Counter()
    for evaluation in evaluations:
        if not evaluation.model:
            continue
        totals[evaluation.model] += 1
        risk_counts.setdefault(evaluation.model, Counter()).update(
            risk_labels(evaluation.result.get("warning_msg"))
        )
    return {
        model: format_risk_summary(risk_counts.get(model, Counter()), total)
        for model, total in totals.items()
        if total
    }


def evaluate_ensemble_members(
    ensemble_members,
    baseline_weather=None,
    flight_number=None,
    predictor=None,
    prediction_fields=(),
):
    """Evaluate each ensemble member once and derive all display summaries from it."""
    if predictor is None:
        raise ValueError("ensemble評価には予測関数が必要です。")

    evaluations = []
    for index, raw_member in enumerate(ensemble_members or [], start=1):
        member = dict(raw_member)
        model = member.get("_model")
        member_id = member.get("_member_id") or f"{model or 'unknown'}:{index:02d}"
        weather = build_member_weather(member, baseline_weather, prediction_fields)
        result = dict(predictor(**weather, flight_number=flight_number))
        evaluations.append(
            EnsembleMemberEvaluation(
                model=model,
                member_id=str(member_id),
                member=member,
                weather=weather,
                result=result,
            )
        )

    confidence = summarize_ensemble_results(
        evaluations,
        baseline_weather=baseline_weather,
        prediction_fields=prediction_fields,
    )
    return EnsembleEvaluation(
        members=tuple(evaluations),
        confidence=confidence,
        model_probabilities=_model_probabilities(evaluations),
        model_risks=_model_risks(evaluations),
    )
