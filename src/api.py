"""
FastAPI ダッシュボードサーバー。
- /          : ダッシュボードHTML
- /api/sites : サイト管理
- /api/*     : DBからデータを取得して返すエンドポイント（?site_id=1）
"""
import os
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

load_dotenv()

from .db import get_conn, fetch_yoy_pairs, fetch_peak_months
from .analyzer import analyze, build_summary

app = FastAPI(title="SEO Rank Watcher")
DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"


@app.on_event("startup")
def on_startup():
    from .db import run_migrations
    from .site_manager import seed_default_site
    try:
        run_migrations()
        seed_default_site()
        print("startup: ok")
    except Exception as e:
        print(f"startup error: {e}")


# ── Static ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")


# ── Helper ───────────────────────────────────────────────────────────────────

def _get_site_or_404(site_id: int) -> dict:
    from .site_manager import get_site
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"site_id={site_id} が見つかりません")
    return site


def _latest_snapshot_date(site_id: int) -> date | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(snapshot_date) FROM rank_snapshots WHERE site_id = %s", (site_id,))
            row = cur.fetchone()
            return row[0] if row else None


def _top_articles_trend(site_id: int, url_prefix: str, top_n: int = 5, weeks: int = 12) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT page_url, SUM(impressions) AS total
                FROM rank_snapshots
                WHERE site_id = %s AND page_url LIKE %s
                  AND snapshot_date >= CURRENT_DATE - INTERVAL '4 weeks'
                GROUP BY page_url ORDER BY total DESC LIMIT %s
            """, (site_id, f"{url_prefix}%", top_n))
            top_urls = [r[0] for r in cur.fetchall()]
            if not top_urls:
                return {"labels": [], "datasets": []}
            cur.execute("""
                SELECT page_url, snapshot_date, impressions
                FROM rank_snapshots
                WHERE site_id = %s AND page_url = ANY(%s)
                  AND snapshot_date >= CURRENT_DATE - INTERVAL '%s weeks'
                ORDER BY snapshot_date
            """, (site_id, top_urls, weeks))
            rows = cur.fetchall()
    dates = sorted(set(str(r[1]) for r in rows))
    colors = ["#6366f1","#16a34a","#dc2626","#f59e0b","#06b6d4"]
    datasets = []
    for i, url in enumerate(top_urls):
        label = url.replace(url_prefix, "").strip("/")[:25]
        data_map = {str(r[1]): r[2] for r in rows if r[0] == url}
        datasets.append({"label": label, "data": [data_map.get(d, 0) for d in dates], "borderColor": colors[i % len(colors)]})
    return {"labels": dates, "datasets": datasets}


def _season_articles(site_id: int, url_prefix: str, month: int) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.page_url, r.avg_position, r.impressions
                FROM article_seasons s
                LEFT JOIN rank_snapshots r
                    ON r.page_url = s.page_url AND r.site_id = s.site_id
                    AND r.snapshot_date = (SELECT MAX(snapshot_date) FROM rank_snapshots WHERE site_id = %s)
                WHERE s.site_id = %s AND %s = ANY(s.peak_months) AND s.page_url LIKE %s
                ORDER BY r.impressions DESC NULLS LAST LIMIT 20
            """, (site_id, site_id, month, f"{url_prefix}%"))
            return [{"page_url": r[0], "label": r[0].rstrip("/").split("/")[-1], "avg_position": round(float(r[1] or 99), 1)} for r in cur.fetchall()]


# ── サイト管理 API ────────────────────────────────────────────────────────────

class SiteCreate(BaseModel):
    name: str
    gsc_site_url: str
    url_prefix: str
    ga4_property_id: str | None = None

class SiteUpdate(BaseModel):
    name: str | None = None
    gsc_site_url: str | None = None
    url_prefix: str | None = None
    ga4_property_id: str | None = None
    is_active: bool | None = None

@app.get("/api/sites")
def api_sites():
    from .site_manager import get_all_sites
    return get_all_sites()

@app.post("/api/sites")
def api_site_create(body: SiteCreate):
    from .site_manager import create_site
    return create_site(body.name, body.gsc_site_url, body.url_prefix, body.ga4_property_id)

@app.patch("/api/sites/{site_id}")
def api_site_update(site_id: int, body: SiteUpdate):
    from .site_manager import update_site
    site = update_site(site_id, **{k: v for k, v in body.dict().items() if v is not None})
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site

@app.delete("/api/sites/{site_id}")
def api_site_delete(site_id: int):
    from .site_manager import update_site
    update_site(site_id, is_active=False)
    return {"status": "ok"}


