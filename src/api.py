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

from .db import get_conn, fetch_yoy_pairs, fetch_peak_months, get_ga4_stats, upsert_ga4_snapshots
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

    from .title_fetcher import get_titles
    all_alert_urls = [a.page_url for a in alerts["critical"] + alerts["warning"] + alerts["watch"]]
    top_urls = [a["page_url"] for a in summary.get("top_articles", [])]
    titles = get_titles(list(set(all_alert_urls + top_urls)), site_id)

    def fmt(a):
        default_label = a.page_url.replace(site["url_prefix"], "").strip("/")
        return {
            "page_url": a.page_url,
            "label": titles.get(a.page_url, default_label),
            "cur_position": a.cur_position,
            "prev_position": a.prev_position,
            "position_diff": round(a.position_diff, 1),
            "cur_impressions": a.cur_impressions,
            "impression_change_rate": round(a.impression_change_rate * 100, 1),
            "estimated_pv_loss": a.estimated_pv_loss,
        }

    for a in summary.get("top_articles", []):
        default = a["page_url"].replace(site["url_prefix"], "").strip("/")
        a["label"] = titles.get(a["page_url"], default)

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
def api_trend(site_id: int = 1, weeks: int = 12, top_n: int = 10):
    site = _get_site_or_404(site_id)
    return _top_articles_trend(site_id, site["url_prefix"], top_n=top_n, weeks=weeks)


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


@app.get("/api/recommendations/detail")
def api_rec_detail(site_id: int = 1, page_url: str = "", pattern: str = "ctr_improve"):
    site = _get_site_or_404(site_id)
    if not page_url:
        raise HTTPException(status_code=400, detail="page_url is required")
    from .rewrite_advisor import get_rewrite_advice
    advice = get_rewrite_advice(site["gsc_site_url"], page_url, pattern, site_id)

    # GA4データを付加（テーブル未存在・データなしの場合は None）
    try:
        latest = _latest_snapshot_date(site_id)
        ga4 = get_ga4_stats([page_url], latest or date.today(), site_id) if latest else {}
        advice["ga4"] = ga4.get(page_url)
    except Exception:
        advice["ga4"] = None

    return advice


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


# ── トラフィック ──────────────────────────────────────────────────────────────

