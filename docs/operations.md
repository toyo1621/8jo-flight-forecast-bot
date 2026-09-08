# 運用Runbook

## 依存関係

CI、Pages公開、日次収集、週次評価は`requirements.lock`をconstraintsとして
同じ解決結果を使います。依存を更新する場合は、対応するPython版でlockを再生成し、
`pytest`、`ruff check .`、`pip-audit -r requirements.lock`を実行してから変更します。

公開状態用のBigQueryスキーマを先に確認する場合は、認証済み環境で
`python migrate_prediction_publications.py`を実行します。これはdry-runが既定で、
本番へ適用する場合だけ明示的に`--apply`を付けます。この作業は本番データの削除や
既存スナップショットの上書きを行いません。

## 定常監視

- `Deploy forecast site to Pages`: 同じSHAのpytest/ruff、生成・検証・公開確認、Data Quality Reportが成功していること
- `Daily Flight & Weather Data Collection`: JST 09:23・14:23・18:23・21:23に収集。便別確定結果・気象欠測・未実行を別に確認すること
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

長寿命のFlaskプロセスで使う過去実績キャッシュは既定300秒で期限切れになり、外部のBigQuery更新を反映します。必要な運用環境では`HISTORY_CACHE_TTL_SECONDS`で短縮・延長でき、収集書き込み後も明示的にキャッシュを消去します。

予報日が0件になった静的ビルドは、BigQueryに候補を保存した後でもHTMLを書き出さず失敗します。Pagesのdeploy jobは起動しないため、前回公開を維持します。予報日が一部欠けた場合は、入力範囲内の中間日を便ごとの`weather_missing`として表示し、欠航や正常運航へ変換しません。

## 台風影響度の欠測・因子内訳

- API全体が失敗した場合: 7時間以内のキャッシュを使用し、なければ補正なしと通知します。
- 一部の日付がない場合: その日を`low`と見なさず、補正を適用していない日付範囲を通知します。
- 現在の表示範囲には、当日を含む11日分が必要です。
- `riskLevel`だけの旧キャッシュや因子内訳のない応答: 台風接近リスクを注意表示し、数値補正はしません。
- サンプル不足・検証期間: GitHub Actionsの環境変数`TYPHOON_NUMERIC_ADJUSTMENT_ENABLED=false`で数値補正を停止します。
- 補正の適用状況、外部因子、weather-only / typhoon-only / combinedは`prediction_snapshots.factor_breakdown_json`で確認します。

## ODPT・日次収集障害

取得失敗・未確定ステータス・日付不明の情報を運航実績へ変換しません。確定した便から保存し、気象取得失敗では結果を捨てません。rawは秘匿情報除去後に保存し、runの開始・成功・部分取得・失敗を記録します。新スキーマを先に移行してください。[移行・保護・復旧手順](collection_outcomes.md)

一時的なHTTPエラー・タイムアウトは指数バックオフで最大3回再試行します。raw保存済みrunを再処理する場合は、BigQuery認証後に次を実行します。

```bash
python data_collector.py --replay-run-id <run_id>
```

日次workflowは直近14日分の`collection_runs`を確認し、run_idとattempt単位でstarted/succeeded/failedを時刻順に集約します。同時に`flight_weather_logs`の指定日ごとの実便数を読み取り、`run_failed`、`started_without_completion`、`data_incomplete`、`data_missing`、`success_record_missing`、`not_recorded`、`not_recorded_before_monitoring`、`not_due`を混同しません。最終成功日、連続欠損日数、最新runも表示します。欠損日がある場合はworkflowを失敗させ、run_idを特定してraw再生または原因修正を行います。監視導入日を設定する場合は`COLLECTION_MONITOR_START_DATE=YYYY-MM-DD`を使います。欠損検知が失敗した場合は、既存の未解決Issueへ追記するか新規Issueを作成します。

収集を過去日に再実行する場合は、`python data_collector.py --date YYYY-MM-DD`を使います。収集runにはODPT・気象ソースごとの状態、開始・完了時刻、raw保存件数が残ります。

欠航理由カテゴリは`weather`（天候・台風・強風等）、`operational`（機材・整備・乗員等）、`airport`（空港・滑走路・管制等）、`other`、`unknown`、`not_applicable`に分けます。理由がない、または未確認の行は`unknown`で保存し、天候起因の学習・評価へ自動算入しません。`status_reason_source`、`status_reason_observed_at`、`status_reason_confidence`は不明な場合に推測で埋めません。

確認項目:

- GitHub Secret `ODPT_API_KEY`
- ODPTとOpen-Meteoの応答
- 実行時刻が最終便の結果確定後か
- Workload Identity FederationとBigQuery書き込み権限

既存の未取得・未対応ステータス行を確認する場合は、`Daily Flight & Weather Data Collection`を`cleanup_only=true`で手動実行します。既定はdry-runで、対象件数・対象範囲・理由・audit_idを`maintenance_audit`へ記録します。削除を適用する場合だけ、対象日または全体の範囲を確認したうえで`cleanup_apply=true`と理由を指定します。通常の日次収集では削除しません。

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

Pagesの静的生成時に、JMA・GFS・ECMWFの各値を`prediction_snapshots`へ不変保存し、公開候補と公開確認を`prediction_publications`へ保存します。各スナップショットには予測生成時刻、取得元別の取得時刻、予報有効時刻、リード時間、provider/model、取得元endpoint、キャッシュfallback、コードSHA、設定版、算出状態、モデル固有入力を記録します。同じ計算内容の`snapshot_id`は入力・結果の内容ハッシュで再送時に重複登録を抑止し、取得時刻だけの違いでは別行にしません。公開候補はPagesのライブHTMLに同じartifact IDが存在するまで`candidate`のままです。

Pages deploy後に`publish_prediction_snapshots.py`がHTTP 200とartifact IDを確認し、対応する候補だけを`published`へ更新します。この更新が失敗しても公開済みHTMLを未確認の予測として評価せず、同じartifact IDでスクリプトを再実行できます。生成・検証・デプロイが失敗した候補は公開履歴と厳密評価に採用しません。

過去日ページは、予測対象時刻より前に保存された最後のスナップショットを
「公開時の予測」として採用し、`flight_weather_logs`の運航結果を結合して再生成します。
結果未取得は欠航として扱いません。アーカイブ取得に失敗したPages buildは失敗させ、
既存の公開アーカイブを空の生成物で置き換えません。

`provenance_status=unknown`の旧キャッシュ、取得時刻不明の行、`candidate`の未公開行は、後続の外部評価で厳密な時系列検証から除外します。これは欠測や公開未確認を現在の予報として扱わないための区別です。旧行は勝手に`published`へ変更せず、アーカイブ表示では`legacy`として扱います。
新パイプラインが保存した行は公開追跡マーカーを持つため、候補記録や公開確認が欠けた場合もlegacyへ暗黙変換されません。公開確認のない旧行だけが、既存URL維持のため明示的な`legacy`表示になります。

週次の`Evaluate published forecasts` workflowは、実績と結合した公開値を対象に、モデル別・便別・リード日別Brier score、10ポイント幅の信頼度ビン、ECE、運航率ベースライン、常時運航ベースライン、条件付運航の感度分析、時系列ローリング分割をJSON/Markdown artifactへ出力します。ローリング基準値には、各予測の生成時刻より後に収集された運航結果を使いません。評価対象がない場合は`insufficient_data`として成功扱いにせず、レポート生成後にworkflowを失敗させます。詳細は`docs/evaluation.md`を参照してください。

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
