"""
記事ごとのリライトアドバイスを生成する。

処理フロー:
  1. GSCからその記事の上位クエリ（直近28日）を取得
  2. 現在タイトル vs 上位クエリのキーワードギャップを分析
  3. タイトル改善案を生成
  4. パターン別のコンテンツ改善ポイントを返す
"""
import re
from datetime import date, timedelta

# ── ストップワード（ギャップ分析から除外） ─────────────────────────────────
STOP_WORDS = {
    "の", "は", "が", "を", "に", "で", "も", "へ", "と", "から", "まで", "より",
    "について", "とは", "など", "ため", "こと", "もの", "よう", "する", "ある",
    "いる", "ない", "なる", "です", "ます", "した", "れる", "られる", "させる",
    "できる", "この", "その", "あの", "どの", "これ", "それ", "あれ", "どれ",
    "方法", "やり方", "仕方", "ポイント", "方", "こちら", "徹底", "解説", "完全",
}

# ── パターン別コンテンツ改善ポイント ──────────────────────────────────────
CONTENT_TIPS: dict[str, list[dict]] = {
    "ctr_improve": [
        {"icon": "✏️", "point": "タイトルに不足キーワードを追加し、クリックされやすい表現に変更する"},
        {"icon": "🔢", "point": "数字・具体性を入れる（例：「5つのコツ」「〇〇分でわかる」）"},
        {"icon": "🎯", "point": "冒頭1文で記事の結論・メリットを30文字以内に明示する"},
        {"icon": "📝", "point": "メタディスクリプションを検索意図に合わせて書き直す"},
    ],
    "near_top": [
        {"icon": "📋", "point": "冒頭30%に最重要情報と結論を集約し、読了率を上げる"},
        {"icon": "❓", "point": "FAQセクションを追加して検索意図の網羅性を高める"},
        {"icon": "🤖", "point": "AIO引用を狙い「〇〇とは」「結論として〜」で始まる明確な段落を追加する"},
        {"icon": "🔗", "point": "関連記事への内部リンクを3〜5本追加して回遊性を高める"},
        {"icon": "📊", "point": "表・リスト・画像を活用してスキャナビリティを改善する"},
    ],
    "trending_up": [
        {"icon": "⬆️", "point": "上位クエリのロングテール語を新しい見出し（H3）として追加する"},
        {"icon": "🔄", "point": "情報の鮮度を更新する（年月の明記、古いデータ・リンクを最新に修正）"},
        {"icon": "🔗", "point": "他記事からこの記事への内部リンクを増やしてクロール頻度を上げる"},
        {"icon": "📐", "point": "見出し構造（H2/H3）を整理し、各クエリに対応したセクションを明確化する"},
    ],
    "pre_season": [
        {"icon": "🗓️", "point": "シーズン特有の作業・注意点をセクションとして追加する"},
        {"icon": "✏️", "point": "タイトルに時期ワード（「〇月」「春」「秋」など）を追加する"},
        {"icon": "📋", "point": "冒頭で「いつやるか」「なぜその時期か」を明確に答える"},
        {"icon": "🔗", "point": "シーズン関連記事（肥料・剪定・害虫対策など）への内部リンクを追加する"},
    ],
}


# ── キーワード抽出 ─────────────────────────────────────────────────────────

def _extract_keywords(text: str) -> set[str]:
    """テキストからキーワードを抽出（助詞・記号を除去）。"""
    text = re.sub(
        r"[のはがをにでもへと、。・　\s｜|\-–—【】「」『』（）()]+", " ",
        text.lower(),
    ).strip()
    words = {w for w in text.split() if len(w) >= 2}
    return words - STOP_WORDS


def _find_missing_keywords(current_title: str, queries: list[dict]) -> list[dict]:
    """
    上位クエリに含まれるがタイトルにないキーワードを返す。
    Returns: [{"keyword": "...", "impressions": 123}, ...]
    """
    title_words = _extract_keywords(current_title)

    kw_impressions: dict[str, int] = {}
    for q in queries:
        for word in _extract_keywords(q["query"]):
            kw_impressions[word] = kw_impressions.get(word, 0) + q["impressions"]

    missing = [
        {"keyword": w, "impressions": imp}
        for w, imp in kw_impressions.items()
        if w not in title_words
    ]
    missing.sort(key=lambda x: x["impressions"], reverse=True)
    return missing[:6]


