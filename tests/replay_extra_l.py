#!/usr/bin/env /usr/bin/python3
"""
replay_extra_l.py  過去 7 日分の extra_l.xml を時系列順に処理して
                   新フィルタ（Status チェック + クールダウン）を適用した場合の
                   投稿件数を集計するスクリプト（要件5）。

鮮度フィルタは無効（REPLAY_MODE）にし、全エントリを時系列順に処理する。
クールダウンはメモリ内でシミュレート（ファイルに書かない）。

対象電文:
  - VPWW53_020000（気象警報・注意報、青森県）
  - VPHW50_020000（竜巻注意情報）
  - VPHW51_020000（竜巻注意情報・目撃情報付き）

VPHW50 と VPHW51 は同一ヘッドラインで同時発行されるため、
_dedup_by_text と同じロジックで 1 件に集約する。

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

# 対象コード（竜巻は 020000 以外の地域も含む）
REPLAY_VPWW53_AREA = "020000"
REPLAY_VPHW_CODES  = {"VPHW50", "VPHW51"}
REPLAY_VPHW_AREA   = "020000"   # 青森県のみ

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

entries_vpww53 = []
entries_vphw   = []

for entry in root_feed.findall(f"{{{ATOM_NS}}}entry"):
    link    = entry.find(f"{{{ATOM_NS}}}link")
    href    = link.get("href") if link is not None else ""
    updated = entry.findtext(f"{{{ATOM_NS}}}updated") or ""

    fname = href.rsplit("/", 1)[-1]
    # 例: 20260928141418_0_VPWW53_020000.xml
    parts = fname.replace(".xml", "").split("_")
    product = parts[-2] if len(parts) >= 4 else ""
    area    = parts[-1] if len(parts) >= 4 else ""

    if product == "VPWW53" and area == REPLAY_VPWW53_AREA:
        entries_vpww53.append({"href": href, "updated": updated, "product": "VPWW53"})
    elif product in REPLAY_VPHW_CODES and area == REPLAY_VPHW_AREA:
        entries_vphw.append({"href": href, "updated": updated, "product": product})

entries_vpww53.sort(key=lambda e: e["updated"])
entries_vphw.sort(key=lambda e: e["updated"])
print(f"  VPWW53_020000 エントリ: {len(entries_vpww53)} 件")
print(f"  VPHW50/51_020000 エントリ: {len(entries_vphw)} 件\n")

# ── VPWW53 処理ループ ──────────────────────────────────────
cooldown: dict = {}
seen_ids: set  = set()
seen_texts: dict = {}   # 重複排除用（VPHW50/51 間で使う）

stats: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
# stats[date_jst][prod]["post" | "skip_status" | "skip_cooldown" | "skip_dedup" | "skip_other"]

for e in entries_vpww53:
    eid = e["href"]
    if eid in seen_ids:
        continue

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
        stats[date_key]["VPWW53"]["skip_other"] += 1
        continue

    post_text, _, _, headline_key = bot._parse_weather_xml_full(xml_data, "VPWW53")

    if post_text is None:
        seen_ids.add(eid)
        stats[date_key]["VPWW53"]["skip_status"] += 1
        continue

    if safety.is_in_cooldown(headline_key, cooldown, COOLDOWN_HOURS):
        seen_ids.add(eid)
        stats[date_key]["VPWW53"]["skip_cooldown"] += 1
        continue

    seen_ids.add(eid)
    safety.update_warn_cooldown(headline_key, cooldown)
    stats[date_key]["VPWW53"]["post"] += 1
    print(f"  [POST/VPWW53] {dt_jst.strftime('%m/%d %H:%M')} {post_text[:55]!r}")

# ── VPHW50/51 処理ループ ───────────────────────────────────
seen_texts_vphw: set = set()   # 重複テキスト排除（VPHW50 と VPHW51 が同一文面）

for e in entries_vphw:
    eid = e["href"]
    prod = e["product"]
    if eid in seen_ids:
        continue

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
        stats[date_key]["VPHW"]["skip_other"] += 1
        continue

    post_text, _, _, _ = bot._parse_weather_xml_full(xml_data, prod)

    if post_text is None:
        seen_ids.add(eid)
        stats[date_key]["VPHW"]["skip_status"] += 1
        continue

    # VPHW50/51 は同一ヘッドラインで同時発行されるため重複排除
    if post_text in seen_texts_vphw:
        seen_ids.add(eid)
        stats[date_key]["VPHW"]["skip_dedup"] += 1
        continue

    seen_ids.add(eid)
    seen_texts_vphw.add(post_text)
    stats[date_key]["VPHW"]["post"] += 1
    print(f"  [POST/VPHW]   {dt_jst.strftime('%m/%d %H:%M')} {post_text[:55]!r}")

# ── 集計レポート ──────────────────────────────────────────
print("\n" + "=" * 72)
print("集計結果（VPWW53/VPHW50/51 対象、鮮度フィルタなし、新ロジック適用）")
print("=" * 72)

# VPWW53
print(f"\n▼ VPWW53（気象警報・注意報）")
print(f"{'日付':12s} {'投稿':>4s} {'ステ見送':>8s} {'CD見送':>8s} {'その他':>6s}")
print("-" * 40)
tv_p = tv_s = tv_c = tv_o = 0
for d in sorted(stats.keys()):
    p = stats[d]["VPWW53"]["post"]
    s = stats[d]["VPWW53"]["skip_status"]
    c = stats[d]["VPWW53"]["skip_cooldown"]
    o = stats[d]["VPWW53"]["skip_other"]
    if p + s + c + o == 0:
        continue
    tv_p += p; tv_s += s; tv_c += c; tv_o += o
    print(f"{d:12s} {p:>4d} {s:>8d} {c:>8d} {o:>6d}")
print("-" * 40)
print(f"{'合計':12s} {tv_p:>4d} {tv_s:>8d} {tv_c:>8d} {tv_o:>6d}")

# VPHW
print(f"\n▼ VPHW50/51（竜巻注意情報・青森県 020000）")
print(f"{'日付':12s} {'投稿':>4s} {'ステ見送':>8s} {'重複除外':>8s} {'その他':>6s}")
print("-" * 40)
th_p = th_s = th_d = th_o = 0
for d in sorted(stats.keys()):
    p = stats[d]["VPHW"]["post"]
    s = stats[d]["VPHW"]["skip_status"]
    dd = stats[d]["VPHW"]["skip_dedup"]
    o = stats[d]["VPHW"]["skip_other"]
    if p + s + dd + o == 0:
        continue
    th_p += p; th_s += s; th_d += dd; th_o += o
    print(f"{d:12s} {p:>4d} {s:>8d} {dd:>8d} {o:>6d}")
if th_p + th_s + th_d + th_o == 0:
    print("  （該当なし）")
else:
    print("-" * 40)
    print(f"{'合計':12s} {th_p:>4d} {th_s:>8d} {th_d:>8d} {th_o:>6d}")

# サマリ
print()
all_dates = sorted(stats.keys())
if all_dates:
    print(f"対象期間: {all_dates[0]} 〜 {all_dates[-1]} （{len(all_dates)} 日）")
total_post = tv_p + th_p
print(f"投稿合計（VPWW53+VPHW): {total_post} 件")
print(f"  VPWW53: {tv_p} 件 / VPHW: {th_p} 件")

# 上限妥当性評価（日ごと合算）
print()
print("▼ 上限設定の妥当性評価（現在: 1日10件 / 1回3件）")
all_days_total = {
    d: stats[d]["VPWW53"]["post"] + stats[d]["VPHW"]["post"]
    for d in stats
}
max_day = max(all_days_total.values(), default=0)
print(f"  1日の最大投稿数 (VPWW53+VPHW): {max_day} 件")
if max_day <= 10:
    print(f"  → 1日10件の上限で収まっています。妥当。")
else:
    print(f"  → 1日10件の上限を超えています。上限を {max_day} 件以上に引き上げ検討を。")

per_run_estimate = max_day / (24 * 6) if max_day else 0
print(f"  1回あたり推定 ({max_day}件/日 ÷ 144回): {per_run_estimate:.2f} 件/回")
print("  → 1回3件の上限で通常は十分。警報発令直後のみ超える可能性があります。")
