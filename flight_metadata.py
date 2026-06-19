FLIGHT_DISPLAY_NAMES = {
    "ANA1891": "ANA1891(1便)",
    "ANA1893": "ANA1893(2便)",
    "ANA1895": "ANA1895(3便)",
}


def flight_display_name(flight_number):
    return FLIGHT_DISPLAY_NAMES.get(flight_number, flight_number)
