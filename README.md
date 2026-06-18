# 羽田→八丈島便 就航確率予測システム ✈️🏝️
(Haneda to Hachijojima Flight Forecast System)

ANAの羽田発・八丈島着便における「過去の運航実績」と「その当時の気象予報・実況データ」を自動的に蓄積・分析し、未来の気象予報から「過去の類似条件での就航確率」を算出・可視化するためのシステムです。

> [!IMPORTANT]
> **気象業務法（予報業務許可）への配慮について**
> 本システムは「独自の天気予報」を行うものではありません。気象庁等の公式予報および実況数値に基づき、「過去の同一・類似条件下での運航実績（統計データ）」を紐解き、**客観的な就航確率として可視化するシステム**という立て付けにすることで、日本の気象業務法をクリアします。

---

## 🛠️ システムのデータソース（完全無料枠）

1. **運航データ**  
   [公共交通オープンデータセンター (ODPT API)](https://www.odpt.org/) から、ANAのリアルタイム運航ステータス（通常、条件付き、欠航、引き返し等）を取得します。
2. **気象データ**  
   [Open-Meteo API](https://open-meteo.com/) を使用し、八丈島空港（緯度: 33.115, 経度: 139.782）の指定日時の風向・風速・突風・低層雲量・視程を取得します（APIキー不要）。
3. **データベース**  
   軽量・高速でサーバーレスな **SQLite** を採用し、同一ワークスペース内に永続化データ（貯金箱）を構築します。

---

## 🚀 主な機能

* **高精度なデータ統合**: フライト予定時刻に最も近い時間帯（1時間単位）の気象情報をマッピング。
* **風速の自動単位変換**: Open-Meteoのデフォルト値である `km/h` を、日本の航空気象や予報でなじみ深い `m/s` に自動変換。
* **視程 (visibility) データの自動蓄積**: 八丈島特有の「霧による欠航」を分析するため、1時間ごとの視程データ (km単位) を自動取得して保存。
* **強固な重複排除 (UPSERT)**: データベース側で `(date, flight_number)` の複合ユニーク制約を設け、同一フライトのデータが重複登録されないように設計。何度実行しても安全に更新されます。
* **デモモード搭載**: ODPT APIキーを持っていない、あるいは設定前の状態でも、模擬フライト予定と本物の気象データを連動させて動作を確認できます。

---

## 📂 データベース設計 (SQLite)

データベースファイル: `flights.db`  
テーブル名: `flight_weather_logs`

| カラム名 | 型 | 制約 | 説明 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | レコードID |
| `date` | TEXT | NOT NULL | 運航日付 (YYYY-MM-DD) |
| `flight_number` | TEXT | NOT NULL | 便名 (例: `ANA1891`) |
| `scheduled_time` | TEXT | | 定刻到着時刻 (HH:MM) |
| `status` | TEXT | | 運航結果ステータス (例: 通常/欠航/条件付き運航/引き返しなど) |
| `wind_direction` | REAL | | 地上風向 (度: 0-360) |
| `wind_speed` | REAL | | 地上風速 (m/s) |
| `wind_gusts` | REAL | | 突風 (m/s) |
| `cloud_cover_low` | REAL | | 低層雲量 (%) |
| `visibility` | REAL | | 視程 (km) |
| `created_at` | TEXT | DEFAULT CURRENT_TIMESTAMP | レコード生成日時 |

※ `(date, flight_number)` に `UNIQUE` 制約が適用されています。

---

## ⚙️ 環境構築・セットアップ

Python 3.8以上がインストールされている環境を想定しています。

### 1. リポジトリのクローンと移動
```bash
git clone https://github.com/toyo1621/8jo-flight-forecast-bot.git
cd 8jo-flight-forecast-bot
```

### 2. 仮想環境の構築とライブラリのインストール
```bash
# 仮想環境 venv の作成
python3 -m venv .venv

# 仮想環境の有効化 (Mac / Linux)
source .venv/bin/activate

# 依存パッケージのインストール
pip install -r requirements.txt
```

### 3. 環境変数の設定
`.env.example` をコピーして `.env` を作成し、ODPT APIキーを設定します。

```bash
cp .env.example .env
```

`.env` ファイルを開き、取得したAPIキーを入力します：
```ini
ODPT_API_KEY=あなたのODPT_API_KEY
```

---

## 💻 使い方 (Usage)

### デモモードでテスト実行する (APIキー設定なしでOK)
実際の Open-Meteo API を利用しつつ、デモデータを用いてデータの取得・結合・保存の流れをテストします。
```bash
python data_collector.py --demo
```

### 通常モードで実行する (実データ収集)
`.env` に正しい `ODPT_API_KEY` を設定した上で実行します。
```bash
python data_collector.py
```

通常モードでは、ODPT APIから取得できた便だけでなく、固定ダイヤの `ANA1891` / `ANA1893` / `ANA1895` の3便分を必ず保存対象にします。ODPTで当日の運航結果がまだ取れない便は `未取得` として保存され、次回以降の実行で同じ `(date, flight_number)` の行が更新されます。

### 過去データをCSVから取り込む
`user_raw_data.csv` の過去運航実績に Open-Meteo Archive API の過去気象データを付与して、同じ `flights.db` にUPSERTします。既存テーブルは削除せず、同じ `(date, flight_number)` の行だけ更新します。
```bash
python import_user_csv.py
```

別のCSVを取り込む場合は `--csv` で指定できます。
```bash
python import_user_csv.py --csv path/to/past_flights.csv
```

### 蓄積されたデータの確認
SQLiteデータベースを直接クエリしてデータを確認できます。
```bash
sqlite3 flights.db "SELECT * FROM flight_weather_logs ORDER BY date DESC LIMIT 5"
```

---

## 🔮 今後の開発ロードマップ

1. **データ自動収集の定期化**:
   - `cron` 等を用いて1日数回スクリプトを自動実行し、運航結果と気象データを自動的に蓄積し続けます。
2. **就航予測統計エンジンの作成**:
   - [予測・表示仕様書 (docs/forecast_spec.md)](docs/forecast_spec.md) に基づき、風向・風速だけでなく、「霧（視界不良）」「台風時の警報アラート」「航空会社都合（機材繰り等）を考慮した上限95%制限」などを加味した就航確率算出アルゴリズムを構築します。
3. **可視化ダッシュボードの構築**:
   - 未来の気象予報（数日先まで）を取得し、過去の類似条件の運航実績・就航確率をパーセント表示するWeb UIの作成。

---

## 📄 ライセンス

本プロジェクトは [MIT License](LICENSE) のもとで公開されています。
