"""
GSCのクエリデータから新規記事作成のレコメンドを生成する。

ロジック:
- 表示回数が多い（潜在需要あり）
- 平均順位が低い（15位以下）→ 対応コンテンツが弱い or ない
- 類似クエリをグルーピングしてタイトル候補を提案
"""
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .gsc_client import fetch_page_stats

RECOMMEND_POSITION_THRESHOLD = float(os.getenv("RECOMMEND_POSITION_THRESHOLD", "15"))
RECOMMEND_MIN_IMPRESSIONS = int(os.getenv("RECOMMEND_MIN_IMPRESSIONS", "100"))
RECOMMEND_TOP_N = int(os.getenv("RECOMMEND_TOP_N", "10"))


@dataclass
class ArticleRecommendation:
    main_query: str          # 代表クエリ
    related_queries: list[str]  # 関連クエリ
    avg_position: float
    total_impressions: int
    suggested_title: str


def _normalize(query: str) -> str:
    """クエリを正規化してグルーピングのキーにする。"""
    q = query.lower().strip()
    # 助詞・助動詞・記号を除去
    q = re.sub(r"[のはがをにでもへと、。・　\s]+", " ", q).strip()
    # 単語をソートして順序違いを同一視
    words = sorted(q.split())
    return " ".join(words)


def _suggest_title(query: str) -> str:
    """クエリから記事タイトル候補を生成する。"""
    q = query.strip()

    # 「いつ」「時期」系
    if any(w in q for w in ["いつ", "時期", "季節", "時間"]):
        return f"{q}｜適切なタイミングを解説"
    # 「方法」「やり方」「仕方」系
    if any(w in q for w in ["方法", "やり方", "仕方", "コツ", "ポイント"]):
        return f"{q}｜初心者向けに解説"
    # 「育て方」系
    if "育て方" in q or "栽培" in q:
        plant = re.sub(r"(育て方|栽培方法|栽培)", "", q).strip()
        return f"{plant}の育て方｜初心者でも失敗しないポイント"
    # 「剪定」系
    if "剪定" in q:
        return f"{q}の方法と時期を徹底解説"
    # 「肥料」「水やり」系
    if any(w in q for w in ["肥料", "水やり", "土", "用土"]):
        return f"{q}の正しいやり方を解説"
    # 「原因」「理由」「なぜ」系
    if any(w in q for w in ["原因", "理由", "なぜ", "なに", "どうして"]):
        return f"{q}の原因と対処法"
    # デフォルト
    return f"{q}を徹底解説｜育て方・管理のポイント"


def _group_queries(rows: list[dict]) -> list[dict]:
    """類似クエリをグルーピングする。"""
    groups: dict[str, dict] = {}
    for row in rows:
        key = _normalize(row["query"])
        if key not in groups:
            groups[key] = {
                "queries": [],
                "total_impressions": 0,
                "position_sum": 0,
                "count": 0,
            }
        groups[key]["queries"].append(row["query"])
        groups[key]["total_impressions"] += row["impressions"]
        groups[key]["position_sum"] += row["avg_position"] * row["impressions"]
        groups[key]["count"] += row["impressions"]

    result = []
    for key, g in groups.items():
        # 代表クエリ = 最もインプレッション数が多いクエリ
        main_query = sorted(
            g["queries"],
            key=lambda q: next(r["impressions"] for r in rows if r["query"] == q),
            reverse=True
        )[0]
        related = [q for q in g["queries"] if q != main_query][:3]
        avg_pos = g["position_sum"] / g["count"] if g["count"] > 0 else 0

        result.append({
            "main_query": main_query,
            "related_queries": related,
            "avg_position": avg_pos,
            "total_impressions": g["total_impressions"],
        })
    return result


def fetch_recommendations(
    site_url: str,
    url_prefix: str,
    start_date: date,
    end_date: date,
) -> list[ArticleRecommendation]:
    """記事作成レコメンドを生成して返す。"""
    from .gsc_client import _build_service

    service = _build_service()

    # クエリ単位でデータ取得
    request_body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query"],
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
        "rowLimit": 5000,
    }
    response = (
        service.searchanalytics()
        .query(siteUrl=site_url, body=request_body)
        .execute()
    )
    rows = response.get("rows", [])

    # 高インプレッション × 低順位に絞る
    candidates = []
    for row in rows:
        impressions = int(row.get("impressions", 0))
        avg_position = row.get("position", 0.0)
        if impressions >= RECOMMEND_MIN_IMPRESSIONS and avg_position >= RECOMMEND_POSITION_THRESHOLD:
            candidates.append({
                "query": row["keys"][0],
                "impressions": impressions,
                "avg_position": avg_position,
                "ctr": row.get("ctr", 0.0),
            })

    # インプレッション降順でソート
    candidates.sort(key=lambda x: x["impressions"], reverse=True)

    # グルーピング
    grouped = _group_queries(candidates)
    grouped.sort(key=lambda x: x["total_impressions"], reverse=True)

    # Top Nをレコメンドとして返す
    recommendations = []
    for g in grouped[:RECOMMEND_TOP_N]:
        recommendations.append(
            ArticleRecommendation(
                main_query=g["main_query"],
                related_queries=g["related_queries"],
                avg_position=g["avg_position"],
                total_impressions=g["total_impressions"],
                suggested_title=_suggest_title(g["main_query"]),
            )
        )
    return recommendations
