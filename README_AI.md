# AI向け開発ガイド

この文書は、AIエージェントが現在仕様を崩さずに開発を継続するためのコンテキストです。利用者向け説明は[`README.md`](README.md)、計算仕様は[`docs/forecast_spec.md`](docs/forecast_spec.md)を参照してください。

## プロジェクトの目的

羽田発・八丈島行きのANA1891、ANA1893、ANA1895について、同じ便の過去実績と公開気象予報から未校正の運航統計参考値を表示する静的サイトです。表示する%は、統計的に検証・校正された将来確率ではありません。

## データフロー

1. `data_collector.py`がODPTの当日運航結果とOpen-Meteoの気象値を取得します。
2. 対象3便と必須気象値がすべて揃った場合だけ、BigQueryの`hachijo-flight-forecast.flight_forecast.flight_weather_logs`へMERGEします。
3. `build_static.py`がOpen-Meteo標準予報、GFS・ECMWFアンサンブル、JMA予報、外部の台風影響度を取得します。
4. `forecast_engine.py`と`web_app.py`が便ごとの統計参考値、天候信頼度、モデル別参考値、類似実績を計算します。
5. `templates/index.html`を`dist/`へレンダリングし、GitHub Pagesへデプロイします。

Pagesは`main`へのpush、手動実行、6時間ごとのスケジュールで更新します。運航実績収集は毎日21:00 JSTです。

## 保存先

- 運用データの保存・参照先はBigQueryだけです。
- `data/flights_dump.sql`とSQLiteの運用経路は削除済みです。
- `user_raw_data.csv`は移行入力として一時的に残していますが、実行時やPages生成では参照しません。
- `migrate_sqlite_to_bigquery.py`は、手元の旧SQLiteをBigQueryへ一方向移行するためだけに残しています。
- SQLダンプ、DBファイル、Google Cloud鍵、`.env`をコミットしないでください。

## データ収集の不変条件

- ODPTの取得失敗、JSON不正、対象便不足、重複、空・未対応ステータスでは保存しません。
- Open-Meteoの取得失敗、応答不正、必須気象値の欠測では保存しません。
- 対象3便がすべて揃うまで、1行も保存しません。
- 取得失敗を`欠航`や0点へ変換しないでください。
- BigQuery MERGEでは、入力が`NULL`の気象値で既存の正常値を上書きしません。
- 同一ステータスの既知欠航理由を、`NULL`や`未確認`で上書きしません。
- `--demo`はBigQueryへ書き込みません。
- `--cleanup-only`は外部APIを呼ばず、未取得・未対応ステータス行だけを削除します。

## データソース

- **主予報**: Open-Meteo標準予報。画面の大きな統計参考値に使用します。
- **GFS**: 最大31メンバー。中央値を比較用の統計参考値として表示します。
- **ECMWF**: 最大31メンバー。中央値を比較用の統計参考値として表示します。
- **JMA**: Open-Meteoの`jma_seamless`。独立した比較用参考値です。
- **天候信頼度**: GFS 31 + ECMWF 31の最大62通りで計算し、JMAは含めません。
- **台風影響度**: 外部APIの`targets.flight.riskLevel`をJMAモードで取得します。
- **過去実績**: BigQueryのみを参照します。

台風影響度APIは当日を含む11日分が必要です。`low`は補正なし、`medium`はリスク小で`× 0.9`、`high`はリスク中で`× 0.8`、`severe`はリスク大で`× 0.7`です。日付が欠けている場合は`low`と見なさず、その日には補正を適用しない旨を表示します。

## 統計参考値の不変条件

`forecast_engine.predict_flight_probability()`の関数名と`probability`フィールドは既存互換の内部名です。UIや説明文では「運航統計参考値」または「統計参考値」と呼び、校正済み確率と表現しないでください。

1. 指定された便と同じ便の履歴だけを対象にします。
2. 風向差30度以内・風速差3 m/s以内で検索します。
3. 5件未満なら45度以内・5 m/s以内へ広げます。
4. それでも5件未満なら同じ便の全履歴を使います。
5. `運航`と`運航(条件付)`は1.0、`欠航`と`条件付き→引返欠航`は0.0です。
6. 未取得、空、未対応ステータスは保存・集計しません。
7. 視程5 km未満は`× 0.6`、3 km未満は`× 0.45`です。
8. 降水量2 mm/h以上は`× 0.85`、8 mm/h以上は`× 0.7`です。
9. 低層雲量80%超は`× 0.9`、95%以上は`× 0.75`です。
10. 最大瞬間風速15 m/s以上は`× 0.9`、20 m/s以上は`× 0.55`です。該当しない場合だけ、平均風速10 m/s以上で`× 0.9`です。
11. 台風影響度の倍率は主予報、GFS、ECMWF、JMAの各参考値へ適用します。
12. 表示範囲は0〜97%です。
13. 風向120〜240度かつ平均風速9 m/s以上では「南風注意」を表示しますが、それ自体では値を下げません。
14. 主予報とJMA参考値の差が20ポイント以上なら「気象モデル差に注意」を追加します。

しきい値や補正倍率は`app_config.py`に集約します。コード中へ重複して直書きしないでください。

## 時刻の不変条件

