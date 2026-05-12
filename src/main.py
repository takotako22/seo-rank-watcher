"""
エントリポイント。

Railway の Cron Job として週次実行される。
環境変数 RUN_MODE を変えて手動実行にも対応：
  - RUN_MODE=weekly   (デフォルト) 全アクティブサイトの週次レポートを実行してSlack通知
  - RUN_MODE=seasons  全アクティブサイトのシーズン推定のみ実行（月1回推奨）
  - RUN_MODE=migrate  DBマイグレーションのみ実行
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from .db import (
    run_migrations,
    upsert_snapshots,
    fetch_yoy_pairs,
    fetch_peak_months,
    upsert_ga4_snapshots,
)
from .gsc_client import fetch_last_week_stats
from .analyzer import analyze, build_summary
from .slack_notifier import send
from .season_estimator import estimate_and_save
from .backfill import run_backfill


def run_weekly_for_site(site: dict):
    """1サイト分の週次処理を実行する。"""
    site_id      = site["id"]
    site_name    = site["name"]
    gsc_site_url = site["gsc_site_url"]
    url_prefix   = site["url_prefix"]
    ga4_prop_id  = site.get("ga4_property_id") or ""

    print(f"\n=== [{site_name}] 週次SEO順位レポート開始 ===")

    print("1. GSCからデータ取得中...")
    start_date, end_date, rows = fetch_last_week_stats(gsc_site_url, url_prefix)
    print(f"   取得件数: {len(rows)} 記事 ({start_date} 〜 {end_date})")

    print("2. DBに保存中...")
    upsert_snapshots(rows, start_date, site_id)

    print("3. YoY比較データ取得中...")
    yoy_pairs = fetch_yoy_pairs(start_date, url_prefix, site_id)
    print(f"   YoYペア件数: {len(yoy_pairs)}")

    print("4. ピーク月データ取得中...")
    page_urls      = [p["page_url"] for p in yoy_pairs]
    peak_months_map = fetch_peak_months(page_urls, site_id)
    print(f"   シーズン設定済み記事: {len(peak_months_map)}")

    print("5. 変動分析中...")
    alerts  = analyze(yoy_pairs, start_date, peak_months_map)
    summary = build_summary(yoy_pairs)
    print(
        f"   critical={len(alerts['critical'])}, "
        f"warning={len(alerts['warning'])}, "
        f"watch={len(alerts['watch'])}, "
        f"total={summary.get('total', 0)}"
    )

    print("6. GA4データ取得中...")
    if ga4_prop_id:
        try:
            from .ga4_client import fetch_page_stats_ga4
            ga4_rows = fetch_page_stats_ga4(ga4_prop_id, url_prefix, start_date, end_date)
            upsert_ga4_snapshots(ga4_rows, start_date, site_id)
            print(f"   GA4取得件数: {len(ga4_rows)} 記事")
        except Exception as e:
            print(f"   GA4取得スキップ（エラー）: {e}")
    else:
        print("   GA4_PROPERTY_ID 未設定のためスキップ")

    print("7. Slack通知送信中...")
    send(alerts, start_date, summary, site_name=site_name, url_prefix=url_prefix)
    print(f"=== [{site_name}] 完了 ===")


def run_weekly():
    from .site_manager import get_all_sites
    sites = [s for s in get_all_sites() if s["is_active"]]
    if not sites:
        print("アクティブなサイトが登録されていません")
        return
    print(f"対象サイト: {len(sites)} 件")
    for site in sites:
        try:
            run_weekly_for_site(site)
        except Exception as e:
            print(f"[{site['name']}] エラー: {e}")


def run_seasons():
    from .site_manager import get_all_sites
    sites = [s for s in get_all_sites() if s["is_active"]]
    for site in sites:
        print(f"\n=== [{site['name']}] シーズン推定開始 ===")
        count = estimate_and_save(site["gsc_site_url"], site["url_prefix"], site["id"])
        print(f"シーズン推定完了: {count} 記事")


def run_migrate():
    print("=== DBマイグレーション実行 ===")
    run_migrations()
    print("完了")


if __name__ == "__main__":
    mode = os.getenv("RUN_MODE", "weekly")
    if mode == "weekly":
        run_weekly()
    elif mode == "seasons":
        run_seasons()
    elif mode == "migrate":
        run_migrate()
    elif mode == "backfill":
        # 後方互換: 環境変数があれば使う、なければ全サイト
        gsc_url    = os.environ.get("GSC_SITE_URL", "")
        url_prefix = os.environ.get("TARGET_URL_PREFIX", "")
        months     = int(os.getenv("BACKFILL_MONTHS", "16"))
        if gsc_url and url_prefix:
            print(f"=== 過去データ取り込み開始（{months}ヶ月分） ===")
            result = run_backfill(gsc_url, url_prefix, months_back=months)
            print(f"結果: {result}")
        else:
            print("GSC_SITE_URL / TARGET_URL_PREFIX が未設定のため、APIエンドポイントを使用してください")
    else:
        print(f"不明なRUN_MODE: {mode}", file=sys.stderr)
        sys.exit(1)
