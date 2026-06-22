# AI向け開発ガイド

この文書は、AIエージェントが本プロジェクトの現在仕様を崩さずに開発を継続するためのコンテキストです。利用者向けの説明とセットアップは[`README.md`](README.md)、確率計算の詳細は[`docs/forecast_spec.md`](docs/forecast_spec.md)を参照してください。

## プロジェクトの目的

羽田発・八丈島行きのANA1891(1便)、ANA1893(2便)、ANA1895(3便)について、過去実績と公開気象予報から統計的な就航確率を表示する静的サイトです。航空会社の判断や公式な気象予報ではありません。

## 現在のデータフロー

1. `data_collector.py`がODPTの運航情報とOpen-Meteoの気象情報を収集します。
2. 本番の過去データはBigQueryの`hachijo-flight-forecast.flight_forecast.flight_weather_logs`に保存します。
3. `build_static.py`がOpen-Meteoの標準予報、GFS・ECMWFアンサンブル、JMA予報を取得します。
4. `forecast_engine.py`と`web_app.py`が確率、天候信頼度、モデル別参考値、類似実績を計算します。
5. `templates/index.html`をレンダリングし、`dist/`をGitHub Pagesへデプロイします。

GitHub Pagesは`main`へのpush時、手動実行時、6時間ごとのスケジュールで更新します。運航実績収集は毎日21:00 JSTです。

## データソースの役割

- **主予報**: Open-Meteo標準予報。画面の大きな「就航確率」はこの気象条件で計算します。
- **GFS**: 31メンバー。詳細画面に中央値を参考就航確率として表示します。
- **ECMWF**: 31メンバー。詳細画面に中央値を参考就航確率として表示します。
- **JMA**: Open-Meteoの`jma_seamless`（GSM・MSM）。決定論的な独立参考値です。
- **天候信頼度**: GFS 31 + ECMWF 31の最大62通りで計算します。JMAは含めません。
- **過去実績**: 本番はBigQueryが正です。SQLiteの`flights.db`と`data/flights_dump.sql`はローカル・レビュー用の補助スナップショットです。

## 確率計算の不変条件

`forecast_engine.predict_flight_probability()`を変更するときは、以下を意図せず変えないでください。

1. 過去データを風向差30度以内・風速差3 m/s以内で検索します。
2. 5件未満なら45度以内・5 m/s以内へ広げます。
3. それでも5件未満なら全履歴を使います。
4. `通常`、`遅延`、`条件付き→就航`は1.0、それ以外は0.0です。
5. 視程5 km未満は`× 0.6`です。
6. 低層雲量90%超は`× 0.9`です。
7. 最大瞬間風速15 m/s以上は`× 0.9`です。該当しない場合のみ、平均風速10 m/s以上で`× 0.9`です。
8. 表示確率の範囲は0〜97%です。
9. 風向120〜240度かつ平均風速9 m/s以上では「南風注意」を表示しますが、それ自体では確率を下げません。
10. 主予報とJMA参考値の差が20ポイント以上なら「気象モデル差に注意」を追加します。

## 天候信頼度

各アンサンブルメンバーで就航確率を再計算し、昇順に並べた10パーセンタイルから90パーセンタイルまでの幅を使います。

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

- サイト名は「八丈島就航統計予測」です。
- 便名は`ANA1891(1便)`、`ANA1893(2便)`、`ANA1895(3便)`です。
- 「雲量」ではなく「低層雲量」と表示します。
- 便カードは**就航確率60%未満**のときだけオレンジにします。警告の有無でカード色を変えないでください。
- 警告文は確率色とは独立して表示します。
- 当日便は到着予定時刻の30分後を過ぎたら非表示にします。
- 詳細画面の確率ラベルは「主予報(Open-Meteo)での就航確率」「GFS予報での参考就航確率」「ECMWF予報での参考就航確率」「JMA予報での参考就航確率」です。
- CSSやJavaScriptを変更したら、`templates/index.html`のクエリ文字列を更新してGitHub Pagesのキャッシュを回避します。