@app.get("/api/traffic")
def api_traffic(site_id: int = 1, limit: int = 50):
    """GA4セッション数 Top N 記事と前週比・GSC順位を返す。"""
    site = _get_site_or_404(site_id)

    # ga4_snapshotsテーブル未存在やデータなしを安全に処理
    try:
        _check = None
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM ga4_snapshots LIMIT 1")
                _check = cur.fetchone()
        if _check is None:
            return {"snapshot_date": None, "prev_date": None, "articles": []}
    except Exception:
        return {"snapshot_date": None, "prev_date": None, "articles": []}

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 最新GA4日付
            cur.execute(
                "SELECT MAX(snapshot_date) FROM ga4_snapshots WHERE site_id = %s",
                (site_id,),
            )
            latest_ga4 = cur.fetchone()[0]
            if not latest_ga4:
                return {"snapshot_date": None, "prev_date": None, "articles": []}

            # 約4週前のGA4日付
            cur.execute(
                """
                SELECT snapshot_date FROM ga4_snapshots
                WHERE site_id = %s AND snapshot_date <= %s - INTERVAL '3 weeks'
                  AND page_url LIKE %s
                ORDER BY snapshot_date DESC LIMIT 1
                """,
                (site_id, latest_ga4, f"{site['url_prefix']}%"),
            )
            row = cur.fetchone()
            prev_ga4 = row[0] if row else None

            # 最新GSC日付（GA4日付に最も近いもの）
            cur.execute(
                "SELECT MAX(snapshot_date) FROM rank_snapshots WHERE site_id = %s AND snapshot_date <= %s",
                (site_id, latest_ga4),
            )
            latest_gsc = cur.fetchone()[0]

            # Top N 記事を取得（セッション降順）
            cur.execute(
                """
                SELECT
                    g.page_url,
                    g.sessions          AS cur_sessions,
                    g.pageviews         AS cur_pageviews,
                    g.engagement_rate,
                    g.avg_engagement_time_sec,
                    gp.sessions         AS prev_sessions,
                    r.avg_position      AS cur_position
                FROM ga4_snapshots g
                LEFT JOIN ga4_snapshots gp
                    ON gp.page_url = g.page_url AND gp.site_id = g.site_id
                    AND gp.snapshot_date = %s
                LEFT JOIN rank_snapshots r
                    ON r.page_url = g.page_url AND r.site_id = g.site_id
                    AND r.snapshot_date = %s
                WHERE g.site_id = %s
                  AND g.snapshot_date = %s
                  AND g.page_url LIKE %s
                ORDER BY g.sessions DESC
                LIMIT %s
                """,
                (prev_ga4, latest_gsc, site_id, latest_ga4, f"{site['url_prefix']}%", limit),
            )
            rows = cur.fetchall()

    # タイトル取得
    from .title_fetcher import get_titles
    page_urls = [r[0] for r in rows]
    titles    = get_titles(page_urls, site_id)

    articles = []
    for r in rows:
        page_url, cur_s, cur_pv, eng_rate, avg_time, prev_s, cur_pos = r
        cur_s  = int(cur_s  or 0)
        prev_s = int(prev_s or 0) if prev_s is not None else None

        if prev_s is not None and prev_s > 0:
            change_rate = round((cur_s - prev_s) / prev_s * 100, 1)
        elif prev_s == 0 and cur_s > 0:
            change_rate = 100.0
        else:
            change_rate = None

        articles.append({
            "page_url":               page_url,
            "label":                  titles.get(page_url, page_url.rstrip("/").split("/")[-1]),
            "cur_sessions":           cur_s,
            "prev_sessions":          prev_s,
            "session_change_rate":    change_rate,
            "cur_pageviews":          int(cur_pv or 0),
            "engagement_rate":        round(float(eng_rate or 0) * 100, 1),
            "avg_engagement_time_sec": round(float(avg_time or 0), 1),
            "cur_position":           round(float(cur_pos), 1) if cur_pos else None,
        })

    return {
        "snapshot_date": str(latest_ga4),
        "prev_date":     str(prev_ga4) if prev_ga4 else None,
        "articles":      articles,
    }


# ── GA4 バックフィル ──────────────────────────────────────────────────────────

@app.get("/api/ga4/debug")
def api_ga4_debug(site_id: int = 1):
    """GA4接続の診断エンドポイント。Internal Server Errorの原因調査に使用。"""
    import traceback
    result: dict = {}

    # 1. サイト確認
    site = _get_site_or_404(site_id)
    result["site_name"]       = site["name"]
    result["ga4_property_id"] = site.get("ga4_property_id")

    # 2. テーブル存在確認
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM ga4_snapshots WHERE site_id = %s", (site_id,))
                result["ga4_snapshots_rows"] = cur.fetchone()[0]
        result["table_ok"] = True
    except Exception as e:
        result["table_ok"] = False
        result["table_error"] = str(e)

    # 3. 認証テスト
    try:
        from .ga4_client import _build_credentials
        creds = _build_credentials()
        result["auth_ok"]    = True
        result["token_type"] = type(creds.token).__name__
    except Exception as e:
        result["auth_ok"]    = False
        result["auth_error"] = str(e)
        result["auth_trace"] = traceback.format_exc()
        return result

    # 4. GA4 API テスト呼び出し（直近7日・1件のみ）
    if site.get("ga4_property_id"):
        try:
            import requests as req
            from datetime import timedelta
            end_d   = date.today() - timedelta(days=3)
            start_d = end_d - timedelta(days=6)
            from urllib.parse import urlparse
            parsed   = urlparse(site["url_prefix"])
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            body = {
                "dateRanges": [{"startDate": start_d.isoformat(), "endDate": end_d.isoformat()}],
                "dimensions": [{"name": "pagePath"}],
                "metrics":    [{"name": "sessions"}],
                "limit": 3,
            }
            resp = req.post(
                f"https://analyticsdata.googleapis.com/v1beta/properties/{site['ga4_property_id']}:runReport",
                json=body,
                headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
                timeout=15,
            )
            result["ga4_api_status"] = resp.status_code
            if resp.ok:
                rows = resp.json().get("rows", [])
                result["ga4_api_ok"]      = True
                result["sample_rows"]     = [
                    {"path": r["dimensionValues"][0]["value"],
                     "sessions": r["metricValues"][0]["value"]}
                    for r in rows
                ]
            else:
                result["ga4_api_ok"]    = False
                result["ga4_api_error"] = resp.text
        except Exception as e:
            result["ga4_api_ok"]    = False
            result["ga4_api_error"] = str(e)
    else:
        result["ga4_api_ok"] = False
        result["ga4_api_error"] = "ga4_property_id が未設定"

    return result


