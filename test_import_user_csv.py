from import_user_csv import parse_date_range, parse_status


def test_parse_date_range_normalizes_single_digit_month_and_day():
    assert parse_date_range("2025-6-12") == ["2025-06-12"]


def test_parse_status_supports_operation_csv_labels():
    assert parse_status("運航") == ("運航", None)
    assert parse_status("通常") == ("運航", None)
    assert parse_status("条件付→運航") == ("条件付き運航", None)
    assert parse_status("運航条件付→運航") == ("条件付き運航", None)
    assert parse_status("条件付→引返欠航") == ("条件付き→引返欠航", None)
    assert parse_status("欠航(強風)") == ("欠航", "強風")
    assert parse_status("？") == (None, None)

