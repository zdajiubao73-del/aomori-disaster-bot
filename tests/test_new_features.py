#!/usr/bin/env /usr/bin/python3
"""
test_new_features.py  新機能のテスト

テスト項目:
  P. WEATHER_PRODUCTS の内容確認
     - VPWW53 が含まれる
     - VPWW54/55/56/58/59/61 が除外されている
  Q. _has_new_kind() の動作確認
     - Status要素なし → True (安全側)
     - Status=継続のみ → False (見送り)
     - Status=継続 + 発表 → True (新規あり)
     - Status=発表警報・注意報はなし のみ → False (見送り)
  R. VPWW53 継続のみの電文 → parse_weather_xml が None を返す
  S. safety モジュールのクールダウン関数
     - is_in_cooldown: 未登録 → False
     - is_in_cooldown: 登録済み直後 (cooldown_hours=3) → True
     - is_in_cooldown: 登録済みで期限超過 → False
     - is_in_cooldown: headline_key が空文字 → False
     - update_warn_cooldown: 正しく記録される
     - save/load_warn_cooldown: 往復テスト、古いエントリ削除
  T. get_config の cooldown_hours デフォルト値
"""

import os
import sys
import json
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aomori_bot as bot
import safety

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results: list = []


def ok(name: str, cond: bool, info: str = "") -> None:
    status = PASS if cond else FAIL
    msg = f"  [{status}] {name}"
    if info:
        msg += f" — {info}"
    print(msg)
    _results.append((name, cond))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


# ──────────────────────────────────────────────────────────────
# P. WEATHER_PRODUCTS の内容確認
# ──────────────────────────────────────────────────────────────
print("\n== P. WEATHER_PRODUCTS の内容確認 ==")

ok("VPWW53 が含まれる", "VPWW53" in bot.WEATHER_PRODUCTS)
ok("VXWW50（土砂災害警戒情報）が含まれる", "VXWW50" in bot.WEATHER_PRODUCTS)
ok("VPOA50（記録的短時間大雨）が含まれる", "VPOA50" in bot.WEATHER_PRODUCTS)
ok("VPHW62（竜巻注意情報）が含まれる",     "VPHW62" in bot.WEATHER_PRODUCTS)

ok("VPWW54（H27 重複フォーマット）が除外されている", "VPWW54" not in bot.WEATHER_PRODUCTS)
ok("VPWW55（大雨 R06 分割）が除外されている",        "VPWW55" not in bot.WEATHER_PRODUCTS)
ok("VPWW56（土砂 R06 分割）が除外されている",        "VPWW56" not in bot.WEATHER_PRODUCTS)
ok("VPWW58（暴風 R06 分割）が除外されている",        "VPWW58" not in bot.WEATHER_PRODUCTS)
ok("VPWW59（波浪 R06 分割）が除外されている",        "VPWW59" not in bot.WEATHER_PRODUCTS)
ok("VPWW61（竜巻・雷 R06 分割）が除外されている",   "VPWW61" not in bot.WEATHER_PRODUCTS)


# ──────────────────────────────────────────────────────────────
# _has_new_kind() テスト用 XML ヘルパー
# ──────────────────────────────────────────────────────────────
import xml.etree.ElementTree as ET

def make_vpww53_xml(statuses: list[str]) -> bytes:
    """Body/Warning に指定した Status 値を持つ VPWW53 XML を生成する"""
    items = ""
    for st in statuses:
        items += f"""
      <Item>
        <Kind><Name>強風注意報</Name><Code>15</Code><Status>{st}</Status></Kind>
        <Areas><Area><Name>青森県</Name><Code>020000</Code></Area></Areas>
      </Item>"""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>青森県気象警報・注意報</Title>
    <ReportDateTime>2026-09-28T23:14:00+09:00</ReportDateTime>
    <InfoType>発表</InfoType>
    <Headline>
      <Text>青森県では、強風に注意してください。</Text>
    </Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/">
    <Warning type="気象警報・注意報（府県予報区等）">{items}
    </Warning>
  </Body>
