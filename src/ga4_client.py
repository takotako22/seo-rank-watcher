"""
Google Analytics 4 Data API クライアント。
ページ別の週次セッション・エンゲージメント指標を取得する。

必要な環境変数:
  GSC_CLIENT_ID, GSC_CLIENT_SECRET, GSC_REFRESH_TOKEN
  (analytics.readonly スコープが含まれたトークンが必要)
"""
import os
from datetime import date
from urllib.parse import urlparse

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


GA4_SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
]


def _build_credentials() -> Credentials:
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GSC_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GSC_CLIENT_ID"],
        client_secret=os.environ["GSC_CLIENT_SECRET"],
        scopes=GA4_SCOPES,
    )
    creds.refresh(Request())
    return creds


def _path_to_url(page_path: str, base_url: str) -> str:
    """GA4の pagePath（/columns/xxx）を完全URLに変換する。"""
    path = page_path if page_path.startswith("/") else "/" + page_path
    return base_url.rstrip("/") + path


def fetch_page_stats_ga4(
    property_id: str,
    url_prefix: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """
    GA4からページ別の基本指標を取得して返す。

    Returns:
        [{"page_url": "...", "sessions": 123, "pageviews": 145,
          "engagement_rate": 0.72, "avg_engagement_time_sec": 95.4}, ...]
    """
    import requests as req

    parsed    = urlparse(url_prefix)
    base_url  = f"{parsed.scheme}://{parsed.netloc}"
    path_prefix = parsed.path  # e.g. "/columns/"

    creds = _build_credentials()
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }

    body = {
        "dateRanges": [
            {"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}
        ],
        "dimensions": [{"name": "pagePath"}],
        "metrics": [
            {"name": "sessions"},
            {"name": "screenPageViews"},
            {"name": "engagementRate"},
            {"name": "userEngagementDuration"},
        ],
        "dimensionFilter": {
            "filter": {
                "fieldName": "pagePath",
                "stringFilter": {
                    "matchType": "BEGINS_WITH",
                    "value": path_prefix,
                },
            }
        },
        "limit": 10000,
        "orderBys": [
            {"metric": {"metricName": "sessions"}, "desc": True}
        ],
    }

    resp = req.post(
        f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
        json=body,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    results = []
    for row in data.get("rows", []):
        dims    = row.get("dimensionValues", [])
        metrics = row.get("metricValues", [])
        if not dims or not metrics:
            continue

        page_path = dims[0]["value"]
        page_url  = _path_to_url(page_path, base_url)

        sessions    = int(float(metrics[0]["value"] or 0))
        pageviews   = int(float(metrics[1]["value"] or 0))
        eng_rate    = float(metrics[2]["value"] or 0)
        eng_dur_sec = float(metrics[3]["value"] or 0)

        # engagementDuration はセッション合計秒数なので平均に変換
        avg_eng_time = eng_dur_sec / sessions if sessions > 0 else 0.0

        results.append({
            "page_url":               page_url,
            "sessions":               sessions,
            "pageviews":              pageviews,
            "engagement_rate":        round(eng_rate, 4),
            "avg_engagement_time_sec": round(avg_eng_time, 2),
        })

    return results
