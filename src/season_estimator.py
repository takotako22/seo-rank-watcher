"""前年GSCデータから記事ごとの需要ピーク月を自動推定してDBに書き込む。"""
import os
from datetime import date, timedelta

from .analyzer import infer_peak_months
from .db import fetch_peak_months, upsert_article_seasons
from .gsc_client import fetch_page_stats


def estimate_and_save(site_url: str, url_prefix: str, site_id: int = 1):
    """1年分の月次データを取得してピーク月を推定する。初回 or 月1回程度実行する。"""
    today = date.today()
    end_date = today - timedelta(days=3)  # GSCの反映遅延を考慮
    start_date = end_date - timedelta(days=365)

    monthly_impressions: dict[str, dict[int, int]] = {}

    # 月ごとに集計
    cursor = start_date.replace(day=1)
    while cursor <= end_date:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = min(next_month - timedelta(days=1), end_date)
        rows = fetch_page_stats(site_url, cursor, month_end, url_prefix)
        for row in rows:
            url = row["page_url"]
            if url not in monthly_impressions:
                monthly_impressions[url] = {}
            monthly_impressions[url][cursor.month] = (
                monthly_impressions[url].get(cursor.month, 0) + row["impressions"]
            )
        cursor = next_month

    seasons = [
        {"page_url": url, "peak_months": infer_peak_months(url, months)}
        for url, months in monthly_impressions.items()
        if infer_peak_months(url, months)
    ]
    upsert_article_seasons(seasons, site_id)
    return len(seasons)
