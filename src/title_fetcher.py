"""
記事URLからページタイトルをスクレイプしてDBに保存する。
"""
import re
import time
import requests
from .db import get_conn

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; seo-rank-watcher/1.0)"}
TIMEOUT = 8


def _extract_title(html: str) -> str | None:
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        # 「| GreenSnap STORE」などサイト名部分を除去
        title = re.sub(r"\s*[\|｜\-–—]\s*GreenSnap.*$", "", title).strip()
        return title or None
    return None


def fetch_titles_for_urls(urls: list[str], sleep_sec: float = 0.3) -> dict[str, str]:
    """URLリストのタイトルを取得して返す。"""
    results = {}
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            title = _extract_title(resp.text)
            if title:
                results[url] = title
            time.sleep(sleep_sec)
        except Exception:
            pass
    return results


def upsert_titles(titles: dict[str, str]):
    if not titles:
        return
    import psycopg2.extras
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO page_titles (page_url, title)
                VALUES %s
                ON CONFLICT (page_url) DO UPDATE SET
                    title = EXCLUDED.title,
                    updated_at = NOW()
                """,
                list(titles.items()),
            )


def get_titles(page_urls: list[str]) -> dict[str, str]:
    """DBからタイトルを取得する。なければURL末尾をスラッグとして返す。"""
    if not page_urls:
        return {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT page_url, title FROM page_titles WHERE page_url = ANY(%s)",
                (page_urls,),
            )
            db_titles = {r[0]: r[1] for r in cur.fetchall()}

    # DBにないURLはスラッグをフォールバックとして使用
    result = {}
    for url in page_urls:
        if url in db_titles:
            result[url] = db_titles[url]
        else:
            slug = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")
            result[url] = slug
    return result
