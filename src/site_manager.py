"""サイト管理ユーティリティ。"""
import os
from .db import get_conn


def get_all_sites() -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, gsc_site_url, url_prefix, ga4_property_id, is_active
                FROM sites ORDER BY id
            """)
            cols = ["id","name","gsc_site_url","url_prefix","ga4_property_id","is_active"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_site(site_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, gsc_site_url, url_prefix, ga4_property_id, is_active
                FROM sites WHERE id = %s
            """, (site_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = ["id","name","gsc_site_url","url_prefix","ga4_property_id","is_active"]
            return dict(zip(cols, row))


def create_site(name: str, gsc_site_url: str, url_prefix: str, ga4_property_id: str = None) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sites (name, gsc_site_url, url_prefix, ga4_property_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id, name, gsc_site_url, url_prefix, ga4_property_id, is_active
            """, (name, gsc_site_url, url_prefix, ga4_property_id))
            row = cur.fetchone()
            cols = ["id","name","gsc_site_url","url_prefix","ga4_property_id","is_active"]
            return dict(zip(cols, row))


def update_site(site_id: int, **kwargs) -> dict | None:
    allowed = {"name", "gsc_site_url", "url_prefix", "ga4_property_id", "is_active"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_site(site_id)
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE sites SET {set_clause} WHERE id = %s RETURNING id, name, gsc_site_url, url_prefix, ga4_property_id, is_active",
                [*updates.values(), site_id]
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = ["id","name","gsc_site_url","url_prefix","ga4_property_id","is_active"]
            return dict(zip(cols, row))


def seed_default_site():
    """環境変数からデフォルトサイトを登録（未登録の場合のみ）。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sites")
            if cur.fetchone()[0] > 0:
                return  # 既にサイトが登録されている

    gsc_site_url = os.environ.get("GSC_SITE_URL", "")
    url_prefix   = os.environ.get("TARGET_URL_PREFIX", "")
    if gsc_site_url and url_prefix:
        name = os.environ.get("DEFAULT_SITE_NAME", "GreenSnap STORE")
        create_site(name, gsc_site_url, url_prefix)
        print(f"デフォルトサイトを登録しました: {name}")