</Report>"""
    return xml.encode("utf-8")


# ──────────────────────────────────────────────────────────────
# Q. _has_new_kind() の動作確認
# ──────────────────────────────────────────────────────────────
print("\n== Q. _has_new_kind() の動作 ==")

# Status 要素なし (Body が空) → True (安全側)
_xml_no_status_body = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>青森県気象警報・注意報</Title>
    <ReportDateTime>2026-09-28T23:14:00+09:00</ReportDateTime>
    <InfoType>発表</InfoType>
    <Headline><Text>青森県では、強風に注意してください。</Text></Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"/>
</Report>""".encode("utf-8")

_root_no = ET.fromstring(_xml_no_status_body)
ok("Status 要素なし → True (安全側・投稿する)",
   bot._has_new_kind(_root_no) is True)

# Status=継続のみ → False
_root_k = ET.fromstring(make_vpww53_xml(["継続", "継続"]))
ok("Status=継続のみ → False (見送り)",
   bot._has_new_kind(_root_k) is False)

# Status=継続 + 発表 → True
_root_h = ET.fromstring(make_vpww53_xml(["継続", "発表"]))
ok("Status=継続 + 発表 → True (新規あり・投稿する)",
   bot._has_new_kind(_root_h) is True)

# Status=発表警報・注意報はなし のみ → False
_root_na = ET.fromstring(make_vpww53_xml(["発表警報・注意報はなし"]))
ok("Status=発表警報・注意報はなし のみ → False (見送り)",
   bot._has_new_kind(_root_na) is False)

# Status=継続 + 発表警報・注意報はなし → False
_root_cn = ET.fromstring(make_vpww53_xml(["継続", "発表警報・注意報はなし"]))
ok("Status=継続 + 発表警報・注意報はなし → False (見送り)",
   bot._has_new_kind(_root_cn) is False)

# Status=警報から注意報へ移行 → True (種類変化)
_root_dn = ET.fromstring(make_vpww53_xml(["継続", "警報から注意報へ移行"]))
ok("Status=警報から注意報へ移行 → True (種類変化・投稿する)",
   bot._has_new_kind(_root_dn) is True)


# ──────────────────────────────────────────────────────────────
# R. VPWW53 継続のみ → parse_weather_xml が None を返す
# ──────────────────────────────────────────────────────────────
print("\n== R. VPWW53 継続のみ → None ==")

_xml_keizoku = make_vpww53_xml(["継続", "継続"])
_result_keizoku = bot.parse_weather_xml(_xml_keizoku, "VPWW53")
ok("VPWW53 継続のみ → None (投稿しない)",
   _result_keizoku is None,
   f"got={_result_keizoku!r:.60s}" if _result_keizoku else "None")

_xml_hatsu = make_vpww53_xml(["発表", "継続"])
_result_hatsu = bot.parse_weather_xml(_xml_hatsu, "VPWW53")
ok("VPWW53 発表あり → 投稿文が生成される",
   _result_hatsu is not None,
   str(_result_hatsu)[:60] if _result_hatsu else "None")

# Status なし XML（既存の簡易テスト XML と同じ形式）では True を返す（安全側）
_xml_no_body = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>青森県気象警報・注意報</Title>
    <ReportDateTime>2026-09-28T23:14:00+09:00</ReportDateTime>
    <InfoType>発表</InfoType>
    <Headline>
      <Text>津軽、下北では、強風に注意してください。</Text>
    </Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"/>