# ── タイトル改善案生成 ────────────────────────────────────────────────────

def _suggest_new_title(
    current_title: str,
    missing_keywords: list[dict],
    pattern: str,
) -> str:
    """タイトル改善案を生成する。"""
    if not missing_keywords or not current_title:
        return current_title

    # サイト名区切りがある場合は本文部分だけ使う
    title_main = re.split(r"[｜|]", current_title)[0].strip()
    top_kw  = missing_keywords[0]["keyword"] if missing_keywords else ""
    top_kw2 = missing_keywords[1]["keyword"] if len(missing_keywords) >= 2 else ""

    if pattern == "ctr_improve":
        # CTR改善: インパクトのある形容詞・数字を付加
        if any(w in title_main for w in ["育て方", "栽培"]):
            return f"{title_main}｜{top_kw}・{top_kw2}も解説" if top_kw2 else f"{title_main}｜{top_kw}も解説"
        return f"{title_main}【{top_kw}】" if top_kw else title_main

    elif pattern == "near_top":
        # 上位押し上げ: 網羅性・信頼性を強調
        if top_kw2:
            return f"{title_main}｜{top_kw}・{top_kw2}を徹底解説"
        return f"{title_main}｜{top_kw}を徹底解説" if top_kw else title_main

    elif pattern == "pre_season":
        # 来季先行: 時期ワードを前面に
        season_words = ["時期", "シーズン", "月", "春", "夏", "秋", "冬"]
        has_season = any(w in top_kw for w in season_words)
        if has_season:
            return f"{title_main}の{top_kw}と正しい管理方法"
        return f"{title_main}｜{top_kw}の時期を解説" if top_kw else title_main

    else:  # trending_up
        return f"{title_main}｜{top_kw}" if top_kw else title_main


# ── GSC クエリ取得 ────────────────────────────────────────────────────────

def _fetch_page_top_queries(
    site_url: str,
    page_url: str,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """特定ページの上位クエリをGSCから取得する。"""
    from .gsc_client import _build_service

    service = _build_service()
    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query"],
        "dimensionFilterGroups": [{
            "filters": [{
                "dimension": "page",
                "operator": "equals",
                "expression": page_url,
            }]
        }],
        "rowLimit": 20,
    }
    resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
    return [
        {
            "query":       r["keys"][0],
            "impressions": int(r.get("impressions", 0)),
            "position":    round(float(r.get("position", 0)), 1),
            "ctr":         round(float(r.get("ctr", 0)) * 100, 2),
        }
        for r in resp.get("rows", [])
    ]


# ── メイン関数 ────────────────────────────────────────────────────────────

def get_rewrite_advice(
    site_url: str,
    page_url: str,
    pattern: str,
    site_id: int,
) -> dict:
    """
    1記事分のリライトアドバイスを生成して返す。

    Returns:
        current_title    : 現在のタイトル
        suggested_title  : 改善案タイトル
        missing_keywords : タイトルに不足しているキーワードのリスト
        top_queries      : 上位クエリ一覧（GSC直近28日）
        content_tips     : コンテンツ改善チェックリスト
    """
    from .title_fetcher import get_titles

    # タイトル取得（DBキャッシュ or スラッグ）
    titles = get_titles([page_url], site_id)
    current_title = titles.get(page_url, "")

    # GSCから上位クエリ取得（直近28日、エラー時は空リスト）
    end_date   = date.today() - timedelta(days=3)
    start_date = end_date - timedelta(days=27)
    try:
        top_queries = _fetch_page_top_queries(site_url, page_url, start_date, end_date)
    except Exception as e:
        print(f"[rewrite_advisor] GSCクエリ取得エラー: {e}")
        top_queries = []

    # キーワードギャップ分析
    missing_keywords = _find_missing_keywords(current_title, top_queries) if current_title else []
    suggested_title  = _suggest_new_title(current_title, missing_keywords, pattern)

    return {
        "page_url":         page_url,
        "current_title":    current_title,
        "suggested_title":  suggested_title,
        "missing_keywords": missing_keywords,
        "top_queries":      top_queries[:10],
        "content_tips":     CONTENT_TIPS.get(pattern, []),
    }