# ── データ API ────────────────────────────────────────────────────────────────

@app.get("/api/summary")
def api_summary(site_id: int = 1):
    site = _get_site_or_404(site_id)
    latest = _latest_snapshot_date(site_id)
    if not latest:
        return JSONResponse({"error": "データがありません"}, status_code=404)

    yoy_pairs = fetch_yoy_pairs(latest, site["url_prefix"], site_id)
    page_urls  = [p["page_url"] for p in yoy_pairs]
    peak_map   = fetch_peak_months(page_urls, site_id)
    alerts     = analyze(yoy_pairs, latest, peak_map)
    summary    = build_summary(yoy_pairs)

    def fmt(a):
        return {
            "page_url": a.page_url,
            "label": a.page_url.replace(site["url_prefix"], "").strip("/"),
            "cur_position": a.cur_position,
            "prev_position": a.prev_position,
            "position_diff": round(a.position_diff, 1),
            "cur_impressions": a.cur_impressions,
            "impression_change_rate": round(a.impression_change_rate * 100, 1),
            "estimated_pv_loss": a.estimated_pv_loss,
        }

    return {
        "snapshot_date": str(latest),
        "site": site,
        "summary": summary,
        "alerts": {
            "critical": [fmt(a) for a in alerts["critical"]],
            "warning":  [fmt(a) for a in alerts["warning"]],
            "watch":    [fmt(a) for a in alerts["watch"]],
        },
    }


@app.get("/api/trend")
def api_trend(site_id: int = 1, weeks: int = 12):
    site = _get_site_or_404(site_id)
    return _top_articles_trend(site_id, site["url_prefix"], weeks=weeks)


@app.get("/api/season")
def api_season(site_id: int = 1, month: int | None = None):
    site = _get_site_or_404(site_id)
    m = month or date.today().month
    articles = _season_articles(site_id, site["url_prefix"], m)
    from .title_fetcher import get_titles
    urls = [a["page_url"] for a in articles]
    titles = get_titles(urls, site_id)
    for a in articles:
        a["label"] = titles.get(a["page_url"], a["label"])
    return {"month": m, "articles": articles}


@app.get("/api/recommendations")
def api_recommendations(site_id: int = 1):
    site = _get_site_or_404(site_id)
    from .recommender import fetch_rewrite_recommendations
    result = fetch_rewrite_recommendations(site_id, site["url_prefix"])

    def fmt(r):
        return {
            "page_url":        r.page_url,
            "label":           r.label,
            "avg_position":    r.avg_position,
            "impressions":     r.impressions,
            "ctr":             r.ctr,
            "pattern":         r.pattern,
            "position_change": r.position_change,
            "peak_months":     r.peak_months,
        }

    return {
        "ctr_improve": [fmt(r) for r in result["ctr_improve"]],
        "near_top":    [fmt(r) for r in result["near_top"]],
        "trending_up": [fmt(r) for r in result["trending_up"]],
        "pre_season":  [fmt(r) for r in result["pre_season"]],
    }


# ── タイトル取得 ──────────────────────────────────────────────────────────────

@app.get("/api/titles/fetch")
def api_titles_fetch(background_tasks: BackgroundTasks, site_id: int = 1, limit: int = 100):
    def _run():
        from .title_fetcher import fetch_titles_for_urls, upsert_titles
        with get_conn() as conn:
            with conn.cursor() as cur:
                site = _get_site_or_404(site_id)
                cur.execute("""
                    SELECT DISTINCT r.page_url FROM rank_snapshots r
                    LEFT JOIN page_titles t ON t.page_url = r.page_url AND t.site_id = r.site_id
                    WHERE r.site_id = %s AND t.page_url IS NULL AND r.page_url LIKE %s
                    LIMIT %s
                """, (site_id, f"{site['url_prefix']}%", limit))
                urls = [row[0] for row in cur.fetchall()]
        if urls:
            titles = fetch_titles_for_urls(urls)
            upsert_titles(titles, site_id)
    background_tasks.add_task(_run)
    return {"status": "started", "message": f"最大{limit}件のタイトル取得を開始しました"}

@app.get("/api/titles/status")
def api_titles_status(site_id: int = 1):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM page_titles WHERE site_id = %s", (site_id,))
            titles_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT page_url) FROM rank_snapshots WHERE site_id = %s", (site_id,))
            total_urls = cur.fetchone()[0]
    return {"fetched": titles_count, "total_urls": total_urls, "remaining": total_urls - titles_count}


# ── バックフィル ──────────────────────────────────────────────────────────────

