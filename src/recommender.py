"""
既存記事リライト優先のレコメンドロジック。
DBのrank_snapshotsを使って4パターンに分類する。

パターン:
  ctr_improve  : 表示回数多い & CTR低い（タイトルの引きを強化）
  near_top     : 4〜6位（あと一歩で1〜3位・AIO入り）
  trending_up  : 直近4週で順位が改善中（波に乗る）
  pre_season   : 来月〜再来月がピーク & 現在10位以下（先行着手）
"""
import os
from dataclasses import dataclass, field
from datetime import date

# ── しきい値（環境変数で調整可） ───────────────────────────────────────────
CTR_LOW_THRESHOLD    = float(os.getenv("CTR_LOW_THRESHOLD",      "0.02"))   # 2%未満
CTR_MIN_IMPRESSIONS  = int(os.getenv("CTR_MIN_IMPRESSIONS",      "100"))    # 週100表示以上
NEAR_TOP_MIN         = float(os.getenv("NEAR_TOP_POSITION_MIN",  "3.5"))
NEAR_TOP_MAX         = float(os.getenv("NEAR_TOP_POSITION_MAX",  "6.5"))
NEAR_TOP_MIN_IMP     = int(os.getenv("NEAR_TOP_MIN_IMPRESSIONS", "50"))
TREND_UP_MIN_CHANGE  = float(os.getenv("TREND_UP_MIN_CHANGE",    "2.0"))    # 2位以上改善
PRESEASON_MAX_POS    = float(os.getenv("PRESEASON_MAX_POSITION", "10.0"))
TOP_N_EACH           = int(os.getenv("REC_TOP_N_EACH",           "10"))


@dataclass
class RewriteRecommendation:
    page_url: str
    label: str
    avg_position: float
    impressions: int
    ctr: float          # % 表示（例: 1.5 → 1.5%）
    pattern: str        # "ctr_improve" | "near_top" | "trending_up" | "pre_season"
    position_change: float = 0.0   # trending_up: 正の値 = 改善幅
    peak_months: list = field(default_factory=list)


