import os
from datetime import date

import requests

from .analyzer import AlertItem

WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
MAX_ITEMS_PER_SECTION = 5


def _shorten_url(page_url: str, url_prefix: str) -> str:
    """URLからプレフィックスを除去して短いラベルにする。"""
    short = page_url.replace(url_prefix, "").strip("/")
    return short[:40] if short else page_url


def _format_summary(summary: dict, url_prefix: str = "") -> str:
    if not summary:
        return ""
    lines = [
        f"監視記事数: *{summary['total']}件*　｜　"
        f"順位UP ↑ *{summary['improved']}件*　"
        f"順位DOWN ↓ *{summary['declined']}件*　"
        f"変化なし *{summary['unchanged']}件*（前年同週比）",
        "",
        "*表示回数 Top10（前年同週比）*",
    ]
    for i, a in enumerate(summary.get("top_articles", []), 1):
        diff = a["position_diff"]
        if diff < -0.5:
            icon, diff_str = "↑", f"+{abs(diff):.1f}位"
        elif diff > 0.5:
            icon, diff_str = "↓", f"-{abs(diff):.1f}位"
        else:
            icon, diff_str = "→", "変化なし"
        label = _shorten_url(a["page_url"], url_prefix)
        lines.append(f"{i}. {icon} `{a['cur_position']:.1f}位` {diff_str}　{label}")
    return "\n".join(lines)


def _format_item(item: AlertItem, url_prefix: str = "") -> str:
    direction = "↓" if item.position_diff > 0 else "↑"
    pos_label = (
        f"{item.prev_position:.1f}位 → {item.cur_position:.1f}位 "
        f"({direction}{abs(item.position_diff):.1f}位)"
    )
    imp_label = f"{item.impression_change_rate * 100:+.0f}%（前年同週比）"
    label     = _shorten_url(item.page_url, url_prefix)
    pv_label  = f"推定月間PV損失: 約{item.estimated_pv_loss:,}PV" if item.estimated_pv_loss > 0 else ""
    lines = [
        f"• <{item.page_url}|{label}>",
        f"  順位: {pos_label}　表示回数: {imp_label}",
    ]
    if pv_label:
        lines.append(f"  {pv_label}")
    return "\n".join(lines)


def build_payload(
    alerts: dict[str, list[AlertItem]],
    snapshot_date: date,
    summary: dict | None = None,
    site_name: str = "",
    url_prefix: str = "",
) -> dict:
    critical = alerts.get("critical", [])
    warning  = alerts.get("warning", [])
    watch    = alerts.get("watch", [])

    site_label = f" ― {site_name}" if site_name else ""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🌱 SEO順位ウォッチ週次レポート{site_label}（{snapshot_date.strftime('%Y/%m/%d')}週）",
            },
        }
    ]

    if critical:
        items_text = "\n".join(_format_item(i, url_prefix) for i in critical[:MAX_ITEMS_PER_SECTION])
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🔴 要対策（需要ピーク期・大幅下落） {len(critical)}件*\n{items_text}"},
        })
        if len(critical) > MAX_ITEMS_PER_SECTION:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"他 {len(critical) - MAX_ITEMS_PER_SECTION} 件あり"}],
            })

    if warning:
        items_text = "\n".join(_format_item(i, url_prefix) for i in warning[:MAX_ITEMS_PER_SECTION])
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🟡 要監視（需要期・軽度下落） {len(warning)}件*\n{items_text}"},
        })

    if watch:
        items_text = "\n".join(_format_item(i, url_prefix) for i in watch[:MAX_ITEMS_PER_SECTION])
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🔵 事前対策推奨（来月がピーク期） {len(watch)}件*\n{items_text}"},
        })

    if not critical and not warning and not watch:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "✅ 今週は対策が必要な記事はありませんでした。"},
        })

    if summary:
        summary_text = _format_summary(summary, url_prefix)
        if summary_text:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*📊 全体チェック結果*\n{summary_text}"},
            })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"比較基準: 前年同週 | 対象: {url_prefix or '—'} | 上位100記事"}],
    })

    return {"blocks": blocks}


def send(
    alerts: dict[str, list[AlertItem]],
    snapshot_date: date,
    summary: dict | None = None,
    site_name: str = "",
    url_prefix: str = "",
):
    if not WEBHOOK_URL:
        raise ValueError("SLACK_WEBHOOK_URL が設定されていません")
    payload = build_payload(alerts, snapshot_date, summary, site_name=site_name, url_prefix=url_prefix)
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()