- タイムゾーンは`app_config.JST`を使います。
- 便設定は`app_config.FLIGHTS`を唯一の情報源とします。
- 予報参照時刻はANA1891が8時、ANA1893が13時、ANA1895が17時です。
- Web表示、日次収集、CSV取り込み、視程バックフィルで同じ参照時刻を使います。
- 当日便は到着予定時刻の30分後を過ぎたら非表示にします。
- 「予報データ取得」にはページ生成時刻ではなく、実際に表示している予報バンドルの取得時刻を表示します。

## キャッシュ

- `.cache/forecast_bundle.json`のバージョンは`forecast_cache.py`で管理します。
- 主予報の代替に使えるキャッシュは7時間以内だけです。期限切れの主予報を最新として表示しないでください。
- JMA、アンサンブル、台風影響度の取得だけが失敗した場合は、7時間以内の該当キャッシュがあれば使用し、その旨を画面へ表示します。
- 主予報も有効キャッシュもない場合は静的生成を失敗させます。

## UIの不変条件

- サイト名は「八丈島便 運航統計参考値」です。
- 便名は`ANA1891(1便)`、`ANA1893(2便)`、`ANA1895(3便)`です。
- 画面上で「未校正の統計参考値で、将来の運航確率ではない」と明示します。
- 60%未満の便だけをオレンジにします。警告の有無でカード色を変えません。
- 主表示には`(Open-Meteo主予報 / 統計参考値)`を添えます。
- GFS・ECMWF・JMAにも「統計参考値」というラベルを使います。
- 記号は95%以上`◎`、75%以上`〇`、35%以上`△`、35%未満`×`です。
- 「雲量」ではなく「低層雲量」と表示します。
- faviconのロゴはトップへ置かず、フッターだけに表示します。
- 表示用フィールドは`presentation.py`で整形します。

## ステータス

- 条件付きで運航: `運航(条件付)`
- 引き返し: `条件付き→引返欠航`
- 遅延・旧表記の`通常`は保存時に`運航`へ正規化します。
- BigQueryのレコード識別子は`date + flight_number`です。
- ステータス集合と正規化は`flight_metadata.py`を正とし、別ファイルへ複製しないでください。

## 主要ファイル

| ファイル | 責務 |
| --- | --- |
| `app_config.py` | 便、時刻、予報日数、しきい値、補正倍率 |
| `flight_metadata.py` | 便表示名、ステータス集合、正規化 |
| `data_collector.py` | 失敗時に保存しない日次収集とデータ掃除 |
| `bigquery_storage.py` | BigQuery取得・MERGE |
| `bigquery_schema.py` | BigQuery設定、スキーマ、テーブル作成 |
| `forecast_engine.py` | 便別の統計参考値と類似実績 |
| `web_app.py` | 外部予報API、モデル比較、表示データ構築 |
| `forecast_cache.py` | 予報キャッシュと鮮度判定 |
| `presentation.py` | 画面表示用データ整形 |
| `data_quality.py` | BigQuery品質検査 |
| `build_static.py` | `dist/`生成 |
| `.github/workflows/ci.yml` | テストと`pip-audit` |
| `.github/workflows/codeql.yml` | CodeQL検査 |
| `.github/workflows/pages.yml` | 6時間ごとのPages生成・公開 |
| `.github/workflows/data_collection.yml` | 21:00 JSTの日次収集と手動掃除 |

## ローカル検証

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m compileall -q .
```

BigQuery品質検査と静的生成にはApplication Default Credentialsが必要です。

```bash
export GCP_PROJECT_ID=hachijo-flight-forecast
export BIGQUERY_DATASET=flight_forecast
export BIGQUERY_TABLE=flight_weather_logs
export BIGQUERY_LOCATION=asia-northeast1
python data_quality.py --format markdown --output data_quality_report.md --fail-on error
python build_static.py
```

CSV取り込みもBigQuery専用です。Open-Meteo Archiveの取得失敗・欠測時は全件を中止します。

```bash
python import_user_csv.py --csv path/to/data.csv
python backfill_bigquery_visibility.py
```

テストでは外部APIとBigQueryをモックし、認証不要で完走できる状態を維持してください。

## GitHub設定

- Secret: `ODPT_API_KEY`
- Repository Variables: `GCP_WORKLOAD_IDENTITY_PROVIDER`、`GCP_SERVICE_ACCOUNT`
- Google Cloud認証はWorkload Identity Federationを使用します。
- Secret scanning、push protection、Dependabot alerts、automated security fixesを有効にします。
- CIの必須チェックとブランチ保護を有効にします。
- Pagesと日次収集のデータ品質検査は`error`で失敗させます。
- CodeQLとDependabotを継続運用します。

## 変更時チェックリスト

1. 実装、`README.md`、`docs/forecast_spec.md`、この文書の数値が一致しているか確認します。
2. 表示文言を変えたら`test_web_app.py`も更新します。
3. 統計参考値のロジックを変えたら、便の分離と境界値の回帰テストを追加します。
4. BigQueryスキーマを変える場合は移行・MERGE・テストを同時に更新します。
5. `python -m pytest -q`を実行します。
6. 公開後はPagesのHTML、予報取得時刻、Actionsを確認します。

## 注意

- 取得失敗や未知値を欠航と推測しないでください。
- 欠航理由が不明なら`未確認`とし、既知理由を劣化させないでください。
- 気象データの欠測と0を区別してください。
- JMAやアンサンブルの一部障害と、主予報全体の障害を区別してください。
- Brier score、信頼度曲線、時系列外部検証が完了するまで、表示値を「予測確率」と呼ばないでください。
