from datetime import date

from data_quality import analyze_records, format_markdown, should_fail, summarize


def _row(**overrides):
    base = {
        "date": "2026-06-20",
        "flight_number": "ANA1891",
        "scheduled_time": "08:30:00",
        "status": "運航",
        "status_reason": None,
        "wind_direction": 180.0,
        "wind_speed": 5.0,
        "wind_gusts": 8.0,
        "cloud_cover_low": 20.0,
        "visibility": 15.0,
        "visibility_source": "open_meteo_historical_forecast",
    }
    return {**base, **overrides}


def test_data_quality_passes_clean_records():
    records = [
        _row(flight_number="ANA1891"),
        _row(flight_number="ANA1893"),
        _row(flight_number="ANA1895"),
    ]

    findings = analyze_records(records, today=date(2026, 6, 21))

    assert findings == []
    assert summarize(findings, len(records))["passed"] is True


def test_data_quality_finds_errors_and_warnings():
    records = [
        _row(status="欠航", status_reason=None, visibility=None, visibility_source=None),
        _row(status="欠航", status_reason=None, visibility=None, visibility_source=None),
        _row(flight_number="ANA9999", status="謎"),
        _row(date="2026/06/20"),
    ]

    findings = analyze_records(records, today=date(2026, 6, 21))
    codes = {finding.code: finding for finding in findings}

    assert codes["duplicate_date_flight"].severity == "error"
    assert codes["unknown_flight_number"].severity == "error"
    assert codes["unknown_status"].severity == "error"
    assert codes["invalid_date"].severity == "error"
    assert codes["missing_cancellation_reason"].severity == "warning"
    assert codes["missing_visibility"].severity == "warning"
    assert should_fail(findings, "error") is True


def test_data_quality_markdown_report_includes_summary_and_examples():
    findings = analyze_records(
        [_row(status="条件付き→引返欠航", status_reason=None)],
        today=date(2026, 6, 21),
    )

    report = format_markdown(findings, 1)

    assert "# Data Quality Report" in report
    assert "Records checked: 1" in report
    assert "`missing_cancellation_reason`" in report
    assert "2026-06-20 ANA1891" in report


def test_data_quality_fail_on_warning_is_configurable():
    findings = analyze_records(
        [_row(status="欠航", status_reason=None)],
        today=date(2026, 6, 21),
    )

    assert should_fail(findings, "error") is False
    assert should_fail(findings, "warning") is True
    assert should_fail(findings, "none") is False


def test_unconfirmed_cancellation_reason_is_tracked_as_info():
    findings = analyze_records(
        [_row(status="欠航", status_reason="未確認")],
        today=date(2026, 6, 21),
    )
    codes = {finding.code: finding for finding in findings}

    assert "missing_cancellation_reason" not in codes
    assert codes["unconfirmed_cancellation_reason"].severity == "info"
    assert should_fail(findings, "warning") is False
