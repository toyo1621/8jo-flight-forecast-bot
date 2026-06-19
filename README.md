# 羽田発・八丈島行き フライト就航確率

羽田空港から八丈島空港へ向かうANA便について、過去の運航実績と気象情報、現在の気象予報を組み合わせ、就航確率の参考値を表示するプロジェクトです。

**公開サイト:** [八丈島フライト予報](https://toyo1621.github.io/8jo-flight-forecast-bot/)

> [!IMPORTANT]
> 表示する確率は、過去の類似事例に基づく試験的な統計値です。航空会社による実際の運航判断や公式な気象予報を示すものではありません。旅行・安全上の判断には、気象庁等の公式情報とANAの最新運航情報を利用してください。

## なぜ作ったのか

八丈島便は、風や視程などの気象条件によって条件付き運航や欠航になることがあります。公式の運航判断が出る前にも、現在の予報が過去のどのような運航状況に近いのかを把握し、予定を考える際の参考にできる情報を作ることが目的です。

## 主な機能

- ANA1891・ANA1893・ANA1895の7日分の就航確率を表示
- 風向、風速、最大瞬間風速、低層雲量、視程を考慮
- 過去の類似気象条件における運航実績から確率を算出
- GFS・ECMWFアンサンブル予報による予測信頼度A〜Eを表示
- PC・スマートフォン対応のシンプルなWeb UI
- GitHub Pagesで公開し、6時間ごとに自動更新
- Open-Meteo障害時のエラー表示と信頼度の暫定評価

## システム構成

```mermaid
flowchart LR
    O[ODPT API] --> C[運航・気象データ収集]
    M[Open-Meteo] --> C
    C --> S[(SQLite / SQL snapshot)]
    S --> P[就航確率エンジン]
    M --> P
    P --> B[静的サイト生成]
    B --> G[GitHub Pages]
```

GitHub Actionsが次の処理を行います。

1. `data/flights_dump.sql`からSQLiteデータベースを復元
2. Open-Meteoから通常予報とアンサンブル予報を取得
3. 過去の運航実績を使って就航確率と信頼度を計算
4. `build_static.py`でHTMLとCSSを`dist/`へ生成
5. GitHub Pagesへデプロイ

`.github/workflows/pages.yml`は6時間ごとに実行されます。GitHub Actionsのスケジュール実行は混雑状況により遅れる場合があります。

## データソース

### 運航情報

[公共交通オープンデータセンター（ODPT）](https://www.odpt.org/)から、対象便の運航ステータスを取得します。ODPT APIの利用にはAPIキーが必要です。

### 気象情報

[Open-Meteo](https://open-meteo.com/en/docs)から、八丈島空港周辺（緯度33.115、経度139.782）の次のデータを取得します。

- 風向
- 風速
- 最大瞬間風速
- 低層雲量
- 視程
- GFS・ECMWFアンサンブル予報

### 過去データ

過去の運航実績と対応する気象情報をSQLiteの`flight_weather_logs`テーブルへ保存します。バイナリDBはGit管理せず、レビュー可能な`data/flights_dump.sql`をスナップショットとして管理します。

## 就航確率の計算

`forecast_engine.py`は、予報された風向・風速に近い過去レコードを段階的に検索します。

1. 風向差30度以内、風速差3 m/s以内
2. 該当データが5件未満なら、風向差45度以内、風速差5 m/s以内
3. それでも5件未満なら全履歴を使用

運航結果を次の重みで集計します。

| 運航結果 | 重み |
| --- | ---: |
| 通常・遅延 | 1.00 |
| 条件付き運航 | 0.75 |
| 欠航・引き返し・その他 | 0.00 |

さらに視程不良、低層雲、強風・突風の条件で確率を補正します。航空会社都合や機材繰りなど気象以外の要因を考慮し、表示上限は95%です。

島民知見に基づく暫定的な高リスク条件として、南風系（120°〜240°）、平均風速10 m/s以上、最大瞬間風速15 m/s以上が同時に成立するケースを記録しています。これは公式な運航基準ではなく、今後、蓄積データとの相関を検証する仮説です。詳細は[`docs/forecast_spec.md`](docs/forecast_spec.md)を参照してください。

## 予測信頼度A〜E

Open-MeteoのGFS・ECMWFアンサンブル予報を使い、複数の気象シナリオごとに就航確率を再計算します。その中央80%に含まれる確率の幅が狭いほど、予測信頼度を高く表示します。

| 信頼度 | 就航確率の予測幅 | 意味 |
| --- | ---: | --- |
| A | 5ポイント以内 | 高い |
| B | 10ポイント以内 | やや高い |
| C | 20ポイント以内 | 標準 |
| D | 30ポイント以内 | 低め |
| E | 30ポイント超 | 低い |

これはモデル間の一致度を示す指標であり、実際の運航を保証する精度評価ではありません。

## 気象業務法への配慮

本プロジェクトは、気象現象そのものを独自に予報することを目的としていません。第三者が提供する気象予報データを入力とし、過去の運航実績との類似性から就航確率の参考値を統計的に表示する試験的な取り組みです。

気象庁は、数値予報モデルの結果について、加工や表示方法によっては独自の予報と見なされる可能性があると案内しています。本サイトでは、公式予報や航空会社の判断と誤認されないよう、データ出典、算出方法、参考値であることを明示しています。

この説明は法的助言や適法性の保証ではありません。機能、対象範囲、利用目的、商用化方針を変更する際は、必要に応じて気象庁等へ確認します。

- [気象庁「予報業務の許可について」](https://www.jma.go.jp/jma/kishou/minkan/kyoka.html)
- [気象庁「予報業務許可についてよくお寄せいただくご質問」](https://www.jma.go.jp/jma/kishou/minkan/q_a_m.html)

## ローカル環境のセットアップ

Python 3.10以上を推奨します。

```bash
git clone https://github.com/toyo1621/8jo-flight-forecast-bot.git
cd 8jo-flight-forecast-bot
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Webサイトの実行

Flask開発サーバーを起動します。

```bash
flask --app web_app run
```

ブラウザで<http://127.0.0.1:5000/>を開きます。

GitHub Pagesと同じ静的サイトを生成する場合:

```bash
python build_static.py
```

生成結果は`dist/index.html`へ出力されます。

## 運航データの収集

`.env.example`を`.env`へコピーし、ODPT APIキーを設定します。

```ini
ODPT_API_KEY=your_odpt_api_key_here
```

デモモード:

```bash
python data_collector.py --demo
```

通常収集:

```bash
python data_collector.py
```

過去のCSVを取り込む場合:

```bash
python import_user_csv.py --csv path/to/past_flights.csv
```

## テスト

```bash
python -m pytest -q
```

Web表示、信頼度計算、外部API障害時の表示、ヘルスチェックを検証します。

## 主なファイル

| パス | 役割 |
| --- | --- |
| `web_app.py` | Flaskアプリ、気象予報取得、信頼度計算 |
| `forecast_engine.py` | 過去実績に基づく就航確率計算 |
| `build_static.py` | GitHub Pages用の静的HTML生成 |
| `data_collector.py` | 当日の運航・気象情報の収集 |
| `import_user_csv.py` | 過去運航実績の取り込み |
| `db_snapshot.py` | SQLiteとSQLスナップショットの変換 |
| `templates/index.html` | WebページのHTML |
| `static/styles.css` | Webページのスタイル |
| `.github/workflows/pages.yml` | 6時間ごとのPages更新 |
| `.github/workflows/data_collection.yml` | 日次のデータ収集 |

## 現在の制約と今後の予定

- 確率は蓄積済みデータの量と品質に依存します。
- 気象以外の機材繰り、乗員、空港運用などは予測できません。
- Open-MeteoやODPTの仕様変更・障害の影響を受けます。
- 現在はSQLiteを使用しています。将来的には過去の運航・気象データをBigQueryへ移行する計画です。

## ライセンス

[MIT License](LICENSE)

