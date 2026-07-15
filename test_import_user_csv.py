import pytest

from import_user_csv import build_import_items, parse_date_range, parse_status


def test_parse_date_range_normalizes_single_digit_month_and_day():
    assert parse_date_range("2025-6-12") == ["2025-06-12"]


def test_parse_status_supports_operation_csv_labels():
    assert parse_status("運航") == ("運航", None)
    assert parse_status("通常") == ("運航", None)
    assert parse_status("条件付→運航") == ("運航(条件付)", None)
    assert parse_status("運航条件付→運航") == ("運航(条件付)", None)
    assert parse_status("条件付→引返欠航") == ("条件付き→引返欠航", None)
    assert parse_status("欠航(強風)") == ("欠航", "強風")
    assert parse_status("？") == (None, None)


def test_import_stops_when_weather_is_missing():
    records = [{"dates": ["2026-07-15"], "statuses": ["運航", "", ""]}]

    with pytest.raises(RuntimeError, match="ANA1891"):
        build_import_items(records, {})