</Report>""".encode("utf-8")
_result_no_body = bot.parse_weather_xml(_xml_no_body, "VPWW53")
ok("VPWW53 Status 要素なし → 投稿文が生成される（安全側）",
   _result_no_body is not None,
   str(_result_no_body)[:60] if _result_no_body else "None")


# ──────────────────────────────────────────────────────────────
# S. クールダウン関数
# ──────────────────────────────────────────────────────────────
print("\n== S. クールダウン関数 ==")

_now_utc = now_utc()

# is_in_cooldown: 未登録 → False
_cd_empty: dict = {}
ok("is_in_cooldown: 未登録 → False",
   not safety.is_in_cooldown("強風注意報テスト", _cd_empty, 3))

# is_in_cooldown: 空文字キー → False（クールダウン対象外）
ok("is_in_cooldown: 空文字キー → False",
   not safety.is_in_cooldown("", {"": _now_utc.isoformat()}, 3))

# is_in_cooldown: 直後 → True
_cd_fresh = {"強風注意報テスト": _now_utc.isoformat()}
ok("is_in_cooldown: 記録直後 → True (3時間以内)",
   safety.is_in_cooldown("強風注意報テスト", _cd_fresh, 3))

# is_in_cooldown: 4時間後 → False (3時間超過)
_cd_old = {"強風注意報テスト": (_now_utc - timedelta(hours=4)).isoformat()}
ok("is_in_cooldown: 4時間後 → False (3時間超過)",
   not safety.is_in_cooldown("強風注意報テスト", _cd_old, 3))

# is_in_cooldown: パース不能な日時 → False (安全側)
_cd_broken = {"強風注意報テスト": "not-a-date"}
ok("is_in_cooldown: 壊れた日時 → False (安全側)",
   not safety.is_in_cooldown("強風注意報テスト", _cd_broken, 3))

# update_warn_cooldown: 正しく記録される
_cd_mutable: dict = {}
safety.update_warn_cooldown("大雨警報テスト", _cd_mutable)
ok("update_warn_cooldown: キーが追加される",
   "大雨警報テスト" in _cd_mutable)
ok("update_warn_cooldown: 追加後クールダウン中",
   safety.is_in_cooldown("大雨警報テスト", _cd_mutable, 3))

# update_warn_cooldown: 空文字キーは無視
_cd_mutable2: dict = {}
safety.update_warn_cooldown("", _cd_mutable2)
ok("update_warn_cooldown: 空文字キーは無視される",
   len(_cd_mutable2) == 0)

# save/load_warn_cooldown: 往復テスト
with tempfile.TemporaryDirectory() as _tmpdir_cd:
    _cd_to_save = {
        "青森県では、強風に注意してください。": _now_utc.isoformat(),
        "津軽では、大雨に注意してください。":   (_now_utc - timedelta(hours=1)).isoformat(),
    }
    safety.save_warn_cooldown(_cd_to_save, _tmpdir_cd)
    _cd_loaded = safety.load_warn_cooldown(_tmpdir_cd)
    ok("save/load 往復: キー数が一致",
       len(_cd_loaded) == 2, f"got={len(_cd_loaded)}")
    ok("save/load 往復: 値が一致",
       _cd_loaded.get("青森県では、強風に注意してください。") == _now_utc.isoformat())

    # 古いエントリが削除されること（retain_hours=1: 2時間超は削除）
    _cd_with_old = {
        "新しいキー": _now_utc.isoformat(),
        "古いキー":   (_now_utc - timedelta(hours=25)).isoformat(),
    }
    safety.save_warn_cooldown(_cd_with_old, _tmpdir_cd, retain_hours=24)
    _cd_after_cleanup = safety.load_warn_cooldown(_tmpdir_cd)
    ok("save: 24時間超のエントリは削除される",
       "古いキー" not in _cd_after_cleanup,
       f"keys={list(_cd_after_cleanup.keys())}")
    ok("save: 新しいエントリは残る",
       "新しいキー" in _cd_after_cleanup)

# ファイルなし → 空 dict を返す
with tempfile.TemporaryDirectory() as _tmpdir_empty:
    _cd_missing = safety.load_warn_cooldown(_tmpdir_empty)
    ok("load_warn_cooldown: ファイルなし → 空 dict",
       _cd_missing == {}, f"got={_cd_missing}")


# ──────────────────────────────────────────────────────────────
# T. get_config の cooldown_hours デフォルト値
# ──────────────────────────────────────────────────────────────
print("\n== T. get_config cooldown_hours ==")

import unittest.mock as mock

_saved_ch = os.environ.pop("POST_COOLDOWN_HOURS", None)
_cfg_default = safety.get_config()
if _saved_ch is not None:
    os.environ["POST_COOLDOWN_HOURS"] = _saved_ch

ok("デフォルト POST_COOLDOWN_HOURS=3",
   _cfg_default["cooldown_hours"] == 3,
   f"got={_cfg_default['cooldown_hours']}")

with mock.patch.dict(os.environ, {"POST_COOLDOWN_HOURS": "6"}, clear=False):
    ok("POST_COOLDOWN_HOURS=6 → 6 時間",
       safety.get_config()["cooldown_hours"] == 6)


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
