# seo-rank-watcher

GreenSnap コラム（`/columns/`配下）の検索順位を毎週監視し、前年同週比で大幅に下落した記事をSlackへ通知するバッチです。

## アーキテクチャ

```
GSC API（週次） → PostgreSQL（履歴蓄積） → YoY比較・スコアリング → Slack通知
```

## セットアップ

### 1. 環境変数の設定

`.env.example` をコピーして `.env` に必要な値を設定してください。

```bash
cp .env.example .env
```

| 変数名 | 説明 |
|---|---|
| `GSC_SERVICE_ACCOUNT_JSON` | サービスアカウントのJSONをそのまま文字列で |
| `GSC_SITE_URL` | GSCに登録されているサイトURL |
| `DATABASE_URL` | PostgreSQL接続URL（Railwayが自動設定） |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL |
| `TARGET_URL_PREFIX` | 監視対象のURLプレフィックス |

### 2. GSC サービスアカウントの準備

1. [Google Cloud Console](https://console.cloud.google.com/) でサービスアカウントを作成
2. Search Console API を有効化
3. サービスアカウントのJSONキーをダウンロード
4. GSC管理画面でサービスアカウントのメールアドレスを「閲覧者」として追加

### 3. DBマイグレーション

```bash
RUN_MODE=migrate python -m src.main
```

### 4. シーズン推定（初回・月1回）

```bash
RUN_MODE=seasons python -m src.main
```

### 5. 週次レポート実行

```bash
python -m src.main
```

## Railway へのデプロイ

1. Railway で新しいプロジェクトを作成
2. PostgreSQL アドオンを追加
3. このリポジトリを接続
4. 環境変数を設定
5. Cron Job を設定: `0 0 * * 1`（毎週月曜UTC0時 = 日本時間月曜9時）

## 通知レベル

| レベル | 条件 |
|---|---|
| 🔴 要対策 | 需要ピーク期 かつ 前年同週比 -3位以上 or 表示回数 -30%以上 |
| 🟡 要監視 | 需要ピーク期 かつ 前年同週比 -1位以上 |
| 🔵 事前対策推奨 | 来月がピーク期 かつ 現在10位以下 |