@app.get("/api/backfill")
def api_backfill(site_id: int = 1, months: int = 16, chunk: int = 5):
    import time
    from .gsc_client import fetch_page_stats
    from .db import upsert_snapshots

    site = _get_site_or_404(site_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT snapshot_date FROM rank_snapshots WHERE site_id = %s GROUP BY snapshot_date", (site_id,))
            done_dates = {row[0] for row in cur.fetchall()}

    today   = date.today()
    cutoff  = today - timedelta(days=3)
    days_since_sunday = (cutoff.weekday() + 1) % 7
    latest_sunday = cutoff - timedelta(days=days_since_sunday)
    earliest = today - timedelta(days=30 * months)

    pending = []
    sunday = latest_sunday
    while sunday >= earliest:
        monday = sunday - timedelta(days=6)
        if monday not in done_dates:
            pending.append((monday, sunday))
        sunday -= timedelta(days=7)

    if not pending:
        return {"status": "done", "message": "すべての週の取り込みが完了しています", "remaining": 0}

    saved, errors = [], []
    for monday, sunday in pending[:chunk]:
        try:
            rows = fetch_page_stats(site["gsc_site_url"], monday, sunday, site["url_prefix"])
            if rows:
                upsert_snapshots(rows, monday, site_id)
                saved.append({"week": str(monday), "rows": len(rows)})
            time.sleep(0.3)
        except Exception as e:
            errors.append({"week": str(monday), "error": str(e)})

    return {"status": "in_progress", "saved_this_call": saved, "errors": errors, "remaining_weeks": len(pending) - len(pending[:chunk]), "message": f"残り {len(pending) - len(pending[:chunk])} 週"}

@app.get("/api/backfill/status")
def api_backfill_status(site_id: int = 1):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT snapshot_date), MIN(snapshot_date), MAX(snapshot_date), COUNT(*)
                FROM rank_snapshots WHERE site_id = %s
            """, (site_id,))
            row = cur.fetchone()
    return {"saved_weeks": row[0], "oldest_date": str(row[1]) if row[1] else None, "newest_date": str(row[2]) if row[2] else None, "total_rows": row[3]}


# ── 手動実行 ──────────────────────────────────────────────────────────────────

@app.post("/api/run")
@app.get("/api/run")
def api_run(background_tasks: BackgroundTasks):
    def _run():
        from .main import run_weekly
        run_weekly()
    background_tasks.add_task(_run)
    return {"status": "started", "message": "週次バッチを開始しました"}

@app.get("/api/run/debug")
def api_run_debug():
    import traceback, io, sys
    log_buffer = io.StringIO()
    class Tee:
        def write(self, m): log_buffer.write(m); sys.__stdout__.write(m)
        def flush(self): sys.__stdout__.flush()
    sys.stdout = Tee()
    try:
        from .main import run_weekly
        run_weekly()
        sys.stdout = sys.__stdout__
        return {"status": "ok", "log": log_buffer.getvalue()}
    except Exception as e:
        sys.stdout = sys.__stdout__
        return JSONResponse({"status": "error", "error": str(e), "traceback": traceback.format_exc(), "log": log_buffer.getvalue()}, status_code=500)


# ── 診断 ──────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def api_health():
    try:
        from .site_manager import get_all_sites
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT site_id, COUNT(*) FROM rank_snapshots GROUP BY site_id")
                snapshots = {r[0]: r[1] for r in cur.fetchall()}
                cur.execute("SELECT COUNT(*) FROM article_seasons")
                season_count = cur.fetchone()[0]
        return {"db": "ok", "sites": get_all_sites(), "snapshots_per_site": snapshots, "article_seasons_count": season_count}
    except Exception as e:
        return JSONResponse({"db": "error", "detail": str(e)}, status_code=500)

@app.get("/api/gsc/debug")
def api_gsc_debug(site_id: int = 1):
    import traceback
    from .gsc_client import _build_service
    site = _get_site_or_404(site_id)
    result = {}
    try:
        service = _build_service()
        sites_resp = service.sites().list().execute()
        result["available_properties"] = [s["siteUrl"] for s in sites_resp.get("siteEntry", [])]
        end_date   = date.today() - timedelta(days=3)
        start_date = end_date - timedelta(days=6)
        resp = service.searchanalytics().query(siteUrl=site["gsc_site_url"], body={"startDate": start_date.isoformat(), "endDate": end_date.isoformat(), "dimensions": ["page"], "rowLimit": 5}).execute()
        result["sample_rows"] = resp.get("rows", [])
        result["date_range"]  = f"{start_date} 〜 {end_date}"
        result["site_url_used"] = site["gsc_site_url"]
    except Exception as e:
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
    return result