## ステータス表記

- 条件付きで就航: `条件付き→就航`
- 引き返し: `条件付き→引返欠航`
- 遅延は統計上、就航として扱います。
- BigQueryのレコード識別子は`date + flight_number`であり、連番`id`は使用しません。
- DB保存時は`運航`を`通常`、`条件付→運航`を`条件付き運航`へ正規化します。表示時のみ`条件付き→就航`へ変換します。

## 主要ファイル

| ファイル | 責務 |
| --- | --- |
| `forecast_engine.py` | 主確率、警告、類似実績 |
| `web_app.py` | 気象API、62メンバー、JMA比較、表示用データ |
| `bigquery_storage.py` | BigQuery取得・`date + flight_number`でのMERGE |
| `flight_metadata.py` | 便表示名とステータス正規化 |
| `build_static.py` | `dist/`生成 |
| `templates/index.html` | 全画面HTMLと説明文 |
| `static/styles.css` | レスポンシブUI |
| `static/app.js` | 詳細ダイアログ操作 |
| `.github/workflows/pages.yml` | 6時間ごとのPages生成・公開 |
| `.github/workflows/data_collection.yml` | 日次収集 |

## ローカル検証

Windowsではリポジトリ内の仮想環境を優先します。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe build_static.py
```

BigQueryを使って静的生成する場合はApplication Default Credentialsと次の環境変数が必要です。

```powershell
$env:FORECAST_DATA_BACKEND = "bigquery"
$env:GCP_PROJECT_ID = "hachijo-flight-forecast"
$env:BIGQUERY_DATASET = "flight_forecast"
$env:BIGQUERY_TABLE = "flight_weather_logs"
$env:BIGQUERY_LOCATION = "asia-northeast1"
.\.venv\Scripts\python.exe build_static.py
```

ユーザー提供CSVをSQLiteとBigQueryへ取り込む場合:

```powershell
.\.venv\Scripts\python.exe import_user_csv.py --csv "C:\path\to\data.csv" --backend both
.\.venv\Scripts\python.exe backfill_bigquery_visibility.py
.\.venv\Scripts\python.exe db_snapshot.py export
```

`import_user_csv.py`は、未知・未確定の`?`または`？`を推測せずスキップします。欠航理由は`status_reason`へ分離します。Archive APIで視程が欠測する場合はHistorical Forecast APIで補完し、`visibility_source`へ出典を保存します。

テストでは外部APIやBigQueryをモックし、認証不要で完走できる状態を維持してください。

## GitHub設定

- Secret: `ODPT_API_KEY`
- Repository Variables: `GCP_WORKLOAD_IDENTITY_PROVIDER`、`GCP_SERVICE_ACCOUNT`
- GitHub ActionsはWorkload Identity FederationでGoogle Cloudへ認証します。サービスアカウント鍵をリポジトリへ保存しないでください。

## 変更時チェックリスト

1. 実装、`README.md`、`docs/forecast_spec.md`、この文書の数値が一致しているか確認します。
2. 表示文言を変えたら`test_web_app.py`の期待値も更新します。
3. 確率ロジックを変えたら境界値の回帰テストを追加します。
4. BigQueryスキーマを変える場合は移行・MERGE・テストを同時に更新します。
5. `.venv\Scripts\python.exe -m pytest -q`を実行します。
6. 公開後はキャッシュを避けたURLでGitHub PagesのHTML/CSSを確認します。

## 注意

- ユーザーが入力・調査した過去実績を勝手に上書き、推測、削除しないでください。
- 欠航理由が不明な場合は推測せず`NULL`のままにします。
- 気象データの欠測と0は区別してください。
- Open-Meteo、ODPT、BigQueryの障害時にも、JMAやアンサンブルの一部欠損で全体を落とさない既存のフォールバックを維持してください。

