import os
from datetime import date

import requests

from .analyzer import AlertItem
from .recommender import ArticleRecommendation

WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
MAX_ITEMS_PER_SECTION = 5


def _format_summary(summary: dict) -> str:
    if not summary:
        return ""
    total = summary["total"]
    improved = summary["improved"]
    declined = summary["declined"]
    unchanged = summary["unchanged"]

    lines = [
        f"監視記事数: *{total}件*　｜　"
        f"順位UP ↑ *{improved}件*　順位DOWN ↓ *{declined}件*　変化なし *{unchanged}件*（前年同週比）",
        "",
        "*表示回数 Top10（前年同週比）*",
    ]
    for i, a in enumerate(summary.get("top_articles", []), 1):
        diff = a["position_diff"]
        if diff < -0.5:
            icon = "↑"
            diff_str = f"+{abs(diff):.1f}位"
        elif diff > 0.5:
            icon = "↓"
            diff_str = f"-{abs(diff):.1f}位"
        else:
            icon = "→"
            diff_str = "変化なし"
        url_short = a["page_url"].replace("https://greensnap.co.jp/columns/", "").rstrip("/")[:30]
        lines.append(
            f"{i}. {icon} `{a['cur_position']:.1f}位` {diff_str}　{url_short}"
        )
    return "\n".join(lines)


def _format_item(item: AlertItem) -> str:
    direction = "↓" if item.position_diff > 0 else "↑"
    pos_label = f"{item.prev_position:.1f}位 → {item.cur_position:.1f}位 ({direction}{abs(item.position_diff):.1f}位)"
    imp_pct = item.impression_change_rate * 100
    imp_label = f"{imp_pct:+.0f}%（前年同週比）"
    url_short = item.page_url.replace("https://greensnap.co.jp", "")
    pv_label = f"推定月間PV損失: 約{item.estimated_pv_loss:,}PV" if item.estimated_pv_loss > 0 else ""
    lines = [
        f"• <{item.page_url}|{url_short}>",
        f"  順位: {pos_label}　表示回数: {imp_label}",
    ]
    if pv_label:
        lines.append(f"  {pv_label}")
    return "\n".join(lines)


def _format_recommendations(recs: list[ArticleRecommendation]) -> str:
    lines = []
    for i, r in enumerate(recs, 1):
        related = "・".join(r.related_queries) if r.related_queries else ""
        related_str = f"\n  　関連: {related}" if related else ""
        lines.append(
            f"{i}. 📝 *{r.suggested_title}*\n"
            f"  　検索ワード: `{r.main_query}`　表示回数: {r.total_impressions:,}回　平均順位: {r.avg_position:.1f}位{related_str}"
        )
    return "\n".join(lines)


def build_payload(
    alerts: dict[str, list[AlertItem]],
    snapshot_date: date,
    summary: dict | None = None,
    recommendations: list[ArticleRecommendation] | None = None,
) -> dict:
    critical = alerts.get("critical", [])
    warning = alerts.get("warning", [])
    watch = alerts.get("watch", [])

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🌱 SEO順位ウォッチ週次レポート（{snapshot_date.strftime('%Y/%m/%d')}週）",
            },
        }
    ]

    if critical:
        items_text = "\n".join(_format_item(i) for i in critical[:MAX_ITEMS_PER_SECTION])
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🔴 要対策（需要ピーク期・大幅下落） {len(critical)}件*\n{items_text}",
                },
            }
        )
        if len(critical) > MAX_ITEMS_PER_SECTION:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"他 {len(critical) - MAX_ITEMS_PER_SECTION} 件あり",
                        }
                    ],
                }
            )

    if warning:
        items_text = "\n".join(_format_item(i) for i in warning[:MAX_ITEMS_PER_SECTION])
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🟡 要監視（需要期・軽度下落） {len(warning)}件*\n{items_text}",
                },
            }
        )

    if watch:
        items_text = "\n".join(_format_item(i) for i in watch[:MAX_ITEMS_PER_SECTION])
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🔵 事前対策推奨（来月がピーク期） {len(watch)}件*\n{items_text}",
                },
            }
        )

    if not critical and not warning and not watch:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "✅ 今週は対策が必要な記事はありませんでした。"},
            }
        )

    # 全体サマリー
    if summary:
        summary_text = _format_summary(summary)
        if summary_text:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*📊 全体チェック結果*\n{summary_text}",
                    },
                }
            )

    # 記事作成レコメンド
    if recommendations:
        rec_text = _format_recommendations(recommendations)
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*✍️ 新規記事作成レコメンド（需要あり・コンテンツ弱）*\n{rec_text}",
                },
            }
        )

    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "比較基準: 前年同週 | 対象: greensnap.co.jp/columns/ | 上位100記事",
                }
            ],
        }
    )

    return {"blocks": blocks}


def send(
    alerts: dict[str, list[AlertItem]],
    snapshot_date: date,
    summary: dict | None = None,
    recommendations: list[ArticleRecommendation] | None = None,
):
    if not WEBHOOK_URL:
        raise ValueError("SLACK_WEBHOOK_URL が設定されていません")
    payload = build_payload(alerts, snapshot_date, summary, recommendations)
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()
