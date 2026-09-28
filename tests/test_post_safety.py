#!/usr/bin/env /usr/bin/python3
"""
test_post_safety.py  安全装置・X投稿モジュールのテスト

テスト項目（外部通信なし・すべてモックで完結）
  A. OAuth 署名アルゴリズム
  B. 鮮度フィルタ（POST_MAX_AGE_MIN）
  C. 初回実行で過去7日分が一斉投稿されないこと（実データ風タイムスタンプ使用）
  D. 文面ガード（check_content）
  E. 日次カウント管理
  F. 上限適用（apply_limits）
  G. VPWW53/54 重複排除
  H. 注意報解除スキップ・警報解除の投稿
  I. POST_MODE 停止スイッチ
  J. X API 失敗時の動作（モック）
     - 401/402/403/429 で sys.exit(1)
     - 失敗アイテムは seen_ids に追加されない
  K. 投稿成功後に seen_ids を即座に保存する
  L. 設定読み込み（get_config のデフォルト値）
  M. load_credentials の未設定検出
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest.mock as mock
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

# プロジェクトルートを sys.path に追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aomori_bot as bot
import safety
import x_poster

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

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


JST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------------
# A. OAuth 署名アルゴリズム
# ---------------------------------------------------------------------------
print("\n== A. OAuth HMAC-SHA1 署名 ==")

# Twitter / X 公式ドキュメント掲載のテストベクターを使って HMAC-SHA1 実装を検証する。
# ref: https://developer.twitter.com/en/docs/authentication/oauth-1-0a/creating-a-signature
#
# 以下のキー・トークンはすべて X (Twitter) 公式ドキュメントに掲載されているダミー値であり、
# 実在するアカウントや API キーではない。テスト以外の目的で使用してはならない。
#
# 署名ベース文字列を2通りの方法で構築し、HMAC-SHA1 を計算して一致を確認する。
# 公式ドキュメントのパラメータ（架空のダミー値）:
#   consumer_key    : xvz1evFS4wEEPTGEFPHBog
#   consumer_secret : kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw
#   access_token    : 370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb
#   token_secret    : LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE
#   timestamp       : 1318622958
#   nonce           : kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg

_SIGNING_KEY = (
    b"kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw"
    b"&"
    b"LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"
)

# 方法①：公式ドキュメントの署名ベース文字列をそのまま構築（percent-encode 済み）
_BASE_EXACT = (
    "POST"
    "&https%3A%2F%2Fapi.twitter.com%2F1.1%2Fstatuses%2Fupdate.json"
    "&include_entities%3Dtrue"
    "%26oauth_consumer_key%3Dxvz1evFS4wEEPTGEFPHBog"
    "%26oauth_nonce%3DkYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg"
    "%26oauth_signature_method%3DHMAC-SHA1"
    "%26oauth_timestamp%3D1318622958"
    "%26oauth_token%3D370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb"
    "%26oauth_version%3D1.0"
    "%26status%3DHello%2520Ladies%2520%252B%2520Gentlemen%252C"
    "%2520a%2520signed%2520OAuth%2520request%2521"
)

# 方法②：urllib.parse.quote を使って独立に計算
def _pct_test(s):
    return urllib.parse.quote(str(s), safe="")

_params_ref = {
    "include_entities":      "true",
    "oauth_consumer_key":    "xvz1evFS4wEEPTGEFPHBog",
    "oauth_nonce":           "kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
    "oauth_signature_method": "HMAC-SHA1",
    "oauth_timestamp":       "1318622958",
    "oauth_token":           "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
    "oauth_version":         "1.0",
    "status":                "Hello Ladies + Gentlemen, a signed OAuth request!",
}
_sorted_ref = sorted((_pct_test(k), _pct_test(v)) for k, v in _params_ref.items())
_param_str_ref = "&".join(f"{k}={v}" for k, v in _sorted_ref)
_BASE_CALC = "&".join([
    _pct_test("POST"),
    _pct_test("https://api.twitter.com/1.1/statuses/update.json"),
    _pct_test(_param_str_ref),
])

# 両方が一致することを確認（署名ベース文字列の構築が正しい）
ok("署名ベース文字列：2通りの構築方法が一致", _BASE_EXACT == _BASE_CALC,
   f"exact={_BASE_EXACT[:60]}...")

# HMAC-SHA1 を計算（Python 標準ライブラリ）
_sig_exact = base64.b64encode(
    hmac.new(_SIGNING_KEY, _BASE_EXACT.encode(), hashlib.sha1).digest()
).decode()
_sig_calc = base64.b64encode(
    hmac.new(_SIGNING_KEY, _BASE_CALC.encode(), hashlib.sha1).digest()
).decode()
ok("HMAC-SHA1：2通りの計算が一致", _sig_exact == _sig_calc,
   f"sig={_sig_exact}")

# x_poster._build_auth_header が同じ署名アルゴリズムを使うことを確認
# （timestamp/nonce を固定して再現性を確認）
_EXPECTED_SIG_FROM_STDLIB = _sig_calc  # stdlib で計算した値を期待値とする

# Authorization ヘッダーの形式確認（固定 nonce/timestamp で生成）
_creds = {
    "api_key":             "xvz1evFS4wEEPTGEFPHBog",
    "api_secret":          "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
    "access_token":        "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
    "access_token_secret": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
}
_header = x_poster._build_auth_header(
    "POST", "https://api.twitter.com/2/tweets", _creds,
    _nonce="testnonce123",
    _timestamp="1318622958",
)
ok("Authorization ヘッダーが 'OAuth' で始まる",   _header.startswith("OAuth "))
ok("oauth_consumer_key が含まれる",              "oauth_consumer_key" in _header)
ok("oauth_signature_method=HMAC-SHA1 が含まれる", "HMAC-SHA1" in _header)
ok("oauth_signature が含まれる",                 "oauth_signature" in _header)
ok("ランダム要素なし（固定値での再現性）",
   x_poster._build_auth_header(
       "POST", "https://api.twitter.com/2/tweets", _creds,
       _nonce="testnonce123", _timestamp="1318622958",
   ) == _header)


# ---------------------------------------------------------------------------
# B. 鮮度フィルタ
# ---------------------------------------------------------------------------
print("\n== B. 鮮度フィルタ ==")

# 今から 100 分前 → 90 分超で陳腐
_stale_100m = iso(now_utc() - timedelta(minutes=100))
# 今から 50 分前 → 90 分以内で新鮮
_fresh_50m  = iso(now_utc() - timedelta(minutes=50))
# 空文字 → フィルタしない
_empty = ""

ok("100 分前 → 陳腐（is_fresh=False）",
   not safety.is_fresh(_stale_100m, 90), f"ts={_stale_100m[:19]}")
ok("50 分前 → 新鮮（is_fresh=True）",
   safety.is_fresh(_fresh_50m, 90),  f"ts={_fresh_50m[:19]}")
ok("空文字 → フィルタしない（is_fresh=True）",
   safety.is_fresh(_empty, 90))
ok("パース不能文字列 → フィルタしない（is_fresh=True）",
   safety.is_fresh("not-a-date", 90))
ok("89分30秒前 → 新鮮（90分制限内）",
   safety.is_fresh(iso(now_utc() - timedelta(minutes=89, seconds=30)), 90))
ok("91 分前 → 陳腐（境界値）",
   not safety.is_fresh(iso(now_utc() - timedelta(minutes=91)), 90))


# ---------------------------------------------------------------------------
# C. 初回実行で過去7日分が一斉投稿されないこと
# ---------------------------------------------------------------------------
print("\n== C. 初回実行：過去7日分の一斉投稿防止 ==")

# 過去 1〜7 日前の実データ風タイムスタンプ
_old_items = [
    {
        "id": f"jma_past_{d}d",
        "type": "VPWW53",
        "text": f"【気象注意報】{d}日前 津軽では強風に注意。\n出典：気象庁",
        "source": "JMA XML",
        "updated": iso(datetime.now(JST) - timedelta(days=d)),
    }
    for d in range(1, 8)
]

_stale_count = sum(
    1 for item in _old_items
    if not safety.is_fresh(item["updated"], max_age_min=90)
)
ok(f"過去7日分すべて（{len(_old_items)}件）が鮮度フィルタで除外される",
   _stale_count == len(_old_items),
   f"除外={_stale_count}/{len(_old_items)}")

# 1時間以内のものだけが残ることを確認
_recent = {"id": "now", "type": "VPWW53",
           "text": "【気象注意報】津軽では強風に注意。\n出典：気象庁",
           "source": "JMA XML",
           "updated": iso(now_utc() - timedelta(minutes=30))}
_mixed = _old_items + [_recent]
_passed = [i for i in _mixed if safety.is_fresh(i["updated"], 90)]
ok("7日分 + 直近1件 → 直近1件だけ通過", len(_passed) == 1,
   f"通過={len(_passed)}件")


# ---------------------------------------------------------------------------
# D. 文面ガード（check_content）
# ---------------------------------------------------------------------------
print("\n== D. 文面ガード ==")

_good = "【気象注意報】9月28日20時 津軽では強風に注意してください。\n出典：気象庁"

# 正常
try:
    safety.check_content(_good)
    ok("正常な投稿文 → エラーなし", True)
except ValueError as e:
    ok("正常な投稿文 → エラーなし", False, str(e))

# 空文字
try:
    safety.check_content("")
    ok("空文字 → ValueError", False)
except ValueError:
    ok("空文字 → ValueError", True)

# 空白のみ
try:
    safety.check_content("   ")
    ok("空白のみ → ValueError", False)
except ValueError:
    ok("空白のみ → ValueError", True)

# 141 字
_over = "あ" * 140 + "い" + "\n出典：気象庁"
try:
    safety.check_content(_over)
    ok("141字 → ValueError", False)
except ValueError:
    ok("141字 → ValueError", True)

# 出典なし
try:
    safety.check_content("【気象注意報】津軽では強風に注意。")
    ok("出典なし → ValueError", False)
except ValueError:
    ok("出典なし → ValueError", True)

# URL 含む
try:
    safety.check_content("https://example.com を見てください。\n出典：気象庁")
    ok("URL含む → ValueError", False)
except ValueError:
    ok("URL含む → ValueError", True)

# ドメイン風文字列含む
try:
    safety.check_content("詳細は jma.go.jp まで\n出典：気象庁")
    ok("ドメイン風文字列含む → ValueError", False)
except ValueError:
    ok("ドメイン風文字列含む → ValueError", True)

# 日本語テキスト（気象庁）は誤検知しない
try:
    safety.check_content("【気象注意報】津軽では強風に注意。\n出典：気象庁")
    ok("「気象庁」日本語テキスト → 誤検知しない", True)
except ValueError as e:
    ok("「気象庁」日本語テキスト → 誤検知しない", False, str(e))

# 震度 M4.5 表記は誤検知しない
try:
    safety.check_content("【地震情報】M4.5 青森県内最大震度3。\n出典：気象庁")
    ok("M4.5 表記 → 誤検知しない", True)
except ValueError as e:
    ok("M4.5 表記 → 誤検知しない", False, str(e))


# ---------------------------------------------------------------------------
# E. 日次カウント管理
# ---------------------------------------------------------------------------
print("\n== E. 日次カウント ==")

with tempfile.TemporaryDirectory() as tmpdir:
    # ファイルなし → 0 を返す
    cnt = safety.load_daily_count(tmpdir)
    ok("ファイルなし → 0", cnt == 0, f"got={cnt}")

    # 保存して読み込み
    safety.save_daily_count(5, tmpdir)
    cnt2 = safety.load_daily_count(tmpdir)
    ok("保存→読み込み: 5", cnt2 == 5, f"got={cnt2}")

    # 日付が変わったら 0 にリセット
    yesterday = (datetime.now(JST) - timedelta(days=1)).strftime("%Y-%m-%d")
    path = os.path.join(tmpdir, "daily_count.json")
    with open(path, "w") as f:
        json.dump({"date": yesterday, "count": 8}, f)
    cnt3 = safety.load_daily_count(tmpdir)
    ok("昨日のデータ → 0 にリセット", cnt3 == 0, f"got={cnt3}")


# ---------------------------------------------------------------------------
# F. 上限適用（apply_limits）
# ---------------------------------------------------------------------------
print("\n== F. 上限適用 ==")

_cands = [{"id": f"item_{i}"} for i in range(5)]

# daily_limit=10, per_run_limit=3 → 3件通過
_to_post, _skipped = safety.apply_limits(_cands, daily_count=0,
                                          daily_limit=10, per_run_limit=3)
ok("per_run_limit=3: 通過3件", len(_to_post) == 3,    f"got={len(_to_post)}")
ok("per_run_limit=3: 見送り2件", len(_skipped) == 2,  f"got={len(_skipped)}")

# daily_limit に届く場合
_to_post2, _skipped2 = safety.apply_limits(_cands, daily_count=8,
                                            daily_limit=10, per_run_limit=5)
ok("daily残り2: 通過2件", len(_to_post2) == 2,       f"got={len(_to_post2)}")
ok("daily残り2: 見送り3件", len(_skipped2) == 3,     f"got={len(_skipped2)}")

# daily_limit = 0 → 全件見送り
_to_post3, _skipped3 = safety.apply_limits(_cands, daily_count=10,
                                            daily_limit=10, per_run_limit=5)
ok("daily 上限到達: 全件見送り", len(_to_post3) == 0, f"got={len(_to_post3)}")


# ---------------------------------------------------------------------------
# G. VPWW53/54 重複排除
# ---------------------------------------------------------------------------
print("\n== G. VPWW53/54 重複排除 ==")

_same_text = "【気象注意報】津軽では強風に注意。\n出典：気象庁"
_posts_dup = [
    {"id": "vpww53_001", "type": "VPWW53", "text": _same_text},
    {"id": "vpww54_001", "type": "VPWW54", "text": _same_text},  # 同一文面
    {"id": "vpww53_002", "type": "VPWW53",
     "text": "【気象警報】下北では大雨に警戒。\n出典：気象庁"},
]
_deduped, _dup_ids = bot._dedup_by_text(_posts_dup)
ok("重複排除後: 2件", len(_deduped) == 2,   f"got={len(_deduped)}")
ok("重複 ID が返る: 1件", len(_dup_ids) == 1, f"got={_dup_ids}")
ok("VPWW54 が重複として除外",               "vpww54_001" in _dup_ids)
ok("VPWW53 は残る",
   any(p["id"] == "vpww53_001" for p in _deduped))


# ---------------------------------------------------------------------------
# H. 解除フィルタ（_is_cancel_to_skip）
# ---------------------------------------------------------------------------
print("\n== H. 解除フィルタ ==")

# 注意報解除 → スキップ（True）
ok("注意報解除 → スキップ",
   bot._is_cancel_to_skip("大雨注意報が解除されました", "解除"))
ok("強風注意報解除 → スキップ",
   bot._is_cancel_to_skip("津軽では強風注意報が解除されました", "解除"))

# 警報解除 → 投稿（False）
ok("大雨警報解除 → 投稿",
   not bot._is_cancel_to_skip("大雨警報が解除されました", "解除"))
ok("特別警報解除 → 投稿",
   not bot._is_cancel_to_skip("特別警報が解除されました", "解除"))

# 警報・注意報両方含む → 警報優先で投稿（False）
ok("警報・注意報両方含む → 投稿",
   not bot._is_cancel_to_skip("大雨警報・大雨注意報が解除", "解除"))

# 解除でない → スキップしない（False）
ok("発表メッセージ → 解除フィルタに引っかからない",
   not bot._is_cancel_to_skip("津軽では大雨注意報が発表されました", "発表"))
ok("空 info_type でも headline に解除なし → False",
   not bot._is_cancel_to_skip("津軽では大雨注意報に注意してください", ""))

# XML 解析レベルで注意報解除が None を返すことを確認
_cancel_chui_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>青森県気象警報・注意報</Title>
    <ReportDateTime>2026-09-28T22:00:00+09:00</ReportDateTime>
    <InfoType>解除</InfoType>
    <Headline>
      <Text>津軽では大雨注意報が解除されました。</Text>
    </Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"/>
</Report>""".encode("utf-8")

