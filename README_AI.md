# AI向け開発ガイド

この文書は、AIエージェントが本プロジェクトの現在仕様を崩さずに開発を継続するためのコンテキストです。利用者向けの説明とセットアップは[`README.md`](README.md)、確率計算の詳細は[`docs/forecast_spec.md`](docs/forecast_spec.md)を参照してください。

## プロジェクトの目的

羽田発・八丈島行きのANA1891(1便)、ANA1893(2便)、ANA1895(3便)について、過去実績と公開気象予報から統計的な運航確率を表示する静的サイトです。航空会社の判断や公式な気象予報ではありません。

## 現在のデータフロー

1. `data_collector.py`がODPTの運航情報とOpen-Meteoの気象情報を収集します。
2. 本番の過去データはBigQueryの`hachijo-flight-forecast.flight_forecast.flight_weather_logs`に保存します。
3. `build_static.py`がOpen-Meteoの標準予報、羽田側の標準予報、GFS・ECMWFアンサンブル、JMA予報を取得します。
4. `forecast_engine.py`と`web_app.py`が確率、天候信頼度、モデル別参考値、類似実績を計算します。
5. `templates/index.html`をレンダリングし、`dist/`をGitHub Pagesへデプロイします。

GitHub Pagesは`main`へのpush時、手動実行時、6時間ごとのスケジュールで更新します。運航実績収集は毎日21:00 JSTです。

## データソースの役割

- **主予報**: Open-Meteo標準予報。画面の大きな「運航確率」はこの気象条件で計算します。
- **GFS**: 31メンバー。便カードと詳細画面に中央値を参考運航確率として表示します。
- **ECMWF**: 31メンバー。便カードと詳細画面に中央値を参考運航確率として表示します。
- **JMA**: Open-Meteoの`jma_seamless`（GSM・MSM）。決定論的な独立参考値です。
- **短期の扱い**: 日本周辺では短期ほどJMAを重視します。ただしGFS・ECMWFも比較材料として残し、モデル差そのものをリスク情報として扱います。
- **天候信頼度**: GFS 31 + ECMWF 31の最大62通りで計算します。JMAは含めません。
- **台風接近リスク**: 八丈島側と羽田側の気圧、アンサンブルの大きな下振れを使う総合リスクです。
- **過去実績**: 本番はBigQueryが正です。SQLiteの`flights.db`と`data/flights_dump.sql`はローカル・レビュー用の補助スナップショットです。

## 確率計算の不変条件

`forecast_engine.predict_flight_probability()`を変更するときは、以下を意図せず変えないでください。

しきい値や補正倍率は`app_config.py`に集約しています。数値を変える場合は、コード中の直書きではなくこのファイルを更新してください。

1. 過去データを風向差30度以内・風速差3 m/s以内で検索します。
2. 5件未満なら45度以内・5 m/s以内へ広げます。
3. それでも5件未満なら全履歴を使います。
4. `運航`、`遅延`、`運航(条件付)`は1.0、それ以外は0.0です。旧データの`通常`も後方互換で1.0として扱います。
5. 視程5 km未満は`× 0.6`です。
6. 降水量2 mm/h以上は`× 0.85`、8 mm/h以上は`× 0.7`です。
7. 低層雲量80%超は`× 0.9`、95%以上は`× 0.75`です。
8. 最大瞬間風速15 m/s以上は`× 0.9`、20 m/s以上は`× 0.55`です。該当しない場合のみ、平均風速10 m/s以上で`× 0.9`です。
9. 表示確率の範囲は0〜97%です。
10. 風向120〜240度かつ平均風速9 m/s以上では「南風注意」を表示しますが、それ自体では確率を下げません。
11. 主予報とJMA参考値の差が20ポイント以上なら「気象モデル差に注意」を追加します。
12. 台風接近リスクは、八丈島または羽田の気圧が低い場合、またはアンサンブルの中央80%幅が50ポイント以上かつ下位10%が45%以下の場合に発火します。主予報、GFS、ECMWF、JMAの表示確率に`× 0.6`を適用し、「台風接近リスク」を追加します。

## 天候信頼度

各アンサンブルメンバーで運航確率を再計算し、昇順に並べた10パーセンタイルから90パーセンタイルまでの幅を使います。

