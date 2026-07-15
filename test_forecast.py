import requests
from datetime import datetime, timedelta
from forecast_engine import predict_flight_probability

HACHIJOJIMA_LAT = 33.115
HACHIJOJIMA_LON = 139.782

FLIGHTS_SCHEDULE = [
    {"flight_number": "ANA1891", "scheduled_time": "08:30", "target_hour": 8},
    {"flight_number": "ANA1893", "scheduled_time": "13:10", "target_hour": 13},
    {"flight_number": "ANA1895", "scheduled_time": "16:40", "target_hour": 17},
]

def fetch_forecast_weather():
    """Open-Meteo APIから未来7日間の1時間ごとの気象予報を取得"""
    print("Open-Meteo APIから最新の気象予報を取得中...")
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": HACHIJOJIMA_LAT,
        "longitude": HACHIJOJIMA_LON,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility",
        "timezone": "Asia/Tokyo"
    }
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    return response.json().get("hourly", {})

def main():
    try:
        hourly_data = fetch_forecast_weather()
        if not hourly_data:
            print("予報データの取得に失敗しました。")
            return
            
        times = hourly_data.get("time", [])
        wind_speeds = hourly_data.get("wind_speed_10m", [])
        wind_dirs = hourly_data.get("wind_direction_10m", [])
        wind_gusts = hourly_data.get("wind_gusts_10m", [])
        cloud_covers = hourly_data.get("cloud_cover_low", [])
        visibilities = hourly_data.get("visibility", [])
        
        # 予報データをマッピング {"2026-06-04 08:00": {}}
        forecast_map = {}
        for i, t in enumerate(times):
            dt_parsed = datetime.strptime(t, "%Y-%m-%dT%H:%M")
            date_key = dt_parsed.strftime("%Y-%m-%d")
            hour_key = dt_parsed.hour
            
            # 風速と突風は m/s に変換 (Open-Meteo予報はデフォルトkm/h)
            ws_kmh = wind_speeds[i]
            wg_kmh = wind_gusts[i]
            ws_ms = round(ws_kmh / 3.6, 2) if ws_kmh is not None else None
            wg_ms = round(wg_kmh / 3.6, 2) if wg_kmh is not None else None
            
            # 視程は km に変換
            vis_m = visibilities[i]
            vis_km = round(vis_m / 1000.0, 2) if vis_m is not None else None
            
            if date_key not in forecast_map:
                forecast_map[date_key] = {}
                
            forecast_map[date_key][hour_key] = {
                "wind_direction": wind_dirs[i],
                "wind_speed": ws_ms,
                "wind_gusts": wg_ms,
                "cloud_cover_low": cloud_covers[i],
                "visibility": vis_km
            }
            
        # 明日から1週間分（7日間）の予測を実行
        today = datetime.today()
        target_dates = [
            (today + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(1, 8)
        ]
        
        print("\n=======================================================")
        print("     ANA 羽田発 → 八丈島着便 就航確率予測レポート")
        print("=======================================================\n")
        
        for date_str in target_dates:
            print(f"📅 対象日: {date_str}")
            print("-" * 55)
            
            if date_str not in forecast_map:
                print("  ※この日付の予報データがありません。")
                print("-" * 55)
                continue
                
            day_forecast = forecast_map[date_str]
            
            for f in FLIGHTS_SCHEDULE:
                target_hour = f["target_hour"]
                if target_hour in day_forecast:
                    w = day_forecast[target_hour]
                    
                    # 予測の実行
                    res = predict_flight_probability(
                        wind_direction=w["wind_direction"],
                        wind_speed=w["wind_speed"],
                        wind_gusts=w["wind_gusts"],
                        cloud_cover_low=w["cloud_cover_low"],
                        visibility=w["visibility"]
                    )
                    
                    # 確率の表示フォーマット
                    prob = res["probability"]
                    prob_bar = "■" * int(prob / 10) + "□" * (10 - int(prob / 10))
                    
                    # 警告等の絵文字
                    alert_emoji = "⚠️" if res["alert_required"] else "✅"
                    
                    print(f"✈️  便名: {f['flight_number']} (定刻 {f['scheduled_time']})")
                    print(f"  [気象条件] 風向: {w['wind_direction']}° | 風速: {w['wind_speed']} m/s | 突風: {w['wind_gusts']} m/s | 視程: {w['visibility']} km | 低層雲量: {w['cloud_cover_low']}%")
                    print(f"  [就航予測] 就航確率: {prob:>5}%  [{prob_bar}]  (類似過去データ: {res['data_count']}件, ステップ {res['step_used']})")
                    print(f"  [判定状況] 状況: {alert_emoji} {res['warning_msg']}")
                    
                    # 台風/低気圧リスク時警告メッセージ
                    if res["alert_required"]:
                        print("  🚨 注意: 強風または台風の影響を受ける可能性が極めて高いです。")
                        
                    print()
            print("-" * 55)
            
    except Exception as e:
        print(f"予測処理中にエラーが発生しました: {e}")

if __name__ == "__main__":
    main()

