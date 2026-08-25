# 運用Runbook

## 定常監視

- `Deploy forecast site to Pages`: 6時間ごとの生成・公開とData Quality Reportが成功していること
- `Daily Flight & Weather Data Collection`: 毎日21:00 JSTの3便収集が成功していること
- `CodeQL`と`CI`: mainとPull Requestの検査が成功していること
- [公開サイト](https://toyo1621.github.io/8jo-flight-forecast-bot/): 予報データ取得時刻、11日分の表示、詳細ダイアログを確認すること
- フッターの`過去7日間のアクセス数`: Cloudflare Web Analyticsのページビュー集計が更新されていること

Data Quality Reportの`error`はPagesと日次収集を失敗させます。エラーを無視して公開を更新しません。

## アクセス数の集計

- Cloudflare Web Analyticsのページビューを、JSTの暦日単位で直近7日分取得して静的HTMLへ埋め込みます。ユニークユーザー数ではありません。
- 集計処理は`Deploy forecast site to Pages`のビルド中に実行し、6時間ごとの定期デプロイで表示を更新します。
- 計測開始前の日は`未計測`と表示します。Cloudflare側の集計反映に時間差があるため、当日値は途中経過です。
- API取得に失敗してもPagesの予報ビルド・公開は継続します。前回成功した7日分がキャッシュにあれば`stale`として最終取得時刻を表示し、なければ`unavailable`として取得不能を表示します。アクセス数を推測値や0件に置き換えません。
- 取得失敗時はPagesのStep Summaryにも記録します。予報の公開成功とアクセス解析の状態を別々に確認してください。
- CloudflareのAPIトークンはGitHub Secretの`CLOUDFLARE_ANALYTICS_API_TOKEN`だけで管理します。公開HTMLに埋め込むWeb Analyticsビーコンのトークンとは別物です。

## 障害時の優先順位

1. 公開済みサイトが表示できるか確認します。
2. Pagesまたは日次収集の直近ログとData Quality Reportを確認します。
3. JMA主予報(Open-Meteo経由)、補完予報、台風影響度API、ODPT、BigQuery認証、コード変更のどこで失敗したか切り分けます。
4. データを推測で補わず、原因解消後にworkflowを手動実行します。

## 予報API障害

JMA主予報の取得に失敗した場合、7時間以内のキャッシュがあればその取得時刻と注意文を表示します。期限切れキャッシュしかない場合は新しいPagesを公開しません。最大瞬間風速・視程の補完だけが失敗した場合はJMA主予報を維持し、該当項目を欠測として通知します。アンサンブルだけが失敗した場合は、主予報を維持し、有効なキャッシュ利用または欠測を表示します。

## 台風影響度の欠測・因子内訳

- API全体が失敗した場合: 7時間以内のキャッシュを使用し、なければ補正なしと通知します。
- 一部の日付がない場合: その日を`low`と見なさず、補正を適用していない日付範囲を通知します。
- 現在の表示範囲には、当日を含む11日分が必要です。
- `riskLevel`だけの旧キャッシュや因子内訳のない応答: 台風接近リスクを注意表示し、数値補正はしません。
- サンプル不足・検証期間: GitHub Actionsの環境変数`TYPHOON_NUMERIC_ADJUSTMENT_ENABLED=false`で数値補正を停止します。
- 補正の適用状況、外部因子、weather-only / typhoon-only / combinedは`prediction_snapshots.factor_breakdown_json`で確認します。

## ODPT・日次収集障害

取得失敗、対象3便不足、未対応ステータス、気象欠測では運航実績本表を更新しません。失敗を欠航へ変換しないでください。各APIの応答はAPIキー等を除去して`flight_collection_raw`へ保存し、runの開始・成功・失敗を`collection_runs`へ記録します。

一時的なHTTPエラー・タイムアウトは指数バックオフで最大3回再試行します。raw保存済みrunを再処理する場合は、BigQuery認証後に次を実行します。

```bash
python data_collector.py --replay-run-id <run_id>
```

日次workflowは直近14日分の`collection_runs`を確認し、3便の成功記録がない日をStep Summaryとartifactへ出します。最終成功日、連続欠損日数、最新runも表示します。欠損日がある場合はworkflowを失敗させ、run_idを特定してraw再生または原因修正を行います。欠損検知が失敗した場合は、既存の未解決Issueへ追記するか新規Issueを作成します。

収集を過去日に再実行する場合は、`python data_collector.py --date YYYY-MM-DD`を使います。収集runにはODPT・気象ソースごとの状態、開始・完了時刻、raw保存件数が残ります。

欠航理由カテゴリは`weather`（天候・台風・強風等）、`operational`（機材・整備・乗員等）、`airport`（空港・滑走路・管制等）、`other`、`unknown`、`not_applicable`に分けます。理由がない、または未確認の行は`unknown`で保存し、天候起因の学習・評価へ自動算入しません。`status_reason_source`、`status_reason_observed_at`、`status_reason_confidence`は不明な場合に推測で埋めません。

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

## 予測スナップショット

Pagesの静的生成時に、公開対象のJMA・GFS・ECMWFの各値を`prediction_snapshots`へ不変保存します。各行には予測対象時刻、データ取得時刻、リード時間、provider/model、取得元endpoint、キャッシュfallback、コードSHA、設定版、算出状態を記録します。同じ`snapshot_id`は再実行しても更新せず、重複登録を抑止します。

`provenance_status=unknown`の旧キャッシュや取得時刻不明の行は、後続の外部評価で厳密な時系列検証から除外します。これは欠測を現在の予報として扱わないための区別です。

週次の`Evaluate published forecasts` workflowは、実績と結合した公開値を対象に、モデル別・便別・リード日別Brier score、10ポイント幅の信頼度ビン、ECE、運航率ベースライン、常時運航ベースライン、条件付運航の感度分析、時系列ローリング分割をJSON/Markdown artifactへ出力します。評価対象がない場合は`insufficient_data`として成功扱いにせず、レポート生成後にworkflowを失敗させます。詳細は`docs/evaluation.md`を参照してください。

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

サイト名「八丈島便 運航の目安」、実データの取得時刻、未校正の注意書き、台風影響度の欠測通知を確認します。
