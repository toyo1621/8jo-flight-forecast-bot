FLIGHT_DISPLAY_NAMES = {
    "ANA1891": "ANA1891(1便)",
    "ANA1893": "ANA1893(2便)",
    "ANA1895": "ANA1895(3便)",
}

STATUS_LABELS = {
    "通常": "運航",
    "遅延": "運航",
    "条件付き運航": "運航(条件付)",
    "条件付→運航": "運航(条件付)",
    "条件付き→運航": "運航(条件付)",
    "条件付き→就航": "運航(条件付)",
    "条件付き": "運航(条件付)",
    "運航(条件付)": "運航(条件付)",
    "引き返し(他空港着)": "条件付き→引返欠航",
    "引き返し(出発空港着)": "条件付き→引返欠航",
    "引き返し": "条件付き→引返欠航",
}

DATABASE_STATUS_LABELS = {
    "通常": "運航",
    "遅延": "運航",
    "運航": "運航",
    "条件付き運航": "運航(条件付)",
    "条件付→運航": "運航(条件付)",
    "条件付き→運航": "運航(条件付)",
    "条件付き→就航": "運航(条件付)",
    "条件付き": "運航(条件付)",
    "運航(条件付)": "運航(条件付)",
}

OPERATED_STATUSES = frozenset({"運航", "運航(条件付)"})
NON_OPERATED_STATUSES = frozenset({"欠航", "条件付き→引返欠航"})
VALID_STORED_STATUSES = OPERATED_STATUSES | NON_OPERATED_STATUSES
VALID_HISTORY_STATUSES = frozenset(STATUS_LABELS) | frozenset(STATUS_LABELS.values()) | VALID_STORED_STATUSES


def flight_display_name(flight_number):
    return FLIGHT_DISPLAY_NAMES.get(flight_number, flight_number)


def normalize_status(status):
    return STATUS_LABELS.get(status, status)


def normalize_database_status(status):
    return DATABASE_STATUS_LABELS.get(status, status)

