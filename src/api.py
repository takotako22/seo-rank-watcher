"""
FastAPI ダッシュボードサーバー。
- /          : ダッシュボードHTML
- /api/*     : DBからデータを取得して返すエンドポイント
- /api/run   : 週次バッチを手動トリガー（POST）
"""
import os
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from .db import get_conn, fetch_yoy_pairs, fetch_peak_months
from .analyzer import analyze, build_summary
from .recommender import fetch_recommendations

app = FastAPI(title="SEO Rank Watcher")

SITE_URL   = os.environ["GSC_SITE_URL"]
URL_PREFIX = os.environ.get("TARGET_URL_PREFIX", "https://greensnap.co.jp/columns/")
DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"


# ── Static files & HTML ──────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")


# ── Helper ───────────────────────────────────────────────────────────────────

def _latest_snapshot_date() -> date | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(snapshot_date) FROM rank_snapshots")
            row = cur.fetchone()
            return row[0] if row else None


def _weekly_trend(url_prefix: str, weeks: int = 12) -> list[dict]:
    """週ごとの合計インプレッション・平均順位推移（全記事合算）を返す。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    snapshot_date,
                    SUM(impressions)    AS total_impressions,
                    AVG(avg_position)   AS avg_position,
                    SUM(clicks)         AS total_clicks
                FROM rank_snapshots
                WHERE page_url LIKE %s
                  AND snapshot_date >= CURRENT_DATE - INTERVAL '%s weeks'
                GROUP BY snapshot_date
                ORDER BY snapshot_date
                """,
                (f"{url_prefix}%", weeks),
            )
            return [
                {
                    "date": str(r[0]),
                    "impressions": int(r[1] or 0),
                    "avg_position": round(float(r[2] or 0), 1),
                    "clicks": int(r[3] or 0),
                }
                for r in cur.fetchall()
            ]


def _top_articles_trend(url_prefix: str, top_n: int = 5, weeks: int = 12) -> dict:
    """表示回数Top N 記事の週次インプレッション推移を返す。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Top N 記事を特定
            cur.execute(
                """
                SELECT page_url, SUM(impressions) AS total
                FROM rank_snapshots
                WHERE page_url LIKE %s
                  AND snapshot_date >= CURRENT_DATE - INTERVAL '4 weeks'
                GROUP BY page_url
                ORDER BY total DESC
                LIMIT %s
                """,
                (f"{url_prefix}%", top_n),
            )
            top_urls = [r[0] for r in cur.fetchall()]

            if not top_urls:
                return {"labels": [], "datasets": []}

            # 週次データ取得
            cur.execute(
                """
                SELECT page_url, snapshot_date, impressions
                FROM rank_snapshots
                WHERE page_url = ANY(%s)
                  AND snapshot_date >= CURRENT_DATE - INTERVAL '%s weeks'
                ORDER BY snapshot_date
                """,
                (top_urls, weeks),
            )
            rows = cur.fetchall()

    # ラベル（日付）収集
    dates = sorted(set(str(r[1]) for r in rows))

    datasets = []
    colors = ["#6366f1", "#16a34a", "#dc2626", "#f59e0b", "#06b6d4"]
    for i, url in enumerate(top_urls):
        label = url.replace("https://greensnap.co.jp/columns/", "").strip("/")[:20]
        data_map = {str(r[1]): r[2] for r in rows if r[0] == url}
        datasets.append({
            "label": label,
            "data": [data_map.get(d, 0) for d in dates],
            "borderColor": colors[i % len(colors)],
        })

    return {"labels": dates, "datasets": datasets}


def _season_articles(url_prefix: str, month: int) -> list[dict]:
    """指定月がピーク月の記事と現在の順位を返す。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.page_url, r.avg_position
                FROM article_seasons s
                LEFT JOIN rank_snapshots r
                    ON r.page_url = s.page_url
                    AND r.snapshot_date = (SELECT MAX(snapshot_date) FROM rank_snapshots)
                WHERE %s = ANY(s.peak_months)
                  AND s.page_url LIKE %s
                ORDER BY r.impressions DESC NULLS LAST
                LIMIT 20
                """,
                (month, f"{url_prefix}%"),
            )
            return [
                {
                    "page_url": r[0],
                    "label": r[0].replace("https://greensnap.co.jp/columns/", "").strip("/")[:20],
                    "avg_position": round(float(r[1] or 99), 1),
                }
                for r in cur.fetchall()
            ]


# ── 診断エンドポイント ────────────────────────────────────────────────────────

@app.get("/api/health")
def api_health():
    """DB接続・テーブルのレコード数・最新スナップショット日付を返す。"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM rank_snapshots")
                snapshot_count = cur.fetchone()[0]

                cur.execute("SELECT MAX(snapshot_date) FROM rank_snapshots")
                latest_date = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM article_seasons")
                season_count = cur.fetchone()[0]

        return {
            "db": "ok",
            "rank_snapshots_count": snapshot_count,
            "latest_snapshot_date": str(latest_date) if latest_date else None,
            "article_seasons_count": season_count,
            "site_url": SITE_URL,
            "url_prefix": URL_PREFIX,
        }
    except Exception as e:
        return JSONResponse({"db": "error", "detail": str(e)}, status_code=500)


