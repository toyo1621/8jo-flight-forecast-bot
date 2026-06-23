# 運用Runbook

この文書は、八丈島運航統計予測を安定運用するための確認・復旧手順です。

## 定常監視

毎日または更新後に確認するもの:

- GitHub Actions `Deploy forecast site to Pages`
  - 6時間ごとの静的サイト生成が成功していること
  - Step summary の `Data Quality Report` に error がないこと
- GitHub Actions `Daily Flight & Weather Data Collection`
  - 毎日21:00 JST前後の運航実績収集が成功していること
  - Data Quality Report に重複、未知ステータス、未知便名がないこと
- 公開サイト
  - [https://toyo1621.github.io/8jo-flight-forecast-bot/](https://toyo1621.github.io/8jo-flight-forecast-bot/)
  - 更新時刻が古すぎないこと
  - 便カードと詳細画面が開けること

## 障害時の優先順位

1. 公開サイトが表示されているか確認
2. Pages workflow の直近失敗を確認
3. Data Quality Report の error を確認
4. 外部API障害か、BigQuery/認証/コード変更の問題かを切り分け
5. 必要に応じて手動で workflow dispatch を実行

## よくある障害と対応

### Open-Meteo が一時的に失敗

症状:

- Pages build で予報取得エラー
- サイト上に「前回取得した予報データを表示しています」と出る

対応:

- まずは公開サイトが前回データで表示されていることを確認
- 次の6時間更新を待つ
- 急ぐ場合は `Deploy forecast site to Pages` を手動実行

### BigQuery認証エラー

症状:

- `google-github-actions/auth` または BigQuery query が失敗

確認:

- Repository Variables
  - `GCP_WORKLOAD_IDENTITY_PROVIDER`
  - `GCP_SERVICE_ACCOUNT`
- サービスアカウントに BigQuery の読み書き権限があるか
- `BIGQUERY_LOCATION=asia-northeast1` が設定されているか

### ODPT APIエラー

症状:

- `Daily Flight & Weather Data Collection` が失敗

確認:

- GitHub Secret `ODPT_API_KEY`
- ODPT側の一時障害
- 手元で `python data_collector.py --demo` が通るか

### データ品質エラー

Data Quality Report の severity が `error` の場合、公開ページは可用性を優先して維持しつつ、最優先で調査します。PR/CIでは品質チェックを確認し、運用者が修正判断を行います。

代表例:

- `duplicate_date_flight`: 同じ日付・同じ便が重複
- `unknown_flight_number`: 対象外の便名
- `unknown_status`: 正規化できない運航ステータス
- `invalid_date`: 日付形式不正

対応:

1. BigQueryコンソールで該当レコードを確認
2. 正しい値を調査
3. 必要ならCSVを修正して `import_user_csv.py --backend both` で再投入
4. `python data_quality.py --backend bigquery --format markdown --output data_quality_report.md` で再確認

## ローカル確認コマンド

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe data_quality.py --backend sqlite --format markdown --output data_quality_report.md --fail-on none
.\.venv\Scripts\python.exe build_static.py
```

BigQueryを使う場合:

```powershell
$env:FORECAST_DATA_BACKEND = "bigquery"
$env:GCP_PROJECT_ID = "hachijo-flight-forecast"
$env:BIGQUERY_DATASET = "flight_forecast"
$env:BIGQUERY_TABLE = "flight_weather_logs"
$env:BIGQUERY_LOCATION = "asia-northeast1"
.\.venv\Scripts\python.exe data_quality.py --backend bigquery --format markdown --output data_quality_report.md
.\.venv\Scripts\python.exe build_static.py
```

## 手動公開確認

```powershell
$ts=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$url="https://toyo1621.github.io/8jo-flight-forecast-bot/?verify=$ts"
$html=(Invoke-WebRequest -Uri $url -UseBasicParsing).Content
$html.Contains("八丈島運航統計予測")
```

## データ修正の原則

- 欠航理由が不明な場合は推測しない
- `通常` はDB投入時に `運航` へ正規化
- `条件付き→就航`、`条件付→運航` は `運航(条件付)` へ統一
- `date + flight_number` を一意キーとして扱う
- 視程補完値は実測ではなく数値予報値であることを忘れない

## 将来Cloud Runへ移行する場合

Cloud Run移行時に追加で必要になるもの:

- Cloud Run用のヘルスチェックと最小インスタンス方針
- Secret Manager連携
- Cloud Logging/Monitoringのアラート
- リクエスト単位のキャッシュ戦略
- 管理画面の認証