- A: 10ポイント以内
- B: 20ポイント以内
- C: 30ポイント以内
- D: 40ポイント以内
- E: 40ポイント超

アンサンブルが10件未満なら、予報日までの日数による暫定評価へフォールバックします。1日の表示には、その日の各便で最も低い信頼度を採用します。

## 類似過去実績

`find_similar_flights()`は同じ便名の履歴だけを対象に10件返します。通常時も風向と風速を比較しますが、主予報で次の条件が悪化している場合は該当項目を強く重み付けします。

- 平均風速10 m/s以上
- 最大瞬間風速15 m/s以上
- 低層雲量70%以上
- 視程10 km以下

予報に値があるのに過去レコードが欠測している場合はペナルティを加えます。類似検索と主確率の母集団検索は別ロジックです。

## UIの不変条件

- サイト名は「八丈島運航統計予測」です。
- 便名は`ANA1891(1便)`、`ANA1893(2便)`、`ANA1895(3便)`です。
- 「雲量」ではなく「低層雲量」と表示します。
- 便カードは**運航確率60%未満**のときだけオレンジにします。警告の有無でカード色を変えないでください。
- 便カードの主予報確率には`(Open-Meteo主予報)`を添えます。
- 便カードにはGFS・ECMWF・JMAの参考運航確率を表示し、各モデル名の横に`static/flags/us.svg`、`static/flags/eu.svg`、`static/flags/jp.svg`を表示します。
- 運航確率の左には記号を表示します。95%以上は`◎`、75%以上は`〇`、35%以上は`△`、35%未満は`×`です。
- 警告文は確率色とは独立して表示します。
- 当日便は到着予定時刻の30分後を過ぎたら非表示にします。
- 詳細画面は`詳しく見る(運航実績・気象情報)`から開きます。
- 詳細画面の確率ラベルは「主予報(Open-Meteo)での運航確率」「GFS予報での参考運航確率」「ECMWF予報での参考運航確率」「JMA予報での参考運航確率」です。
- CSSやJavaScriptを変更したら、`templates/index.html`のクエリ文字列を更新してGitHub Pagesのキャッシュを回避します。
- 詳細画面には気圧も表示します。台風接近リスクの説明に使うため、予測関数へ渡す項目と表示用の気象項目を混ぜないでください。
- 便カード・詳細画面に渡す表示用フィールドは`presentation.py`で整形します。テンプレートに確率しきい値やモデル別表示の分岐を増やさないでください。

## ステータス表記

- 条件付きで運航: `運航(条件付)`
- 引き返し: `条件付き→引返欠航`
- 遅延は統計上、運航として扱います。
- BigQueryのレコード識別子は`date + flight_number`であり、連番`id`は使用しません。
- DB保存時は`運航`と旧表記の`通常`を`運航`、`条件付→運航`・`条件付き運航`・`条件付き→就航`を`運航(条件付)`へ正規化します。条件付きか判別できない運航は`運航`のままにします。DBと表示で同じ表記を使用します。

## 主要ファイル

| ファイル | 責務 |
| --- | --- |
| `app_config.py` | 予報日数、確率しきい値、補正倍率、信頼度境界などの共通設定 |
| `forecast_cache.py` | Open-Meteo取得失敗時に使う前回予報キャッシュ |
| `presentation.py` | 便カード・詳細画面向けの表示用データ整形 |
| `forecast_engine.py` | 主確率、警告、類似実績 |
| `web_app.py` | 気象API、62メンバー、JMA比較、表示用データ |
| `bigquery_storage.py` | BigQuery取得・`date + flight_number`でのMERGE |
| `data_quality.py` | BigQuery/SQLiteのデータ品質チェックとレポート |
| `flight_metadata.py` | 便表示名とステータス正規化 |
| `build_static.py` | `dist/`生成 |
| `templates/index.html` | 全画面HTMLと説明文 |
| `static/styles.css` | レスポンシブUI |
| `static/app.js` | 詳細ダイアログ操作 |
| `static/flags/*.svg` | GFS・ECMWF・JMA表示用の旗アイコン |
| `.github/workflows/pages.yml` | 6時間ごとのPages生成・公開 |
| `.github/workflows/data_collection.yml` | 日次収集 |
| `.github/workflows/ci.yml` | PR/push時の自動テスト |
| `docs/operations.md` | 運用・復旧手順 |
| `docs/nfr_scorecard.md` | 非機能要件の評価基準 |

