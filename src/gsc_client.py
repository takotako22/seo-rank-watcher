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


def fetch_last_week_stats(site_url: str, url_prefix: str) -> tuple[date, date, list[dict]]:
    """直近完了した月曜〜日曜の週データを取得する。"""
    today = date.today()
    # 直近の日曜日
    days_since_sunday = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday)
    last_monday = last_sunday - timedelta(days=6)
    rows = fetch_page_stats(site_url, last_monday, last_sunday, url_prefix)
    return last_monday, last_sunday, rows
