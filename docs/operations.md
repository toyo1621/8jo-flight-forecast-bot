# 運用Runbook

## 定常監視

- `Deploy forecast site to Pages`: 6時間ごとの生成・公開とData Quality Reportが成功していること
- `Daily Flight & Weather Data Collection`: 毎日21:00 JSTの3便収集が成功していること
- `CodeQL`と`CI`: mainとPull Requestの検査が成功していること
- [公開サイト](https://toyo1621.github.io/8jo-flight-forecast-bot/): 予報データ取得時刻、11日分の表示、詳細ダイアログを確認すること

Data Quality Reportの`error`はPagesと日次収集を失敗させます。エラーを無視して公開を更新しません。

## 障害時の優先順位

1. 公開済みサイトが表示できるか確認します。
2. Pagesまたは日次収集の直近ログとData Quality Reportを確認します。
3. Open-Meteo、台風影響度API、ODPT、BigQuery認証、コード変更のどこで失敗したか切り分けます。
4. データを推測で補わず、原因解消後にworkflowを手動実行します。

## Open-Meteo障害

主予報の取得に失敗した場合、7時間以内のキャッシュがあればその取得時刻と注意文を表示します。期限切れキャッシュしかない場合は新しいPagesを公開しません。JMAやアンサンブルだけが失敗した場合は、主予報を維持し、該当モデルのキャッシュ利用または欠測を表示します。

## 台風影響度の欠測

- API全体が失敗した場合: 7時間以内のキャッシュを使用し、なければ補正なしと通知します。
- 一部の日付がない場合: その日を`low`と見なさず、補正を適用していない日付範囲を通知します。
- 現在の表示範囲には、当日を含む11日分が必要です。

## ODPT・日次収集障害

取得失敗、対象3便不足、未対応ステータス、気象欠測ではBigQueryを更新しません。失敗を欠航へ変換しないでください。

確認項目:

- GitHub Secret `ODPT_API_KEY`
- ODPTとOpen-Meteoの応答
- 実行時刻が最終便の結果確定後か
- Workload Identity FederationとBigQuery書き込み権限

既存の未取得・未対応ステータス行だけを掃除する場合は、`Daily Flight & Weather Data Collection`を`cleanup_only=true`で手動実行します。これは外部APIを呼びません。

## BigQuery認証障害

Repository Variablesの`GCP_WORKLOAD_IDENTITY_PROVIDER`と`GCP_SERVICE_ACCOUNT`、サービスアカウントの最小権限、`BIGQUERY_LOCATION=asia-northeast1`を確認します。JSONサービスアカウント鍵を追加して回避しないでください。

## データ品質エラー

代表例:

- `duplicate_date_flight`: `date + flight_number`の重複
- `missing_status`: 空ステータス
- `unknown_status`: 未対応ステータス
- `unknown_flight_number`: 対象外便
- `invalid_date`: 日付形式不正

修正手順:

1. BigQueryで対象行と出典を確認します。
2. 正しい値を確認できた場合だけ修正します。
3. 未取得・未対応ステータスなら`--cleanup-only`を使用します。
4. 必要ならCSVを修正し、`python import_user_csv.py --csv path/to/data.csv`でBigQueryへ再投入します。
5. `python data_quality.py --format markdown --output data_quality_report.md --fail-on error`で再確認します。

## ローカル検証

```bash
python -m pytest -q
python -m compileall -q .
python data_quality.py --format markdown --output data_quality_report.md --fail-on error
python build_static.py
```

品質検査と静的生成にはBigQuery Application Default Credentialsが必要です。

## データ修正の原則

- 取得失敗や不明ステータスを欠航と推測しない
- 欠航理由を推測しない
- 既知値を`NULL`や`未確認`で劣化させない
- `date + flight_number`を一意キーとする
- 視程補完値は実測ではなく数値予報値として`visibility_source`を残す
- SQLダンプやDBファイルをGitHubへ置かない

## 公開後確認

キャッシュ回避クエリを付けて公開HTMLを確認します。

```text
https://toyo1621.github.io/8jo-flight-forecast-bot/?verify=<timestamp>
```

サイト名「八丈島便 運航統計参考値」、実データの取得時刻、未校正の注意書き、台風影響度の欠測通知を確認します。
