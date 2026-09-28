#!/usr/bin/env /usr/bin/python3
"""
replay_extra_l.py  過去 7 日分の extra_l.xml を時系列順に処理して
                   新フィルタ（Status チェック + クールダウン）を適用した場合の
                   投稿件数を集計するスクリプト（要件5）。

鮮度フィルタは無効（REPLAY_MODE）にし、全エントリを時系列順に処理する。
クールダウンはメモリ内でシミュレート（ファイルに書かない）。

使い方:
  /usr/bin/python3 tests/replay_extra_l.py

出力:
  - 日ごと・種別ごとの投稿件数
  - 見送り理由の内訳
  - 上限設定（1日10件/1回3件）の妥当性評価
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import aomori_bot as bot
import safety

UA        = "aomori-disaster-bot/1.0 (replay-test)"
ATOM_NS   = "http://www.w3.org/2005/Atom"
JST       = timezone(timedelta(hours=9))
COOLDOWN_HOURS = 3   # クールダウン既定値

def local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag

def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

# ── フィード取得 ───────────────────────────────────────────
print("Fetching extra_l.xml ...")
feed_data = fetch("https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml")
root_feed = ET.fromstring(feed_data)

entries = []
for entry in root_feed.findall(f"{{{ATOM_NS}}}entry"):
    link    = entry.find(f"{{{ATOM_NS}}}link")
    href    = link.get("href") if link is not None else ""
    updated = entry.findtext(f"{{{ATOM_NS}}}updated") or ""
    # 青森県対象コード (020000) の VPWW53 のみ対象
    if "VPWW53_020000" not in href:
        continue
    entries.append({"href": href, "updated": updated})

# 古い順（昇順）に並べ替え
entries.sort(key=lambda e: e["updated"])
print(f"  VPWW53_020000 エントリ: {len(entries)} 件\n")

# ── 処理ループ ─────────────────────────────────────────────
cooldown: dict = {}          # {headline_key: iso_datetime_str}
seen_ids: set  = set()

stats = defaultdict(lambda: defaultdict(int))
# stats[date_jst]["post" | "skip_status" | "skip_cooldown" | "skip_other"]

for e in entries:
    eid = e["href"]
    if eid in seen_ids:
        continue

    # JST 日付（エントリ更新時刻から算出）
    try:
        dt_utc   = datetime.fromisoformat(e["updated"].replace("Z", "+00:00"))
        dt_jst   = dt_utc.astimezone(JST)
        date_key = dt_jst.strftime("%Y-%m-%d")
    except Exception:
        date_key = "unknown"

    try:
        xml_data = fetch(e["href"])
    except Exception as ex:
        print(f"  [SKIP] 取得失敗: {e['href'].rsplit('/',1)[-1]} ({ex})")
        seen_ids.add(eid)
        stats[date_key]["skip_other"] += 1
        continue

    post_text, _, _, headline_key = bot._parse_weather_xml_full(xml_data, "VPWW53")

    if post_text is None:
        # Status が全て継続、または青森県外、または注意報解除
        seen_ids.add(eid)
        stats[date_key]["skip_status"] += 1
        continue

    # クールダウン確認
    if safety.is_in_cooldown(headline_key, cooldown, COOLDOWN_HOURS):
        seen_ids.add(eid)
        stats[date_key]["skip_cooldown"] += 1
        continue

    # 投稿する
    seen_ids.add(eid)
    safety.update_warn_cooldown(headline_key, cooldown)
    stats[date_key]["post"] += 1

    print(f"  [POST] {dt_jst.strftime('%m/%d %H:%M')} {post_text[:60]!r}")

# ── 集計レポート ──────────────────────────────────────────
print("\n" + "=" * 64)
print("集計結果（VPWW53_020000 対象、鮮度フィルタなし、新ロジック適用）")
print("=" * 64)
print(f"{'日付':12s} {'投稿':>4s} {'継続見送':>8s} {'CD見送':>8s} {'その他':>6s}")
print("-" * 64)
total_post = total_skip_s = total_skip_c = total_skip_o = 0
for d in sorted(stats.keys()):
    p = stats[d]["post"]
    s = stats[d]["skip_status"]
    c = stats[d]["skip_cooldown"]
    o = stats[d]["skip_other"]
    total_post    += p
    total_skip_s  += s
    total_skip_c  += c
    total_skip_o  += o
    print(f"{d:12s} {p:>4d} {s:>8d} {c:>8d} {o:>6d}")
print("-" * 64)
print(f"{'合計':12s} {total_post:>4d} {total_skip_s:>8d} {total_skip_c:>8d} {total_skip_o:>6d}")
print()

total_days = len(stats)
avg_per_day = total_post / total_days if total_days else 0
print(f"対象期間: {min(stats.keys())} 〜 {max(stats.keys())} （{total_days} 日）")
print(f"投稿合計: {total_post} 件 / 平均: {avg_per_day:.1f} 件/日")
print()

# 上限妥当性評価
print("▼ 上限設定の妥当性評価（現在: 1日10件 / 1回3件）")
max_day  = max((stats[d]["post"] for d in stats), default=0)
print(f"  1日の最大投稿数 (今回): {max_day} 件")
if max_day <= 10:
    print(f"  → 1日10件の上限で収まっています。妥当。")
else:
    print(f"  → 1日10件の上限を超えています。上限を {max_day} 件以上に引き上げ検討を。")

per_run_estimate = max_day / (24 * 6) if max_day else 0  # 10分おき = 1日144回
print(f"  1回あたり推定 ({max_day}件/日 ÷ 144回): {per_run_estimate:.2f} 件/回")
print("  → 1回3件の上限で通常は十分。警報発令直後のみ超える可能性があります。")
