import os
from contextlib import contextmanager
from datetime import date

import psycopg2
import psycopg2.extras


@contextmanager
def get_conn():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migrations():
    base = os.path.join(os.path.dirname(__file__), "../migrations")
    for filename in sorted(os.listdir(base)):
        if not filename.endswith(".sql"):
            continue
        with open(os.path.join(base, filename)) as f:
            sql = f.read()
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        print(f"migration applied: {filename}")


def upsert_snapshots(rows: list[dict], snapshot_date: date, site_id: int = 1):
    if not rows:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO rank_snapshots
                    (site_id, page_url, snapshot_date, week_of_year, month, clicks, impressions, ctr, avg_position)
                VALUES %s
                ON CONFLICT (site_id, page_url, snapshot_date) DO UPDATE SET
                    week_of_year  = EXCLUDED.week_of_year,
                    month         = EXCLUDED.month,
                    clicks        = EXCLUDED.clicks,
                    impressions   = EXCLUDED.impressions,
                    ctr           = EXCLUDED.ctr,
                    avg_position  = EXCLUDED.avg_position
                """,
                [
                    (
                        site_id,
                        r["page_url"],
                        snapshot_date,
                        snapshot_date.isocalendar()[1],
                        snapshot_date.month,
                        r["clicks"],
                        r["impressions"],
                        r["ctr"],
                        r["avg_position"],
                    )
                    for r in rows
                ],
            )


def fetch_yoy_pairs(snapshot_date: date, url_prefix: str, site_id: int = 1) -> list[dict]:
    """今週と前年同週のデータをページ単位でペアにして返す。"""
    year = snapshot_date.year

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    cur.page_url,
                    cur.avg_position   AS cur_position,
                    cur.impressions    AS cur_impressions,
                    cur.clicks         AS cur_clicks,
                    prev.avg_position  AS prev_position,
                    prev.impressions   AS prev_impressions,
                    prev.clicks        AS prev_clicks,
                    prev.snapshot_date AS prev_date
                FROM rank_snapshots cur
                LEFT JOIN rank_snapshots prev
                    ON prev.page_url      = cur.page_url
                    AND prev.site_id      = cur.site_id
                    AND prev.week_of_year = cur.week_of_year
                    AND EXTRACT(YEAR FROM prev.snapshot_date) = %s
                WHERE cur.snapshot_date = %s
                  AND cur.site_id       = %s
                  AND cur.page_url LIKE %s
                ORDER BY cur.impressions DESC
                """,
                (year - 1, snapshot_date, site_id, f"{url_prefix}%"),
            )
            return [dict(r) for r in cur.fetchall()]


def upsert_article_seasons(seasons: list[dict], site_id: int = 1):
    if not seasons:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO article_seasons (site_id, page_url, peak_months)
                VALUES %s
                ON CONFLICT (site_id, page_url) DO UPDATE SET
                    peak_months = EXCLUDED.peak_months,
                    updated_at  = NOW()
                """,
                [(site_id, s["page_url"], s["peak_months"]) for s in seasons],
            )


def fetch_peak_months(page_urls: list[str], site_id: int = 1) -> dict[str, list[int]]:
    if not page_urls:
        return {}
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT page_url, peak_months FROM article_seasons WHERE site_id = %s AND page_url = ANY(%s)",
                (site_id, page_urls),
            )
            return {r["page_url"]: r["peak_months"] for r in cur.fetchall()}


# ── GA4 ──────────────────────────────────────────────────────────────────────

def upsert_ga4_snapshots(rows: list[dict], snapshot_date: date, site_id: int = 1):
    """GA4の週次スナップショットを保存する。"""
    if not rows:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO ga4_snapshots
                    (site_id, page_url, snapshot_date,
                     sessions, pageviews, engagement_rate, avg_engagement_time_sec)
                VALUES %s
                ON CONFLICT (site_id, page_url, snapshot_date) DO UPDATE SET
                    sessions               = EXCLUDED.sessions,
                    pageviews              = EXCLUDED.pageviews,
                    engagement_rate        = EXCLUDED.engagement_rate,
                    avg_engagement_time_sec = EXCLUDED.avg_engagement_time_sec
                """,
                [
                    (
                        site_id,
                        r["page_url"],
                        snapshot_date,
                        r["sessions"],
                        r["pageviews"],
                        r["engagement_rate"],
                        r["avg_engagement_time_sec"],
                    )
                    for r in rows
                ],
            )


def get_ga4_stats(
    page_urls: list[str],
    snapshot_date: date,
    site_id: int = 1,
) -> dict[str, dict]:
    """
    指定日に最も近いGA4スナップショットをURLリストで取得する。
    テーブル未存在・データなしの場合は {} を返す。
    Returns: {page_url: {sessions, pageviews, engagement_rate, avg_engagement_time_sec}}
    """
    if not page_urls:
        return {}
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # 指定日以前の最新スナップショット日を取得
                cur.execute(
                    """
                    SELECT MAX(snapshot_date) FROM ga4_snapshots
                    WHERE site_id = %s AND snapshot_date <= %s
                    """,
                    (site_id, snapshot_date),
                )
                row = cur.fetchone()
                nearest_date = row["max"] if row else None
                if not nearest_date:
                    return {}

                cur.execute(
                    """
                    SELECT page_url, sessions, pageviews, engagement_rate, avg_engagement_time_sec
                    FROM ga4_snapshots
                    WHERE site_id = %s AND snapshot_date = %s AND page_url = ANY(%s)
                    """,
                    (site_id, nearest_date, page_urls),
                )
                return {
                    r["page_url"]: {
                        "sessions":                int(r["sessions"] or 0),
                        "pageviews":               int(r["pageviews"] or 0),
                        "engagement_rate":         float(r["engagement_rate"] or 0),
                        "avg_engagement_time_sec": float(r["avg_engagement_time_sec"] or 0),
                    }
                    for r in cur.fetchall()
                }
    except Exception as e:
        print(f"[get_ga4_stats] エラー（テーブル未存在の可能性）: {e}")
        return {}
