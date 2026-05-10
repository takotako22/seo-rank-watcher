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
    migration_path = os.path.join(os.path.dirname(__file__), "../migrations/001_init.sql")
    with open(migration_path) as f:
        sql = f.read()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)


def upsert_snapshots(rows: list[dict], snapshot_date: date):
    if not rows:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO rank_snapshots
                    (page_url, snapshot_date, week_of_year, month, clicks, impressions, ctr, avg_position)
                VALUES %s
                ON CONFLICT (page_url, snapshot_date) DO UPDATE SET
                    week_of_year  = EXCLUDED.week_of_year,
                    month         = EXCLUDED.month,
                    clicks        = EXCLUDED.clicks,
                    impressions   = EXCLUDED.impressions,
                    ctr           = EXCLUDED.ctr,
                    avg_position  = EXCLUDED.avg_position
                """,
                [
                    (
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


def fetch_yoy_pairs(snapshot_date: date, url_prefix: str) -> list[dict]:
    """今週と前年同週のデータをページ単位でペアにして返す。"""
    week = snapshot_date.isocalendar()[1]
    month = snapshot_date.month
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
                    ON prev.page_url    = cur.page_url
                    AND prev.week_of_year = cur.week_of_year
                    AND EXTRACT(YEAR FROM prev.snapshot_date) = %s
                WHERE cur.snapshot_date = %s
                  AND cur.page_url LIKE %s
                ORDER BY cur.impressions DESC
                """,
                (year - 1, snapshot_date, f"{url_prefix}%"),
            )
            return [dict(r) for r in cur.fetchall()]


def upsert_article_seasons(seasons: list[dict]):
    if not seasons:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO article_seasons (page_url, peak_months)
                VALUES %s
                ON CONFLICT (page_url) DO UPDATE SET
                    peak_months = EXCLUDED.peak_months,
                    updated_at  = NOW()
                """,
                [(s["page_url"], s["peak_months"]) for s in seasons],
            )


def fetch_peak_months(page_urls: list[str]) -> dict[str, list[int]]:
    if not page_urls:
        return {}
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT page_url, peak_months FROM article_seasons WHERE page_url = ANY(%s)",
                (page_urls,),
            )
            return {r["page_url"]: r["peak_months"] for r in cur.fetchall()}
