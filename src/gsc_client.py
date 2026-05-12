import os
from datetime import date, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _build_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GSC_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GSC_CLIENT_ID"],
        client_secret=os.environ["GSC_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("searchconsole", "v1", credentials=creds)


def fetch_page_stats(
    site_url: str,
    start_date: date,
    end_date: date,
    url_prefix: str,
    row_limit: int = 5000,
) -> list[dict]:
    """GSCからページ単位の検索統計を取得する。"""
    service = _build_service()
    request_body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["page"],
        "dimensionFilterGroups": [
            {
                "filters": [
                    {
                        "dimension": "page",
                        "operator": "contains",
                        "expression": url_prefix,
                    }
                ]
            }
        ],
        "rowLimit": row_limit,
    }
    response = (
        service.searchanalytics()
        .query(siteUrl=site_url, body=request_body)
        .execute()
    )
    rows = response.get("rows", [])
    results = []
    for row in rows:
        results.append(
            {
                "page_url": row["keys"][0],
                "clicks": int(row.get("clicks", 0)),
                "impressions": int(row.get("impressions", 0)),
                "ctr": row.get("ctr", 0.0),
                "avg_position": row.get("position", 0.0),
            }
        )
    return results


def fetch_top_queries(
    site_url: str,
    start_date: date,
    end_date: date,
    url_prefix: str = "",
    row_limit: int = 1000,
) -> list[dict]:
    """GSCからクエリ単位の検索統計を取得する。"""
    service = _build_service()
    body: dict = {
        "startDate": start_date.isoformat(),
        "endDate":   end_date.isoformat(),
        "dimensions": ["query"],
        "rowLimit":   row_limit,
    }
    if url_prefix:
        body["dimensionFilterGroups"] = [{
            "filters": [{
                "dimension": "page",
                "operator": "contains",
                "expression": url_prefix,
            }]
        }]
    resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    return [
        {
            "query":       r["keys"][0],
            "impressions": int(r.get("impressions", 0)),
            "clicks":      int(r.get("clicks", 0)),
            "ctr":         round(float(r.get("ctr", 0)) * 100, 2),
            "position":    round(float(r.get("position", 0)), 1),
        }
        for r in resp.get("rows", [])
    ]


def fetch_last_week_stats(site_url: str, url_prefix: str) -> tuple[date, date, list[dict]]:
    """直近完了かつGSCに反映済みの週データを取得する。

    GSCには2〜3日の反映遅延があるため、3日前を基準日として
    その時点で完了している最新の月〜日曜週を取得する。
    """
    today = date.today()
    # 3日前を基準にしてGSC反映済みの週を確実に取得
    cutoff = today - timedelta(days=3)
    days_since_sunday = (cutoff.weekday() + 1) % 7
    last_sunday = cutoff - timedelta(days=days_since_sunday)
    last_monday = last_sunday - timedelta(days=6)
    rows = fetch_page_stats(site_url, last_monday, last_sunday, url_prefix)
    return last_monday, last_sunday, rows
