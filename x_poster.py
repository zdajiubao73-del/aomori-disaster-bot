#!/usr/bin/env /usr/bin/python3
"""
x_poster.py  X API v2 投稿モジュール（標準ライブラリのみ）

OAuth 1.0a HMAC-SHA1 署名で POST /2/tweets を行う。
POST /2/tweets は Content-Type: application/json のため、
リクエストボディのパラメータは OAuth 署名の対象に含めない。

使い方:
    import x_poster
    credentials = x_poster.load_credentials()
    tweet_id = x_poster.post_tweet("テスト投稿", credentials)
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

TWEETS_URL = "https://api.twitter.com/2/tweets"

# X API で即時終了すべきエラーコード（残高不足・認証・権限・レート制限）
FATAL_HTTP_CODES = {401, 402, 403, 429}


class DuplicatePostError(Exception):
    """
    X API が「重複コンテンツ」403 を返したときに raise される。

    X API v2 は同じ文面の投稿を 403 Forbidden で拒否し、
    レスポンスボディに "duplicate" を含む。
    呼び出し側はこのエラーを「投稿済み」扱いとして seen_ids に追加し、
    次の投稿へ進むこと（sys.exit(1) せずに継続）。

    ref: https://developer.twitter.com/en/support/twitter-api/error-troubleshooting
    """
    pass


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _pct(s: str) -> str:
    """RFC 3986 percent-encode（safe 文字なし）"""
    return urllib.parse.quote(str(s), safe="")


def _build_auth_header(
    method: str,
    url: str,
    credentials: dict,
    *,
    _nonce: str = "",
    _timestamp: str = "",
) -> str:
    """
    OAuth 1.0a Authorization ヘッダーを生成する。

    application/json ボディは署名に含めない（X API v2 の仕様に従う）。

    Parameters
    ----------
    method      : HTTP メソッド（大文字に変換）
    url         : エンドポイント URL
    credentials : api_key / api_secret / access_token / access_token_secret
    _nonce      : テスト用固定 nonce（省略時はランダム生成）
    _timestamp  : テスト用固定 timestamp（省略時は現在時刻）
    """
    ts    = _timestamp or str(int(time.time()))
    nonce = _nonce    or secrets.token_hex(16)

    oauth_params: dict[str, str] = {
        "oauth_consumer_key":     credentials["api_key"],
        "oauth_nonce":            nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        ts,
        "oauth_token":            credentials["access_token"],
        "oauth_version":          "1.0",
    }

    # ① パラメータ文字列：キー名でソート（percent-encode 済みで比較）
    sorted_pairs = sorted(
        (_pct(k), _pct(v)) for k, v in oauth_params.items()
    )
    param_str = "&".join(f"{k}={v}" for k, v in sorted_pairs)

    # ② シグネチャベース文字列
    base_str = "&".join([
        _pct(method.upper()),
        _pct(url),
        _pct(param_str),
    ])

    # ③ 署名キー（コンシューマシークレット & アクセストークンシークレット）
    signing_key = (
        _pct(credentials["api_secret"]) + "&" +
        _pct(credentials["access_token_secret"])
    ).encode("ascii")

    # ④ HMAC-SHA1 署名
    sig = base64.b64encode(
        hmac.new(signing_key, base_str.encode("ascii"), hashlib.sha1).digest()
    ).decode("ascii")

    # ⑤ Authorization ヘッダー値を組み立て
    oauth_params["oauth_signature"] = sig
    header_fields = ", ".join(
        f'{_pct(k)}="{_pct(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_fields}"


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def post_tweet(text: str, credentials: dict) -> str:
    """
    X API v2 (POST /2/tweets) でツイートを投稿する。

    Parameters
    ----------
    text        : 投稿本文（文面ガード適用済みであること）
    credentials : load_credentials() の戻り値

    Returns
    -------
    str : 投稿に成功した場合のツイート ID

    Raises
    ------
    urllib.error.HTTPError
        HTTP 401 / 402 / 403 / 429 など X API エラー
        （呼び出し側が sys.exit(1) すること）
    urllib.error.URLError
        ネットワーク接続エラー
        （呼び出し側が sys.exit(1) すること）
    """
    body = json.dumps({"text": text}).encode("utf-8")
    auth_header = _build_auth_header("POST", TWEETS_URL, credentials)

    req = urllib.request.Request(
        TWEETS_URL,
        data=body,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
            "User-Agent": "aomori-disaster-bot/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            # レスポンスボディに "duplicate" が含まれるか確認する。
            # X API v2 は重複コンテンツ投稿を
            #   {"detail": "You are not allowed to create a Tweet with duplicate content.", ...}
            # という 403 で拒否する。この場合は DuplicatePostError を raise する。
            # ボディ読み取りに失敗した場合は通常の 403（HTTPError）として扱う。
            try:
                body_bytes = e.read()
                body = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""
                if "duplicate" in body.lower():
                    raise DuplicatePostError(
                        f"重複コンテンツ（同じ文面が既存）: {body[:120]}"
                    )
            except DuplicatePostError:
                raise
            except Exception:
                pass  # body 読み取り失敗 → 通常の 403 として処理
        raise

    return result["data"]["id"]


def load_credentials() -> dict:
    """
    環境変数から X API 認証情報を読み込む。

    Returns
    -------
    dict : api_key / api_secret / access_token / access_token_secret

    Raises
    ------
    ValueError : 必要な環境変数のいずれかが未設定
    """
    env_map = {
        "api_key":             "X_API_KEY",
        "api_secret":          "X_API_SECRET",
        "access_token":        "X_ACCESS_TOKEN",
        "access_token_secret": "X_ACCESS_TOKEN_SECRET",
    }
    creds: dict[str, str] = {}
    missing: list[str] = []

    for attr, env_name in env_map.items():
        val = os.environ.get(env_name, "")
        if not val:
            missing.append(env_name)
        creds[attr] = val

    if missing:
        raise ValueError(f"環境変数が未設定: {', '.join(missing)}")

    return creds
