#!/usr/bin/env /usr/bin/python3
"""
test_label_fix.py - 警報ラベル修正の回帰テスト（要件 1・2・4）

修正内容:
  classify_weather_label に kind_entries パラメータを追加し、
  見出し文のキーワードではなく Body/Warning の Kind/Name 末尾でラベルを決める。

テスト項目:
  W. _kind_label_from_entries の単体テスト
     W1.  大雨警報（発表）→ 気象警報
     W2.  大雨特別警報（発表）→ 気象特別警報
     W3.  強風注意報（発表）→ 気象注意報
     W4.  複数Kind: 大雨警報+強風注意報 → 気象警報（最重視）
     W5.  全件継続 → "" （フォールバックへ）
     W6.  大雨警報解除 → 気象警報（解除でも警報ラベル）
     W7.  Kind 空 + 発表 → 気象注意報（安全側・最低値）
     W8.  危険警報（大雨危険警報）→ 気象警報（危険警報は警報と同扱い）

  X. classify_weather_label に kind_entries を渡した場合
     X1.  kind_entries あり・見出しに「警報」なし → 気象警報（Kind で決定）
     X2.  kind_entries なし（None）・見出しに「警報」あり → 気象警報（フォールバック）
     X3.  kind_entries 空リスト・見出しに「警報」なし → 気象注意報（フォールバック）
     X4.  kind_entries あり・見出しに「特別警報」 → 気象特別警報（Kind優先）
     X5.  VXWW50 → 土砂災害警戒情報（product 優先、kind_entries 無視）
     X6.  VPHW50 → 竜巻注意情報（product 優先）

  Y. 疑似電文フィクスチャを使ったテスト（要件 4）
     Y1.  VPWW53_pseudo_ooame_hatsu_no_keiro_word.xml
          - assert: Kind/Status が意図どおり入っている
          - parse_weather_xml → 投稿文に「気象警報」が含まれる
          - 見出し文に「警報」語なし → 旧コードなら「気象注意報」になる（修正確認）
     Y2.  VPWW53_pseudo_kiken_keiro_headline.xml
          - assert: Kind/Status が意図どおり入っている
          - 見出し文に「〈危険警報（大雨）〉」を含む
          - parse_weather_xml → 投稿文に「気象警報」が含まれる
          - 見出し文は加工されていない（見出しに〈危険警報（大雨）〉がそのまま入る）

フィクスチャの出典:
  気象庁防災情報 XML フォーマット仕様 (VPWW53) に基づく疑似電文。
  個人情報なし。
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


import xml.etree.ElementTree as ET


def extract_kind_entries_from_bytes(data: bytes) -> list:
    """テスト用: XML バイト列から Kind/Status ペアを抽出"""
    root = ET.fromstring(data)
    return bot._extract_kind_entries(root)


# ──────────────────────────────────────────────────────────────
# W. _kind_label_from_entries の単体テスト
# ──────────────────────────────────────────────────────────────
print("\n== W. _kind_label_from_entries 単体テスト ==")

# W1. 大雨警報（発表）→ 気象警報
_w1 = bot._kind_label_from_entries([("大雨警報", "発表")])
ok("W1. 大雨警報（発表）→ 気象警報", _w1 == "気象警報", f"got={_w1!r}")

# W2. 大雨特別警報（発表）→ 気象特別警報
_w2 = bot._kind_label_from_entries([("大雨特別警報", "発表")])
ok("W2. 大雨特別警報（発表）→ 気象特別警報", _w2 == "気象特別警報", f"got={_w2!r}")

# W3. 強風注意報（発表）→ 気象注意報
_w3 = bot._kind_label_from_entries([("強風注意報", "発表")])
ok("W3. 強風注意報（発表）→ 気象注意報", _w3 == "気象注意報", f"got={_w3!r}")

# W4. 複数Kind: 大雨警報+強風注意報 → 気象警報（最重視）
_w4 = bot._kind_label_from_entries([("大雨警報", "発表"), ("強風注意報", "継続")])
ok("W4. 大雨警報(発表)+強風注意報(継続) → 気象警報", _w4 == "気象警報", f"got={_w4!r}")

# W5. 全件継続 → "" （フォールバックへ）
_w5 = bot._kind_label_from_entries([("強風注意報", "継続"), ("波浪注意報", "継続")])
ok("W5. 全件継続 → '' （フォールバック用）", _w5 == "", f"got={_w5!r}")

# W6. 大雨警報解除 → 気象警報（解除でも警報ラベル）
_w6 = bot._kind_label_from_entries([("大雨警報", "解除")])
ok("W6. 大雨警報（解除）→ 気象警報", _w6 == "気象警報", f"got={_w6!r}")

# W7. Kind名が空 + 発表 → 気象注意報（最低重み = 1）
_w7 = bot._kind_label_from_entries([("", "発表")])
ok("W7. Kind名空（発表）→ 気象注意報", _w7 == "気象注意報", f"got={_w7!r}")

# W8. 危険警報（大雨危険警報）→ 気象警報（危険警報は末尾「警報」に一致）
_w8 = bot._kind_label_from_entries([("大雨危険警報", "発表")])
ok("W8. 大雨危険警報（発表）→ 気象警報（危険警報=警報扱い）", _w8 == "気象警報", f"got={_w8!r}")


# ──────────────────────────────────────────────────────────────
# X. classify_weather_label に kind_entries を渡した場合
# ──────────────────────────────────────────────────────────────
print("\n== X. classify_weather_label (kind_entries あり/なし) ==")

# X1. kind_entries あり・見出しに「警報」なし → 気象警報（Kind で決定）
_x1 = bot.classify_weather_label(
    "VPWW53",
    "三八上北では、低い土地の浸水に警戒してください。",
    [("大雨警報", "発表")]
)
ok("X1. 大雨警報(kind) + 見出しに「警報」なし → 気象警報",
   _x1 == "気象警報", f"got={_x1!r}")

# X2. kind_entries=None・見出しに「警報」あり → 気象警報（フォールバック）
_x2 = bot.classify_weather_label(
    "VPWW53",
    "青森県では、大雨に警報が出ています。",
    None
)
ok("X2. kind_entries=None + 見出しに「警報」あり → 気象警報（フォールバック）",
   _x2 == "気象警報", f"got={_x2!r}")

# X3. kind_entries=[]（空）・見出しに「警報」なし → 気象注意報（フォールバック）
_x3 = bot.classify_weather_label(
    "VPWW53",
    "青森県では、強風に注意してください。",
    []
)
ok("X3. kind_entries=[] + 見出しに「警報」なし → 気象注意報（フォールバック）",
   _x3 == "気象注意報", f"got={_x3!r}")

# X4. kind_entries あり（特別警報）・見出しに「特別警報」あり → 気象特別警報（Kind優先）
_x4 = bot.classify_weather_label(
    "VPWW53",
    "青森県に大雨特別警報が発表されました。",
    [("大雨特別警報", "発表")]
)
ok("X4. 大雨特別警報(kind) → 気象特別警報",
   _x4 == "気象特別警報", f"got={_x4!r}")

# X5. VXWW50 → 土砂災害警戒情報（product 優先）
_x5 = bot.classify_weather_label(
    "VXWW50",
    "青森県では、土砂災害警戒情報が発表されました。",
    [("大雨警報", "発表")]
)
ok("X5. VXWW50 → 土砂災害警戒情報（product 優先）",
   _x5 == "土砂災害警戒情報", f"got={_x5!r}")

# X6. VPHW50 → 竜巻注意情報（product 優先）
_x6 = bot.classify_weather_label(
    "VPHW50",
    "青森県では、竜巻が発生するおそれがあります。",
    None
)
ok("X6. VPHW50 → 竜巻注意情報（product 優先）",
   _x6 == "竜巻注意情報", f"got={_x6!r}")


# ──────────────────────────────────────────────────────────────
# Y. 疑似電文フィクスチャを使ったテスト（要件 4）
# ──────────────────────────────────────────────────────────────
print("\n== Y. 疑似電文フィクスチャ ==")

# ── Y1. 大雨警報発表・見出しに「警報」なし ───────────────────
FNAME_Y1 = "VPWW53_pseudo_ooame_hatsu_no_keiro_word.xml"
_y1_data = load_fixture(FNAME_Y1)
_y1_kinds = extract_kind_entries_from_bytes(_y1_data)

# assert: Kind/Status が意図どおり入っている
assert any(k == "大雨警報" and s == "発表" for k, s in _y1_kinds), \
    f"Y1 ASSERT FAIL: 大雨警報(発表) がフィクスチャに存在しない。found={_y1_kinds}"
ok("Y1. assert: 大雨警報(発表) がフィクスチャに存在する", True,
   f"kinds={[(k,s) for k,s in _y1_kinds if s not in ('継続','発表警報・注意報はなし')]}")

# 見出し文に「警報」が含まれないことを確認（これが修正前のバグ再現条件）
_y1_headline = "三八上北では、低い土地の浸水に警戒してください。"
ok("Y1. 見出し文に「警報」を含まない（修正前は誤ラベルになるケース）",
   "警報" not in _y1_headline, f"headline={_y1_headline!r}")

# parse_weather_xml → 投稿文生成・ラベル確認
_y1_text = bot.parse_weather_xml(_y1_data, "VPWW53")
ok("Y1. 投稿文が生成される", _y1_text is not None,
   str(_y1_text)[:80] if _y1_text else "None")
ok("Y1. 投稿文に「気象警報」が含まれる（修正後）",
   _y1_text is not None and "【気象警報】" in _y1_text,
   f"got={str(_y1_text)[:80]!r}")
ok("Y1. 投稿文に「気象注意報」が含まれない（修正前の誤ラベルが出ていない）",
   _y1_text is not None and "【気象注意報】" not in _y1_text,
   f"got={str(_y1_text)[:80]!r}")
ok("Y1. 投稿文が 140 字以内",
   _y1_text is not None and len(_y1_text) <= 140,
   f"{len(_y1_text)}字" if _y1_text else "None")
ok("Y1. 投稿文に '出典：気象庁' が含まれる",
   _y1_text is not None and "出典：気象庁" in _y1_text)

# ── Y2. 危険警報プレフィックス付き見出し ──────────────────────
FNAME_Y2 = "VPWW53_pseudo_kiken_keiro_headline.xml"
_y2_data = load_fixture(FNAME_Y2)
_y2_kinds = extract_kind_entries_from_bytes(_y2_data)

# assert: Kind/Status が意図どおり入っている
assert any(k == "大雨警報" and s == "発表" for k, s in _y2_kinds), \
    f"Y2 ASSERT FAIL: 大雨警報(発表) がフィクスチャに存在しない。found={_y2_kinds}"
ok("Y2. assert: 大雨警報(発表) がフィクスチャに存在する", True,
   f"kinds={[(k,s) for k,s in _y2_kinds if s not in ('継続','発表警報・注意報はなし')]}")

# parse_weather_xml → 投稿文生成・ラベル確認
_y2_text = bot.parse_weather_xml(_y2_data, "VPWW53")
ok("Y2. 投稿文が生成される", _y2_text is not None,
   str(_y2_text)[:80] if _y2_text else "None")
ok("Y2. 投稿文に「気象警報」が含まれる",
   _y2_text is not None and "【気象警報】" in _y2_text,
   f"got={str(_y2_text)[:80]!r}")
ok("Y2. 見出し文に「〈危険警報（大雨）〉」が含まれる（見出し文は加工しない）",
   _y2_text is not None and "〈危険警報（大雨）〉" in _y2_text,
   f"got={str(_y2_text)[:100]!r}")
ok("Y2. 投稿文が 140 字以内",
   _y2_text is not None and len(_y2_text) <= 140,
   f"{len(_y2_text)}字" if _y2_text else "None")
ok("Y2. 投稿文に '出典：気象庁' が含まれる",
   _y2_text is not None and "出典：気象庁" in _y2_text)


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