def fetch_rewrite_recommendations(
    site_id: int,
    url_prefix: str,
) -> dict[str, list[RewriteRecommendation]]:
    """4パターンのリライト優先レコメンドを返す。"""
    from .db import get_conn, fetch_peak_months
    from .title_fetcher import get_titles

    with get_conn() as conn:
        with conn.cursor() as cur:
            # ── 最新スナップショット日 ─────────────────────────────────────
            cur.execute(
                "SELECT MAX(snapshot_date) FROM rank_snapshots WHERE site_id = %s",
                (site_id,),
            )
            latest = cur.fetchone()[0]
            if not latest:
                return {"ctr_improve": [], "near_top": [], "trending_up": [], "pre_season": []}

            # ── 最新週データ ───────────────────────────────────────────────
            cur.execute(
                """
                SELECT page_url, avg_position, impressions, ctr
                FROM rank_snapshots
                WHERE site_id = %s AND snapshot_date = %s AND page_url LIKE %s
                ORDER BY impressions DESC
                """,
                (site_id, latest, f"{url_prefix}%"),
            )
            latest_rows: dict[str, dict] = {
                r[0]: {
                    "avg_position": float(r[1] or 0),
                    "impressions": int(r[2] or 0),
                    "ctr": float(r[3] or 0),
                }
                for r in cur.fetchall()
            }

            # ── 4週前スナップショット日 ────────────────────────────────────
            cur.execute(
                """
                SELECT DISTINCT snapshot_date FROM rank_snapshots
                WHERE site_id = %s
                  AND snapshot_date <= %s - INTERVAL '3 weeks'
                  AND page_url LIKE %s
                ORDER BY snapshot_date DESC LIMIT 1
                """,
                (site_id, latest, f"{url_prefix}%"),
            )
            row4 = cur.fetchone()
            prev_date = row4[0] if row4 else None

            prev_positions: dict[str, float] = {}
            if prev_date:
                cur.execute(
                    """
                    SELECT page_url, avg_position FROM rank_snapshots
                    WHERE site_id = %s AND snapshot_date = %s AND page_url LIKE %s
                    """,
                    (site_id, prev_date, f"{url_prefix}%"),
                )
                prev_positions = {r[0]: float(r[1] or 0) for r in cur.fetchall()}

    # ── ピーク月 & タイトル ────────────────────────────────────────────────
    all_urls  = list(latest_rows.keys())
    peak_map  = fetch_peak_months(all_urls, site_id)
    titles    = get_titles(all_urls, site_id)

    today_month  = date.today().month
    next_month   = (today_month % 12) + 1
    next2_month  = (next_month  % 12) + 1

    # ── 各パターンに分類 ───────────────────────────────────────────────────
    ctr_improve: list[RewriteRecommendation] = []
    near_top:    list[RewriteRecommendation] = []
    trending_up: list[RewriteRecommendation] = []
    pre_season:  list[RewriteRecommendation] = []

    for url, d in latest_rows.items():
        pos = d["avg_position"]
        imp = d["impressions"]
        ctr = d["ctr"]
        if pos <= 0:
            continue

        label = titles.get(url, url.rstrip("/").split("/")[-1].replace("-", " "))
        peaks = peak_map.get(url, [])

        # CTR改善: 表示回数多い & CTR低い & 1〜15位
        if imp >= CTR_MIN_IMPRESSIONS and ctr < CTR_LOW_THRESHOLD and 1 <= pos <= 15:
            ctr_improve.append(RewriteRecommendation(
                page_url=url, label=label,
                avg_position=round(pos, 1), impressions=imp,
                ctr=round(ctr * 100, 2), pattern="ctr_improve",
                peak_months=peaks,
            ))

        # 上位押し上げ: 4〜6位 & 一定の表示回数
        if NEAR_TOP_MIN <= pos <= NEAR_TOP_MAX and imp >= NEAR_TOP_MIN_IMP:
            near_top.append(RewriteRecommendation(
                page_url=url, label=label,
                avg_position=round(pos, 1), impressions=imp,
                ctr=round(ctr * 100, 2), pattern="near_top",
                peak_months=peaks,
            ))

        # 上昇トレンド: 4週前比で2位以上改善中
        prev_pos = prev_positions.get(url, 0)
        if prev_pos > 0:
            change = prev_pos - pos   # 正の値 = 順位が上がった（数字が小さくなった）
            if change >= TREND_UP_MIN_CHANGE:
                trending_up.append(RewriteRecommendation(
                    page_url=url, label=label,
                    avg_position=round(pos, 1), impressions=imp,
                    ctr=round(ctr * 100, 2), pattern="trending_up",
                    position_change=round(change, 1),
                    peak_months=peaks,
                ))

        # 来季先行対策: 来月 or 再来月がピーク & 現在10位以下
        if (next_month in peaks or next2_month in peaks) and pos >= PRESEASON_MAX_POS:
            pre_season.append(RewriteRecommendation(
                page_url=url, label=label,
                avg_position=round(pos, 1), impressions=imp,
                ctr=round(ctr * 100, 2), pattern="pre_season",
                peak_months=peaks,
            ))

    # ── ソート ─────────────────────────────────────────────────────────────
    ctr_improve.sort(key=lambda x: x.impressions, reverse=True)
    near_top.sort(key=lambda x: x.impressions, reverse=True)
    trending_up.sort(key=lambda x: x.position_change, reverse=True)
    pre_season.sort(key=lambda x: x.avg_position)

    return {
        "ctr_improve": ctr_improve[:TOP_N_EACH],
        "near_top":    near_top[:TOP_N_EACH],
        "trending_up": trending_up[:TOP_N_EACH],
        "pre_season":  pre_season[:TOP_N_EACH],
    }