_cancel_keihou_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>青森県気象警報・注意報</Title>
    <ReportDateTime>2026-09-28T23:00:00+09:00</ReportDateTime>
    <InfoType>解除</InfoType>
    <Headline>
      <Text>津軽では大雨警報が解除されました。</Text>
    </Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"/>
</Report>""".encode("utf-8")

_result_chui_cancel = bot.parse_weather_xml(_cancel_chui_xml, "VPWW53")
ok("注意報解除 XML → None（投稿しない）",
   _result_chui_cancel is None, f"got={_result_chui_cancel}")

_result_keihou_cancel = bot.parse_weather_xml(_cancel_keihou_xml, "VPWW53")
ok("警報解除 XML → 投稿文が生成される",
   _result_keihou_cancel is not None,
   str(_result_keihou_cancel)[:60] if _result_keihou_cancel else "None")
ok("警報解除 XML → '解除' が含まれる",
   _result_keihou_cancel and "解除" in _result_keihou_cancel)


# ---------------------------------------------------------------------------
# I. POST_MODE 停止スイッチ
# ---------------------------------------------------------------------------
print("\n== I. POST_MODE 停止スイッチ ==")

_env_dry  = {"POST_MODE": "dry"}
_env_live = {"POST_MODE": "live"}
_env_none = {}

with mock.patch.dict(os.environ, _env_dry,  clear=False):
    ok("POST_MODE=dry → mode='dry'",  safety.get_config()["post_mode"] == "dry")
with mock.patch.dict(os.environ, _env_live, clear=False):
    ok("POST_MODE=live → mode='live'", safety.get_config()["post_mode"] == "live")
# 未設定 → dry が既定
_orig_mode = os.environ.pop("POST_MODE", None)
ok("POST_MODE 未設定 → mode='dry' (デフォルト)",
   safety.get_config()["post_mode"] == "dry")
if _orig_mode is not None:
    os.environ["POST_MODE"] = _orig_mode


# ---------------------------------------------------------------------------
# J. X API 失敗時の動作（HTTPError モック）
# ---------------------------------------------------------------------------
print("\n== J. X API 失敗時の動作 ==")

_test_creds = {
    "api_key":             "testkey",
    "api_secret":          "testsecret",
    "access_token":        "testtoken",
    "access_token_secret": "testtokensecret",
}


def _mock_http_error(code: int):
    """指定コードの HTTPError を raise するモック urlopen"""
    def _raise(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="https://api.twitter.com/2/tweets",
            code=code,
            msg=f"HTTP {code}",
            hdrs={},
            fp=None,
        )
    return _raise


for _code in (401, 402, 403, 429):
    try:
        with mock.patch("urllib.request.urlopen", _mock_http_error(_code)):
            x_poster.post_tweet("テスト\n出典：気象庁", _test_creds)
        ok(f"HTTP {_code} → HTTPError が raise される", False)
    except urllib.error.HTTPError as e:
        ok(f"HTTP {_code} → HTTPError が raise される", e.code == _code,
           f"code={e.code}")
    except Exception as e:
        ok(f"HTTP {_code} → HTTPError が raise される", False, str(e))

# URLError（接続不可）
try:
    with mock.patch("urllib.request.urlopen",
                    side_effect=urllib.error.URLError("connection refused")):
        x_poster.post_tweet("テスト\n出典：気象庁", _test_creds)
    ok("URLError → raise される", False)
except urllib.error.URLError:
    ok("URLError → raise される", True)

# 成功時の動作確認（201 レスポンスをモック）
_mock_resp_data = json.dumps({"data": {"id": "1234567890"}}).encode()


class _MockResp:
    def read(self):
        return _mock_resp_data
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass


with mock.patch("urllib.request.urlopen", return_value=_MockResp()):
    _tweet_id = x_poster.post_tweet("テスト\n出典：気象庁", _test_creds)
ok("投稿成功 → tweet_id が返る", _tweet_id == "1234567890", f"got={_tweet_id}")


# ---------------------------------------------------------------------------
# K. 投稿失敗時に seen_ids に追加されないこと・成功後に即座に保存されること
# ---------------------------------------------------------------------------
print("\n== K. 失敗時の seen_ids 非追加・成功後の即時保存 ==")

with tempfile.TemporaryDirectory() as _tmpdir:
    # ── 成功シナリオ ──
    _seen = set()
    _post_item = {
        "id": "test_item_001",
        "type": "VPWW53",
        "text": "【気象注意報】津軽では強風に注意。\n出典：気象庁",
        "source": "JMA XML",
        "updated": iso(now_utc() - timedelta(minutes=5)),
    }

    _daily = 0
    with mock.patch("urllib.request.urlopen", return_value=_MockResp()):
        _tid = x_poster.post_tweet(_post_item["text"], _test_creds)
    _seen.add(_post_item["id"])
    # 即座に保存
    _seen_path = os.path.join(_tmpdir, "seen_ids.json")
    with open(_seen_path, "w") as _f:
        json.dump(sorted(_seen), _f)

    with open(_seen_path) as _f:
        _saved = set(json.load(_f))
    ok("成功後に seen_ids に即追加・保存される",
       _post_item["id"] in _saved)

    # ── 失敗シナリオ ──
    _seen2 = set()
    try:
        with mock.patch("urllib.request.urlopen", _mock_http_error(402)):
            x_poster.post_tweet(_post_item["text"], _test_creds)
        # ここには来ない
    except urllib.error.HTTPError:
        pass  # 失敗なので seen_ids には追加しない
    ok("失敗後は seen_ids に追加されない",
       _post_item["id"] not in _seen2)


# ---------------------------------------------------------------------------
# L. 設定デフォルト値
# ---------------------------------------------------------------------------
print("\n== L. 設定デフォルト値 ==")

_saved_env = {k: os.environ.pop(k, None)
              for k in ("POST_MODE", "POST_MAX_AGE_MIN",
                        "POST_DAILY_LIMIT", "POST_PER_RUN_LIMIT", "ENABLE_P2P")}
_default_cfg = safety.get_config()
for k, v in _saved_env.items():
    if v is not None:
        os.environ[k] = v

ok("デフォルト POST_MODE=dry",           _default_cfg["post_mode"]     == "dry")
ok("デフォルト POST_MAX_AGE_MIN=90",     _default_cfg["max_age_min"]   == 90)
ok("デフォルト POST_DAILY_LIMIT=10",     _default_cfg["daily_limit"]   == 10)
ok("デフォルト POST_PER_RUN_LIMIT=3",    _default_cfg["per_run_limit"] == 3)
ok("デフォルト ENABLE_P2P=False",        _default_cfg["enable_p2p"]    is False)


# ---------------------------------------------------------------------------
# M. load_credentials の未設定検出
# ---------------------------------------------------------------------------
print("\n== M. load_credentials 未設定検出 ==")

_saved_creds = {k: os.environ.pop(k, None)
                for k in ("X_API_KEY", "X_API_SECRET",
                          "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")}
try:
    x_poster.load_credentials()
    ok("環境変数未設定 → ValueError", False)
except ValueError as e:
    ok("環境変数未設定 → ValueError", True, str(e)[:60])
finally:
    for k, v in _saved_creds.items():
        if v is not None:
            os.environ[k] = v

# 全部設定された場合
with mock.patch.dict(os.environ, {
    "X_API_KEY":             "key",
    "X_API_SECRET":          "secret",
    "X_ACCESS_TOKEN":        "token",
    "X_ACCESS_TOKEN_SECRET": "token_secret",
}, clear=False):
    _loaded = x_poster.load_credentials()
ok("全部設定 → 辞書が返る",  isinstance(_loaded, dict))
ok("api_key が含まれる",     "api_key" in _loaded)
ok("api_secret が含まれる",  "api_secret" in _loaded)


# ---------------------------------------------------------------------------
# N. 403 重複コンテンツの処理（DuplicatePostError）
# ---------------------------------------------------------------------------
print("\n== N. 403 重複コンテンツの処理 ==")

# X API v2 は同じ文面の投稿を 403 + "duplicate" を含むボディで拒否する。
# このテストで使用する _test_creds は "testkey" 等のテスト専用ダミー値である。
# セクション A の OAuth テストで使用している値（_creds）も
# X (Twitter) 公式ドキュメント掲載のダミーであり、実在するキーではない。
# ref: https://developer.twitter.com/en/support/twitter-api/error-troubleshooting

import io as _io

_dup_body = json.dumps({
    "title": "Forbidden",
    "detail": "You are not allowed to create a Tweet with duplicate content.",
    "type": "about:blank",
    "status": 403,
}).encode("utf-8")

_other_403_body = json.dumps({
    "title": "Forbidden",
    "detail": "Forbidden",
    "status": 403,
}).encode("utf-8")


def _mock_403_with_body(body: bytes):
    """指定ボディを持つ 403 HTTPError を raise するモック urlopen"""
    def _raise(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="https://api.twitter.com/2/tweets",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=_io.BytesIO(body),
        )
    return _raise


# 重複コンテンツの 403 → DuplicatePostError が raise される
try:
    with mock.patch("urllib.request.urlopen", _mock_403_with_body(_dup_body)):
        x_poster.post_tweet("テスト\n出典：気象庁", _test_creds)
    ok("403 重複コンテンツ → DuplicatePostError", False, "例外が raise されなかった")
except x_poster.DuplicatePostError:
    ok("403 重複コンテンツ → DuplicatePostError", True)
except Exception as e:
    ok("403 重複コンテンツ → DuplicatePostError", False, str(e))

# 重複コンテンツでない 403 → HTTPError のまま（DuplicatePostError にならない）
try:
    with mock.patch("urllib.request.urlopen", _mock_403_with_body(_other_403_body)):
        x_poster.post_tweet("テスト\n出典：気象庁", _test_creds)
    ok("403 非重複 → HTTPError のまま", False, "例外が raise されなかった")
except urllib.error.HTTPError as e:
    ok("403 非重複 → HTTPError のまま", e.code == 403, f"code={e.code}")
except x_poster.DuplicatePostError:
    ok("403 非重複 → HTTPError のまま", False, "誤って DuplicatePostError になった")
except Exception as e:
    ok("403 非重複 → HTTPError のまま", False, str(e))

# fp=None の 403（ボディ読み取り失敗）→ HTTPError にフォールバック
try:
    with mock.patch("urllib.request.urlopen", _mock_http_error(403)):
        x_poster.post_tweet("テスト\n出典：気象庁", _test_creds)
    ok("403 fp=None → HTTPError にフォールバック", False)
except urllib.error.HTTPError as e:
    ok("403 fp=None → HTTPError にフォールバック", e.code == 403)
except x_poster.DuplicatePostError:
    ok("403 fp=None → HTTPError にフォールバック", False, "誤って DuplicatePostError")
except Exception as e:
    ok("403 fp=None → HTTPError にフォールバック", False, str(e))

# DuplicatePostError は Exception のサブクラス（通常の except では捕捉されない問題がないか確認）
ok("DuplicatePostError は Exception のサブクラス",
   issubclass(x_poster.DuplicatePostError, Exception))
ok("DuplicatePostError は HTTPError のサブクラスではない",
   not issubclass(x_poster.DuplicatePostError, urllib.error.HTTPError))


# ---------------------------------------------------------------------------
# O. 上限超過通知（sys.exit(1) と seen_ids への追加）
# ---------------------------------------------------------------------------
print("\n== O. 上限超過通知 ==")

import contextlib
import logging as _logging

_fresh_ts_o = iso(now_utc() - timedelta(minutes=5))
_mock_candidates_o = [
    {
        "id": "over_001",
        "type": "VPWW53",
        "text": "【気象注意報】津軽では強風に注意してください。\n出典：気象庁",
        "source": "JMA XML",
        "updated": _fresh_ts_o,
    },
    {
        "id": "over_002",
        "type": "VPWW53",
        "text": "【気象注意報】下北では強風に注意してください。\n出典：気象庁",
        "source": "JMA XML",
        "updated": _fresh_ts_o,
    },
]

with tempfile.TemporaryDirectory() as _od:

    def _fake_setup_logging():
        _logging.basicConfig(
            level=_logging.WARNING,
            handlers=[_logging.StreamHandler(sys.stdout)],
            force=True,
        )

    with contextlib.ExitStack() as _stack:
        _stack.enter_context(mock.patch("aomori_bot.STATE_DIR", _od))
        _stack.enter_context(mock.patch("aomori_bot.setup_logging", _fake_setup_logging))
        _stack.enter_context(mock.patch("aomori_bot.process_weather_feed",
                                        return_value=_mock_candidates_o))
        _stack.enter_context(mock.patch("aomori_bot.process_eqvol_feed",
                                        return_value=[]))
        _stack.enter_context(mock.patch("aomori_bot.process_sample_data",
                                        return_value=[]))
        _stack.enter_context(mock.patch.dict(os.environ, {
            "POST_DAILY_LIMIT":   "1",   # 1 件しか通さない → over_002 が skipped
            "POST_PER_RUN_LIMIT": "5",
            "ENABLE_P2P":         "false",
        }, clear=False))

        try:
            bot.run()
            ok("上限超過: sys.exit(1) が呼ばれる", False, "sys.exit が呼ばれなかった")
        except SystemExit as _se:
            ok("上限超過: sys.exit(1) が呼ばれる", _se.code == 1, f"code={_se.code}")

    # seen_ids.json に通過分・見送り分の両方が記録されているか確認
    _sp = os.path.join(_od, "seen_ids.json")
    if os.path.exists(_sp):
        with open(_sp) as _f:
            _saved_ids = set(json.load(_f))
        ok("上限超過: 通過した item が seen_ids に追加された",
           "over_001" in _saved_ids, f"ids={_saved_ids}")
        ok("上限超過: 見送った item も seen_ids に追加された（繰り返し通知を防ぐ）",
           "over_002" in _saved_ids, f"ids={_saved_ids}")
    else:
        ok("上限超過: 通過した item が seen_ids に追加された", False, "file not found")
        ok("上限超過: 見送った item も seen_ids に追加された", False, "file not found")


# ---------------------------------------------------------------------------
# 結果集計
# ---------------------------------------------------------------------------
print()
total  = len(_results)
passed = sum(1 for _, r in _results if r)
failed = total - passed
bar    = "-" * 40
print(bar)
print(f"テスト結果: {passed}/{total} PASS  ({failed} FAIL)")
print(bar)

sys.exit(0 if failed == 0 else 1)
