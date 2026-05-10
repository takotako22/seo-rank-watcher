"""
過去データ一括取り込みバッチ。
GSCから取得できる最大16ヶ月分を週単位でDBに保存する。

初回セットアップ時に一度だけ実行する。
"""
import time
from datetime import date, timedelta

from .db import upsert_snapshots
from .gsc_client import fetch_page_stats


def _week_ranges(months_back: int = 16) -> list[tuple[date, date]]:
    """過去N ヶ月分の月曜〜日曜の週一覧を返す（新しい順）。"""
    today = date.today()
    # GSCの反映遅延を考慮して3日前を基準に
    cutoff = today - timedelta(days=3)
    # cutoff以前の直近日曜
    days_since_sunday = (cutoff.weekday() + 1) % 7
    latest_sunday = cutoff - timedelta(days=days_since_sunday)

    # 最大16ヶ月前
    earliest = today - timedelta(days=30 * months_back)

    weeks = []
    sunday = latest_sunday
    while sunday >= earliest:
        monday = sunday - timedelta(days=6)
        weeks.append((monday, sunday))
        sunday -= timedelta(days=7)

    return weeks  # 新しい順


def run_backfill(
    site_url: str,
    url_prefix: str,
    months_back: int = 16,
    sleep_sec: float = 0.5,
) -> dict:
    """
    過去データを週単位で取得してDBに保存する。

    Args:
        sleep_sec: API呼び出し間隔（レート制限対策）

    Returns:
        {"total_weeks": N, "saved_weeks": N, "skipped_weeks": N, "errors": [...]}
    """
    weeks = _week_ranges(months_back)
    total   = len(weeks)
    saved   = 0
    skipped = 0
    errors  = []

    print(f"取り込み対象: {total}週 ({weeks[-1][0]} 〜 {weeks[0][1]})")

    for i, (monday, sunday) in enumerate(weeks, 1):
        try:
            rows = fetch_page_stats(site_url, monday, sunday, url_prefix)
            if rows:
                upsert_snapshots(rows, monday)
                saved += 1
                print(f"[{i:3d}/{total}] ✓ {monday} 〜 {sunday}  {len(rows)}件")
            else:
                skipped += 1
                print(f"[{i:3d}/{total}] - {monday} 〜 {sunday}  0件（スキップ）")
            time.sleep(sleep_sec)
        except Exception as e:
            errors.append({"week": str(monday), "error": str(e)})
            print(f"[{i:3d}/{total}] ✗ {monday} 〜 {sunday}  ERROR: {e}")
            time.sleep(sleep_sec * 2)

    print(f"\n完了: saved={saved}, skipped={skipped}, errors={len(errors)}")
    return {
        "total_weeks": total,
        "saved_weeks": saved,
        "skipped_weeks": skipped,
        "errors": errors,
    }
