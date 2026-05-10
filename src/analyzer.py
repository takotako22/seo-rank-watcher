import os
from dataclasses import dataclass
from datetime import date


RANK_DROP_THRESHOLD = int(os.getenv("ALERT_RANK_DROP_THRESHOLD", "3"))
IMPRESSION_DROP_RATE = float(os.getenv("ALERT_IMPRESSION_DROP_RATE", "0.30"))
TOP_N = int(os.getenv("TOP_N_ARTICLES", "100"))


@dataclass
class AlertItem:
    page_url: str
    cur_position: float
    prev_position: float
    position_diff: float           # 正 = 順位低下（悪化）
    cur_impressions: int
    prev_impressions: int
    impression_change_rate: float  # 負 = 減少
    is_peak_season: bool
    priority: str                  # "critical" | "warning" | "watch"
    estimated_pv_loss: int


def _estimate_pv_loss(cur_impressions: int, prev_impressions: int) -> int:
    if prev_impressions <= 0:
        return 0
    # 表示回数の差をPV損失の近似値として扱う（CTRは固定で2%と仮定）
    avg_ctr = 0.02
    return max(0, int((prev_impressions - cur_impressions) * avg_ctr * 4))  # 月換算(週×4)


def _is_peak_season(page_url: str, current_month: int, peak_months_map: dict) -> bool:
    peak = peak_months_map.get(page_url, [])
    return current_month in peak


def analyze(
    yoy_pairs: list[dict],
    current_date: date,
    peak_months_map: dict,
) -> dict[str, list[AlertItem]]:
    """
    Returns:
        {
            "critical": [...],  # 需要ピーク期 & 大幅下落
            "warning":  [...],  # 需要ピーク期 & 中程度下落
            "watch":    [...],  # 需要期入り前（来月がピーク）で順位が低い
        }
    """
    critical, warning, watch = [], [], []
    current_month = current_date.month
    next_month = (current_month % 12) + 1

    # 前年データがある記事のみ対象、表示回数TopNに絞る
    pairs_with_prev = [p for p in yoy_pairs if p.get("prev_position") is not None]
    pairs_with_prev.sort(key=lambda x: x["cur_impressions"], reverse=True)
    targets = pairs_with_prev[:TOP_N]

    for row in targets:
        cur_pos = float(row["cur_position"] or 0)
        prev_pos = float(row["prev_position"] or 0)
        cur_imp = int(row["cur_impressions"] or 0)
        prev_imp = int(row["prev_impressions"] or 0)

        if cur_pos <= 0 or prev_pos <= 0:
            continue

        pos_diff = cur_pos - prev_pos  # 正 = 悪化
        imp_change = (cur_imp - prev_imp) / prev_imp if prev_imp > 0 else 0.0
        is_peak = _is_peak_season(row["page_url"], current_month, peak_months_map)
        pv_loss = _estimate_pv_loss(cur_imp, prev_imp)

        item = AlertItem(
            page_url=row["page_url"],
            cur_position=cur_pos,
            prev_position=prev_pos,
            position_diff=pos_diff,
            cur_impressions=cur_imp,
            prev_impressions=prev_imp,
            impression_change_rate=imp_change,
            is_peak_season=is_peak,
            priority="",
            estimated_pv_loss=pv_loss,
        )

        if is_peak and (pos_diff >= RANK_DROP_THRESHOLD or imp_change <= -IMPRESSION_DROP_RATE):
            item.priority = "critical"
            critical.append(item)
        elif is_peak and pos_diff >= 1:
            item.priority = "warning"
            warning.append(item)
        else:
            # 来月がピーク & 現在順位が10位以下 → 事前対策を促す
            is_pre_peak = _is_peak_season(row["page_url"], next_month, peak_months_map)
            if is_pre_peak and cur_pos >= 10:
                item.priority = "watch"
                watch.append(item)

    # 各カテゴリをPV損失 or 順位変動でソート
    critical.sort(key=lambda x: (-x.estimated_pv_loss, x.position_diff), reverse=False)
    warning.sort(key=lambda x: x.position_diff, reverse=True)
    watch.sort(key=lambda x: x.cur_position, reverse=True)

    return {"critical": critical, "warning": warning, "watch": watch}


def build_summary(yoy_pairs: list[dict]) -> dict:
    """全体チェック結果のサマリーを生成する。"""
    pairs_with_prev = [p for p in yoy_pairs if p.get("prev_position") is not None]
    if not pairs_with_prev:
        return {}

    total = len(pairs_with_prev)
    improved = sum(1 for p in pairs_with_prev if float(p["cur_position"] or 0) < float(p["prev_position"] or 0))
    declined = sum(1 for p in pairs_with_prev if float(p["cur_position"] or 0) > float(p["prev_position"] or 0))
    unchanged = total - improved - declined

    # 表示回数Top10を順位変動付きで返す
    top_articles = sorted(pairs_with_prev, key=lambda x: x["cur_impressions"], reverse=True)[:10]
    top_list = []
    for p in top_articles:
        cur_pos = float(p["cur_position"] or 0)
        prev_pos = float(p["prev_position"] or 0)
        diff = cur_pos - prev_pos
        top_list.append({
            "page_url": p["page_url"],
            "cur_position": cur_pos,
            "prev_position": prev_pos,
            "position_diff": diff,
            "cur_impressions": int(p["cur_impressions"] or 0),
        })

    return {
        "total": total,
        "improved": improved,
        "declined": declined,
        "unchanged": unchanged,
        "top_articles": top_list,
    }


def infer_peak_months(page_url: str, monthly_impressions: dict[int, int]) -> list[int]:
    """月別表示回数からピーク月を推定する（平均+1σ超を採用）。"""
    if not monthly_impressions:
        return []
    values = list(monthly_impressions.values())
    avg = sum(values) / len(values)
    std = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
    threshold = avg + std * 0.5
    return [m for m, v in monthly_impressions.items() if v >= threshold]