## ローカル検証

Windowsではリポジトリ内の仮想環境を優先します。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe data_quality.py --backend sqlite --format markdown --output data_quality_report.md --fail-on none
.\.venv\Scripts\python.exe build_static.py
```

BigQueryを使って静的生成する場合はApplication Default Credentialsと次の環境変数が必要です。

```powershell
$env:FORECAST_DATA_BACKEND = "bigquery"
$env:GCP_PROJECT_ID = "hachijo-flight-forecast"
$env:BIGQUERY_DATASET = "flight_forecast"
$env:BIGQUERY_TABLE = "flight_weather_logs"
$env:BIGQUERY_LOCATION = "asia-northeast1"
.\.venv\Scripts\python.exe data_quality.py --backend bigquery --format markdown --output data_quality_report.md
.\.venv\Scripts\python.exe build_static.py
```

ユーザー提供CSVをSQLiteとBigQueryへ取り込む場合:

```powershell
.\.venv\Scripts\python.exe import_user_csv.py --csv "C:\path\to\data.csv" --backend both
.\.venv\Scripts\python.exe backfill_bigquery_visibility.py
.\.venv\Scripts\python.exe db_snapshot.py export
```

`import_user_csv.py`は、未知・未確定の`?`または`？`を推測せずスキップします。欠航理由は`status_reason`へ分離し、不明な欠航理由は`未確認`として明示します。Archive APIで視程が欠測する場合はHistorical Forecast APIで補完し、`visibility_source`へ出典を保存します。

テストでは外部APIやBigQueryをモックし、認証不要で完走できる状態を維持してください。

## GitHub設定

- Secret: `ODPT_API_KEY`
- Repository Variables: `GCP_WORKLOAD_IDENTITY_PROVIDER`、`GCP_SERVICE_ACCOUNT`
- GitHub ActionsはWorkload Identity FederationでGoogle Cloudへ認証します。サービスアカウント鍵をリポジトリへ保存しないでください。
- 秘密情報や認証ファイルの扱いは`SECURITY.md`を正とします。`.env`、Google CloudのJSON鍵、BigQueryの個人データ入りexportをコミットしないでください。
- Pages更新では`.cache/forecast_bundle.json`をActions Cacheに保存し、Open-Meteoの主予報取得に失敗した場合は前回成功データを使って公開ページを維持します。
- `data_quality.py`はCI、Pages、日次収集でレポートを出します。Pagesと日次収集では可用性を優先し、データ品質の指摘はworkflowを止めずStep Summary/artifactで確認します。
- workflowにはtimeoutとconcurrencyを設定しています。外部APIやBigQueryが遅い場合でも、Actionsが長時間滞留しない前提を維持してください。
- データ修正や運用障害は`.github/ISSUE_TEMPLATE/`のテンプレートで記録します。欠航理由など未確認データはIssueに残し、推測でDBへ入れないでください。
- DependabotがPython依存やGitHub Actionsを更新します。自動更新PRを閉じる場合は、同等の更新を別PRで取り込んだ理由をコメントしてください。

## 変更時チェックリスト

1. 実装、`README.md`、`docs/forecast_spec.md`、この文書の数値が一致しているか確認します。
2. 表示文言を変えたら`test_web_app.py`の期待値も更新します。
3. 確率ロジックを変えたら境界値の回帰テストを追加します。
4. BigQueryスキーマを変える場合は移行・MERGE・テストを同時に更新します。
5. `.venv\Scripts\python.exe -m pytest -q`を実行します。
6. `data_quality.py`を実行し、データ品質レポートを確認します。
7. 公開後はキャッシュを避けたURLでGitHub PagesのHTML/CSSを確認します。
8. GitHub ActionsのCI/Pages/Data CollectionバッジがREADMEで確認できる状態を維持します。

## 注意

- ユーザーが入力・調査した過去実績を勝手に上書き、推測、削除しないでください。
- 欠航理由が不明な場合は推測せず`未確認`として保存し、後でユーザー調査により置き換えます。
- 気象データの欠測と0は区別してください。
- Open-Meteo、ODPT、BigQueryの障害時にも、JMAやアンサンブルの一部欠損で全体を落とさない既存のフォールバックを維持してください。

