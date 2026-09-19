from copy import deepcopy

# Announcements are display-only overlays. Add future full or partial cancellations
# here without changing forecast values, snapshots, or recorded flight outcomes.
SERVICE_ANNOUNCEMENTS = {
    "2026-09-20": {
        "status": "cancelled",
        "label": "全便欠航（発表済み）",
        "flight_label": "欠航（発表済み）",
        "message": (
            "9/20（日）はANA1891・ANA1893・ANA1895の全便欠航が発表されています。"
            "参考スコアは予測値として残しています。"
        ),
        "flight_numbers": ("ANA1891", "ANA1893", "ANA1895"),
        "source_url": "https://www.ana.co.jp/fs/dom/jp/",
    },
    "2026-09-21": {
        "status": "cancelled",
        "label": "全便欠航（発表済み）",
        "flight_label": "欠航（発表済み）",
        "message": (
            "9/21（月）はANA1891・ANA1893・ANA1895の全便欠航が発表されています。"
            "参考スコアは予測値として残しています。"
        ),
        "flight_numbers": ("ANA1891", "ANA1893", "ANA1895"),
        "source_url": "https://www.ana.co.jp/fs/dom/jp/",
    },
}


def service_announcement(date_string, flight_number=None):
    announcement = SERVICE_ANNOUNCEMENTS.get(date_string)
    if announcement is None:
        return None
    if flight_number is not None and flight_number not in announcement["flight_numbers"]:
        return None
    return deepcopy(announcement)