@app.get("/api/ga4/backfill")
def api_ga4_backfill(site_id: int = 1, months: int = 3, chunk: int = 4):
    """GA4の過去データを週単位で取り込む。"""
    import time
    from .ga4_client import fetch_page_stats_ga4

    site = _get_site_or_404(site_id)
    if not site.get("ga4_property_id"):
        raise HTTPException(status_code=400, detail="GA4 property ID が未設定です")

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT snapshot_date FROM ga4_snapshots WHERE site_id = %s GROUP BY snapshot_date",
                    (site_id,),
                )
                done_dates = {row[0] for row in cur.fetchall()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DBエラー（テーブル未存在の可能性）: {e}")

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
            rows = fetch_page_stats_ga4(site["ga4_property_id"], site["url_prefix"], monday, sunday)
            if rows:
                upsert_ga4_snapshots(rows, monday, site_id)
                saved.append({"week": str(monday), "rows": len(rows)})
            time.sleep(0.3)
        except Exception as e:
            errors.append({"week": str(monday), "error": str(e)})

    remaining = len(pending) - len(pending[:chunk])
    return {
        "status": "in_progress" if remaining > 0 else "done",
        "saved_this_call": saved,
        "errors": errors,
        "remaining_weeks": remaining,
        "message": f"残り {remaining} 週",
    }

@app.get("/api/ga4/status")
def api_ga4_status(site_id: int = 1):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT snapshot_date), MIN(snapshot_date), MAX(snapshot_date), COUNT(*)
                FROM ga4_snapshots WHERE site_id = %s
                """,
                (site_id,),
            )
            row = cur.fetchone()
    return {
        "saved_weeks":  row[0],
        "oldest_date":  str(row[1]) if row[1] else None,
        "newest_date":  str(row[2]) if row[2] else None,
        "total_rows":   row[3],
    }


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


# ── SEO監査 ──────────────────────────────────────────────────────────────────

def _run_seo_audit(url_prefix: str) -> list[dict]:
    """
    実サイトを取得してSEO問題をチェックし、結果を返す。
    対象: url_prefix から代表記事を1本サンプリングして診断。
    """
    import re, json
    import requests as req

    HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; seo-rank-watcher/1.0)"}
    findings = []

    def add(tab, priority, category, title, detail, fix, passed=False):
        findings.append({
            "tab": tab, "priority": priority, "category": category,
            "title": title, "detail": detail, "fix": fix, "passed": passed,
        })

    # ── トップページ取得 ─────────────────────────────────────────────────────
    try:
        top_resp = req.get(url_prefix, headers=HEADERS, timeout=10)
        top_html = top_resp.text
    except Exception as e:
        return [{"tab": "tech", "priority": "high", "category": "取得エラー",
                 "title": f"ページ取得に失敗しました: {e}", "detail": "", "fix": "", "passed": False}]

    # ── 代表記事URLを抽出 ────────────────────────────────────────────────────
    from urllib.parse import urlparse
    parsed = urlparse(url_prefix)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path_prefix = parsed.path.rstrip("/")
    # href="/columns/xxx" or href="https://domain/columns/xxx" を抽出
    article_hrefs = re.findall(
        rf'href=["\']({re.escape(url_prefix)}[^"\'?#]+)["\']', top_html
    )
    if not article_hrefs:
        article_hrefs = re.findall(
            rf'href=["\']({re.escape(path_prefix)}/[^"\'?#]+)["\']', top_html
        )
    sample_url = article_hrefs[0] if article_hrefs else url_prefix
    if sample_url.startswith("/"):
        sample_url = base + sample_url

    # ── 記事ページ取得 ───────────────────────────────────────────────────────
    try:
        art_resp = req.get(sample_url, headers=HEADERS, timeout=10)
        art_html = art_resp.text
    except Exception:
        art_html = top_html

    # ─────────────────────────────────────────────────────────────────────────
    # Core Web Vitals チェック
    # ─────────────────────────────────────────────────────────────────────────

    # CLS: img に width/height がない
    imgs = re.findall(r'<img[^>]+>', art_html, re.IGNORECASE)
    imgs_no_dim = [i for i in imgs if not (re.search(r'\bwidth\s*=', i) and re.search(r'\bheight\s*=', i))]
    if imgs_no_dim:
        add("cwv", "high", "CLS",
            f"画像にwidth/height属性が未設定（{len(imgs_no_dim)}/{len(imgs)}件）",
            f"記事内の {len(imgs_no_dim)} 枚の画像にwidth・height属性がなく、読み込み時にレイアウトシフト（CLS）が発生します。",
            "全imgタグに width・height 属性を追加、またはCSSで aspect-ratio を指定する")
    else:
        add("cwv", "low", "CLS", "画像のwidth/height属性は設定済み", "", "", passed=True)

    # LCP: preload がない
    has_preload = bool(re.search(r'rel=["\']preload["\']', art_html, re.IGNORECASE))
    if not has_preload:
        add("cwv", "high", "LCP",
            "ファーストビュー画像にpreloadが未設定",
            "アイキャッチ画像に <link rel=\"preload\" as=\"image\"> がなく、LCP（最大コンテンツ描画）が遅延しています。",
            '<link rel="preload" as="image" href="..."> をhead内に追加してアイキャッチ画像を優先読み込みする')
    else:
        add("cwv", "low", "LCP", "preloadは設定済み", "", "", passed=True)

    # Resource: srcset がない
    imgs_no_srcset = [i for i in imgs if not re.search(r'\bsrcset\s*=', i)]
    if len(imgs_no_srcset) > len(imgs) * 0.7:
        add("cwv", "medium", "Resource",
            f"srcsetによるレスポンシブ画像が未対応（{len(imgs_no_srcset)}/{len(imgs)}件）",
            "画像がsrcsetなしの固定URLで配信されており、モバイルでも大きいサイズの画像が読み込まれています。",
            "CDNの画像変換機能を使いsrcset + sizesを設定。WebP形式への変換も合わせて実施する")
    else:
        add("cwv", "low", "Resource", "srcsetは概ね設定済み", "", "", passed=True)

    # ─────────────────────────────────────────────────────────────────────────
    # 技術的SEO チェック
    # ─────────────────────────────────────────────────────────────────────────

    # JSON-LD を全て抽出
    jsonld_blocks = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', art_html, re.DOTALL | re.IGNORECASE)
    schema_types = []
    for block in jsonld_blocks:
        try:
            data = json.loads(block.strip())
            t = data.get("@type", "")
            if isinstance(t, list):
                schema_types.extend(t)
            else:
                schema_types.append(t)
            # @graph 対応
            for g in data.get("@graph", []):
                gt = g.get("@type", "")
                schema_types.extend(gt if isinstance(gt, list) else [gt])
        except Exception:
            pass

    # FAQPage スキーマ
    has_faq_html = bool(re.search(r'よくある質問|FAQ|faq', art_html, re.IGNORECASE))
    has_faq_schema = "FAQPage" in schema_types
    if has_faq_html and not has_faq_schema:
        add("tech", "high", "構造化データ",
            "FAQPageスキーマが未実装（FAQコンテンツは存在）",
            "記事にFAQセクションがあるにもかかわらずFAQPage JSON-LDがありません。リッチリザルトの機会を失っています。",
            "FAQセクションに FAQPage JSON-LD を追加。Googleリッチリザルト掲載でCTR改善が期待できる")
    elif has_faq_schema:
        add("tech", "low", "構造化データ", "FAQPageスキーマは実装済み", "", "", passed=True)

    # BreadcrumbList スキーマ
    has_bc_html = bool(re.search(r'breadcrumb|パンくず|BreadcrumbList', art_html, re.IGNORECASE))
    has_bc_schema = "BreadcrumbList" in schema_types
    if has_bc_html and not has_bc_schema:
        add("tech", "high", "構造化データ",
            "BreadcrumbListスキーマが未実装（パンくずは存在）",
            "パンくずナビゲーションはHTMLで実装済みですが、BreadcrumbList JSON-LDがありません。",
            "パンくずに対応する BreadcrumbList JSON-LD をページに追加する")
    elif has_bc_schema:
        add("tech", "low", "構造化データ", "BreadcrumbListスキーマは実装済み", "", "", passed=True)

    # Article / BlogPosting スキーマ
    has_article_schema = any(t in schema_types for t in ["Article", "BlogPosting", "NewsArticle"])
    if not has_article_schema:
        add("tech", "high", "構造化データ",
            "Article/BlogPostingスキーマが未実装",
            "記事ページにArticle JSON-LDがなく、Googleニュース・AIOへの引用可能性を損失しています。",
            "Article JSON-LDに headline, author, datePublished, dateModified, image を含めて実装する")
    else:
        add("tech", "low", "構造化データ", "Articleスキーマは実装済み", "", "", passed=True)

    # canonical タグ
    has_canonical = bool(re.search(r'rel=["\']canonical["\']', art_html, re.IGNORECASE))
    if not has_canonical:
        add("tech", "medium", "canonicalタグ",
            "canonicalタグが未設定",
            "記事ページにcanonicalタグがなく、URLの重複インデックスが発生するリスクがあります。",
            '<link rel="canonical" href="https://example.com/columns/xxx"> を各記事のheadに追加する')
    else:
        add("tech", "low", "canonicalタグ", "canonicalタグは設定済み", "", "", passed=True)

    # alt属性の品質
    generic_alts = re.findall(r'alt=["\'](?:サムネイル画像|image|img|写真|thumbnail)["\']', art_html, re.IGNORECASE)
    if generic_alts:
        add("tech", "medium", "alt属性",
            f"汎用的なalt属性が {len(generic_alts)} 件",
            f'"{generic_alts[0]}" のような非記述的なalt属性が使われており、画像検索への流入と音声読み上げ対応が不十分です。',
            "各画像の内容を説明する具体的なalt属性に変更する（例: 「さつまいも苗の植え付け方法」）")
    else:
        add("tech", "low", "alt属性", "alt属性は適切に設定されています", "", "", passed=True)

    # ─────────────────────────────────────────────────────────────────────────
    # E-E-A-T チェック
    # ─────────────────────────────────────────────────────────────────────────

    # 著者情報
    has_author_html = bool(re.search(r'著者|監修|執筆|ライター|author|writer', art_html, re.IGNORECASE))
    has_author_schema = any(
        '"author"' in b or "'author'" in b for b in jsonld_blocks
    )
    if not has_author_html and not has_author_schema:
        add("eeat", "high", "Experience/Expertise",
            "著者・監修者情報が記事に未掲載",
            "植物・園芸の専門記事に著者名・プロフィール・監修者情報が一切ありません。専門性の明示がランキングに影響します。",
            "著者プロフィールページを作成し、各記事に著者名・監修者をリンク付きで明示する")
    else:
        add("eeat", "low", "Experience/Expertise", "著者情報は掲載済み", "", "", passed=True)

    # dateModified
    has_date_modified = bool(re.search(r'"dateModified"', art_html))
    if not has_date_modified:
        add("eeat", "medium", "Trustworthiness",
            "dateModifiedが構造化データに未設定",
            "Article JSON-LDのdateModifiedがないためGoogleが記事の鮮度を正しく評価できません。",
            "Article JSON-LDに dateModified を追加し、コンテンツ更新時に必ず更新する")
    else:
        add("eeat", "low", "Trustworthiness", "dateModifiedは設定済み", "", "", passed=True)

    return findings


@app.get("/api/seo/audit")
def api_seo_audit(site_id: int = 1):
    """実サイトをスクレイプしてSEO問題をチェックする。"""
    site = _get_site_or_404(site_id)
    try:
        findings = _run_seo_audit(site["url_prefix"])
        issues   = [f for f in findings if not f.get("passed")]
        passed   = [f for f in findings if f.get("passed")]
        return {
            "site":    site["name"],
            "checked": site["url_prefix"],
            "issues":  issues,
            "passed":  passed,
            "total_issues": len(issues),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── キーワード分析 ────────────────────────────────────────────────────────────

# 植物・園芸系キーワードのフィルタワード
_PLANT_WORDS = {
    "植物","花","草","木","樹","葉","実","根","球根","種","苗","鉢",
    "観葉","多肉","サボテン","バラ","ひまわり","チューリップ","桜","さくら",
    "紫陽花","あじさい","朝顔","あさがお","菊","きく","蘭","らん","胡蝶蘭",
    "ポトス","モンステラ","パキラ","ドラセナ","ユッカ","フィカス","アイビー",
    "ガジュマル","スパティフィラム","サンスベリア","カランコエ","ハイドランジア",
    "ラベンダー","ローズマリー","ミント","バジル","シソ","ハーブ","野菜","果物",
    "トマト","きゅうり","なす","ピーマン","いちご","ブルーベリー","レモン",
    "育て方","栽培","水やり","肥料","剪定","植え替え","種まき","挿し木","増やし方",
    "枯れ","枯らさ","病気","害虫","虫","日当たり","日陰","室内","屋外","ベランダ",
    "花壇","プランター","ガーデン","庭","土","腐葉土","緑","グリーン",
}

def _is_plant_keyword(query: str) -> bool:
    return any(w in query for w in _PLANT_WORDS)


@app.get("/api/keywords/top")
def api_keywords_top(
    site_id: int = 1,
    days: int = 28,
    limit: int = 50,
    filter_plant: bool = True,
):
    """GSCから上位クエリを取得する。filter_plant=true で植物・園芸系に絞り込む。"""
    site = _get_site_or_404(site_id)
    from .gsc_client import fetch_top_queries

    end_date   = date.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=days - 1)

    try:
        # 絞り込み用に多めに取得
        rows = fetch_top_queries(
            site["gsc_site_url"],
            start_date,
            end_date,
            url_prefix=site["url_prefix"],
            row_limit=2000,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if filter_plant:
        rows = [r for r in rows if _is_plant_keyword(r["query"])]

    # 表示回数降順で上位 limit 件
    rows.sort(key=lambda r: r["impressions"], reverse=True)
    return {
        "site":       site["name"],
        "period":     f"{start_date} 〜 {end_date}",
        "total":      len(rows),
        "keywords":   rows[:limit],
    }


# ── 診断 ──────────────────────────────────────────────────────────────────────

@app.get("/api/migrate")
def api_migrate():
    """マイグレーションを手動実行する。"""
    from .db import run_migrations
    try:
        run_migrations()
        return {"status": "ok", "message": "マイグレーション完了"}
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)


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