# ── API エンドポイント ────────────────────────────────────────────────────────

@app.get("/api/summary")
def api_summary():
    latest = _latest_snapshot_date()
    if not latest:
        return JSONResponse({"error": "データがありません"}, status_code=404)

    yoy_pairs = fetch_yoy_pairs(latest, URL_PREFIX)
    page_urls  = [p["page_url"] for p in yoy_pairs]
    peak_map   = fetch_peak_months(page_urls)
    alerts     = analyze(yoy_pairs, latest, peak_map)
    summary    = build_summary(yoy_pairs)

    return {
        "snapshot_date": str(latest),
        "summary": summary,
        "alerts": {
            "critical": [
                {
                    "page_url": a.page_url,
                    "label": a.page_url.replace("https://greensnap.co.jp/columns/", "").strip("/"),
                    "cur_position": a.cur_position,
                    "prev_position": a.prev_position,
                    "position_diff": round(a.position_diff, 1),
                    "cur_impressions": a.cur_impressions,
                    "impression_change_rate": round(a.impression_change_rate * 100, 1),
                    "estimated_pv_loss": a.estimated_pv_loss,
                }
                for a in alerts["critical"]
            ],
            "warning": [
                {
                    "page_url": a.page_url,
                    "label": a.page_url.replace("https://greensnap.co.jp/columns/", "").strip("/"),
                    "cur_position": a.cur_position,
                    "prev_position": a.prev_position,
                    "position_diff": round(a.position_diff, 1),
                    "cur_impressions": a.cur_impressions,
                    "impression_change_rate": round(a.impression_change_rate * 100, 1),
                    "estimated_pv_loss": a.estimated_pv_loss,
                }
                for a in alerts["warning"]
            ],
            "watch": [
                {
                    "page_url": a.page_url,
                    "label": a.page_url.replace("https://greensnap.co.jp/columns/", "").strip("/"),
                    "cur_position": a.cur_position,
                    "prev_position": a.prev_position,
                    "position_diff": round(a.position_diff, 1),
                }
                for a in alerts["watch"]
            ],
        },
    }


@app.get("/api/trend")
def api_trend(weeks: int = 12):
    return _top_articles_trend(URL_PREFIX, weeks=weeks)


@app.get("/api/season")
def api_season(month: int | None = None):
    m = month or date.today().month
    return {"month": m, "articles": _season_articles(URL_PREFIX, m)}


@app.get("/api/recommendations")
def api_recommendations():
    today     = date.today()
    end_date  = today - timedelta(days=3)
    start_date = end_date - timedelta(days=27)  # 4週分
    recs = fetch_recommendations(SITE_URL, URL_PREFIX, start_date, end_date)
    return [
        {
            "main_query": r.main_query,
            "related_queries": r.related_queries,
            "avg_position": round(r.avg_position, 1),
            "total_impressions": r.total_impressions,
            "suggested_title": r.suggested_title,
        }
        for r in recs
    ]


def _trigger_run(background_tasks: BackgroundTasks):
    def _run():
        from .main import run_weekly
        run_weekly()
    background_tasks.add_task(_run)
    return {"status": "started", "message": "週次バッチを開始しました"}

@app.post("/api/run")
def api_run_post(background_tasks: BackgroundTasks):
    return _trigger_run(background_tasks)

@app.get("/api/run")
def api_run_get(background_tasks: BackgroundTasks):
    return _trigger_run(background_tasks)

@app.get("/api/run/debug")
def api_run_debug():
    """バッチを同期実行してエラーを直接返す（デバッグ用）。"""
    import traceback
    import io, sys
    log_buffer = io.StringIO()

    class TeeStream:
        def write(self, msg):
            log_buffer.write(msg)
            sys.__stdout__.write(msg)
        def flush(self):
            sys.__stdout__.flush()

    sys.stdout = TeeStream()
    try:
        from .main import run_weekly
        run_weekly()
        sys.stdout = sys.__stdout__
        return {"status": "ok", "log": log_buffer.getvalue()}
    except Exception as e:
        sys.stdout = sys.__stdout__
        return JSONResponse({
            "status": "error",
            "error": str(e),
            "traceback": traceback.format_exc(),
            "log": log_buffer.getvalue(),
        }, status_code=500)
