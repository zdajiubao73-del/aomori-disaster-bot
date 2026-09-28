#!/usr/bin/env /usr/bin/python3
"""
test_fixtures.py - 実電文フィクスチャを使った回帰テスト

tests/fixtures/ に保存した気象庁の公開 XML 電文を読み込み、
新フィルタ（Kind/Status 解除判定 + VPHW50/51 竜巻対応）が
正しく動作することを検証する。

フィクスチャの出典:
  気象庁防災情報 XML (https://www.data.jma.go.jp/developer/xml/)
  公開データ（個人情報なし）

テスト項目:
  U. VPWW53 実電文フィクスチャ
     U1. 2026-09-28 23:14（発表 → 投稿）
     U2. 2026-09-28 23:16（継続のみ → 見送り）
     U3. 2026-09-28 22:57（継続のみ → 見送り）
     U4. 2026-09-28 20:19（注意報解除+継続 → 見送り）
     U5. 2026-09-24 09:51（注意報解除のみ → 見送り）
     U6. 2026-09-24 06:03（発表 → 投稿）
  V. VPHW50/51 竜巻注意情報フィクスチャ
     V1. VPHW50 2026-09-28 14:13（竜巻注意情報として投稿）
     V2. VPHW51 2026-09-28 14:13（竜巻注意情報として投稿）
     V3. VPHW50 + VPHW51 → 重複排除で 1 件
     V4. 投稿文が 140 字以内、「出典：気象庁」を含む
     V5. 投稿文が句点「。」で切られ、途中で切れていない
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import aomori_bot as bot

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results: list = []

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def ok(name: str, cond: bool, info: str = "") -> None:
    status = PASS if cond else FAIL
    msg = f"  [{status}] {name}"
    if info:
        msg += f" — {info}"
    print(msg)
    _results.append((name, cond))


def load_fixture(fname: str) -> bytes:
    path = os.path.join(FIXTURES_DIR, fname)
    with open(path, "rb") as f:
        return f.read()


# ──────────────────────────────────────────────────────────────
# U. VPWW53 実電文フィクスチャ
# ──────────────────────────────────────────────────────────────
print("\n== U. VPWW53 実電文フィクスチャ ==")

# U1. 2026-09-28 23:14 JST — 発表あり → 投稿する
_u1 = bot.parse_weather_xml(load_fixture("VPWW53_20260928_2314_hatsu.xml"), "VPWW53")
ok("U1. 2026-09-28 23:14（発表）→ 投稿文が生成される",
   _u1 is not None,
   str(_u1)[:80] if _u1 else "None")
ok("U1. 投稿文が 140 字以内",
   _u1 is not None and len(_u1) <= 140,
   f"{len(_u1)}字" if _u1 else "None")
ok("U1. 投稿文に '出典：気象庁' が含まれる",
   _u1 is not None and "出典：気象庁" in _u1)

# U2. 2026-09-28 23:16 JST — 継続のみ → 見送り
_u2 = bot.parse_weather_xml(load_fixture("VPWW53_20260928_2316_keizoku.xml"), "VPWW53")
ok("U2. 2026-09-28 23:16（継続のみ）→ None（見送り）",
   _u2 is None,
   f"got={str(_u2)[:60]}" if _u2 else "None")

# U3. 2026-09-28 22:57 JST — 継続のみ → 見送り
_u3 = bot.parse_weather_xml(load_fixture("VPWW53_20260928_2257_keizoku.xml"), "VPWW53")
ok("U3. 2026-09-28 22:57（継続のみ）→ None（見送り）",
   _u3 is None,
   f"got={str(_u3)[:60]}" if _u3 else "None")

# U4. 2026-09-28 20:19 JST — 注意報解除+継続 → 見送り
_u4 = bot.parse_weather_xml(load_fixture("VPWW53_20260928_2019_kaijo.xml"), "VPWW53")
ok("U4. 2026-09-28 20:19（注意報解除+継続）→ None（見送り）",
   _u4 is None,
   f"got={str(_u4)[:60]}" if _u4 else "None")

# U5. 2026-09-24 09:51 JST — 注意報解除のみ → 見送り
_u5 = bot.parse_weather_xml(load_fixture("VPWW53_20260924_0951_kaijo.xml"), "VPWW53")
ok("U5. 2026-09-24 09:51（注意報解除のみ）→ None（見送り）",
   _u5 is None,
   f"got={str(_u5)[:60]}" if _u5 else "None")

# U6. 2026-09-24 06:03 JST — 発表あり → 投稿する
_u6 = bot.parse_weather_xml(load_fixture("VPWW53_20260924_0603_hatsu.xml"), "VPWW53")
ok("U6. 2026-09-24 06:03（発表）→ 投稿文が生成される",
   _u6 is not None,
   str(_u6)[:80] if _u6 else "None")
ok("U6. 投稿文が 140 字以内",
   _u6 is not None and len(_u6) <= 140,
   f"{len(_u6)}字" if _u6 else "None")
ok("U6. 投稿文に '出典：気象庁' が含まれる",
   _u6 is not None and "出典：気象庁" in _u6)


# ──────────────────────────────────────────────────────────────
# V. VPHW50/51 竜巻注意情報フィクスチャ
# ──────────────────────────────────────────────────────────────
print("\n== V. VPHW50/51 竜巻注意情報フィクスチャ ==")

_v50 = bot.parse_weather_xml(load_fixture("VPHW50_20260928_1413_aomori.xml"), "VPHW50")
_v51 = bot.parse_weather_xml(load_fixture("VPHW51_20260928_1413_aomori.xml"), "VPHW51")

# V1. VPHW50 → 竜巻注意情報として投稿
ok("V1. VPHW50（竜巻注意情報）→ 投稿文が生成される",
   _v50 is not None,
   str(_v50)[:80] if _v50 else "None")
ok("V1. VPHW50 投稿文に '竜巻注意情報' が含まれる",
   _v50 is not None and "竜巻注意情報" in _v50)

# V2. VPHW51 → 竜巻注意情報として投稿
ok("V2. VPHW51（目撃情報付き）→ 投稿文が生成される",
   _v51 is not None,
   str(_v51)[:80] if _v51 else "None")
ok("V2. VPHW51 投稿文に '竜巻注意情報' が含まれる",
   _v51 is not None and "竜巻注意情報" in _v51)

# V3. VPHW50 と VPHW51 は同一ヘッドライン → 重複排除で 1 件
ok("V3. VPHW50 と VPHW51 の投稿文が同じ（重複排除対象）",
   _v50 is not None and _v50 == _v51,
   f"VPHW50={str(_v50)[:40]!r}  VPHW51={str(_v51)[:40]!r}")

_posts_vphw = []
if _v50 is not None:
    _posts_vphw.append({"id": "fix_VPHW50", "type": "VPHW50", "text": _v50})
if _v51 is not None:
    _posts_vphw.append({"id": "fix_VPHW51", "type": "VPHW51", "text": _v51})
_deduped_v, _dup_ids_v = bot._dedup_by_text(_posts_vphw)

ok("V3. 重複排除後 1 件",
   len(_deduped_v) == 1,
   f"got={len(_deduped_v)}")
ok("V3. 重複 ID が 1 件返る",
   len(_dup_ids_v) == 1,
   f"got={_dup_ids_v}")

# V4. 投稿文が 140 字以内、「出典：気象庁」を含む
for label, txt in [("VPHW50", _v50), ("VPHW51", _v51)]:
    ok(f"V4. {label} 投稿文が 140 字以内",
       txt is not None and len(txt) <= 140,
       f"{len(txt)}字" if txt else "None")
    ok(f"V4. {label} 投稿文に '出典：気象庁' が含まれる",
       txt is not None and "出典：気象庁" in txt)

# V5. 投稿文が句点「。」で切られ、末尾（出典の直前）が「。」または「…」
if _v50 is not None:
    _citation = "\n出典：気象庁"
    _body_v50 = _v50[:-len(_citation)] if _v50.endswith(_citation) else _v50
    ok("V5. VPHW50 投稿文が句点「。」か「…」で終わる（出典の直前）",
       _body_v50.endswith("。") or _body_v50.endswith("…"),
       f"末尾={_body_v50[-4:]!r}")


# ──────────────────────────────────────────────────────────────
# 結果集計
# ──────────────────────────────────────────────────────────────
print()
total  = len(_results)
passed = sum(1 for _, r in _results if r)
failed = total - passed
bar    = "-" * 40
print(bar)
print(f"テスト結果: {passed}/{total} PASS  ({failed} FAIL)")
print(bar)

sys.exit(0 if failed == 0 else 1)
