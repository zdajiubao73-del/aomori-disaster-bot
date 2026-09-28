#!/usr/bin/env /usr/bin/python3
"""
safety.py  青森災害情報ボット 安全装置モジュール（標準ライブラリのみ）

- 鮮度フィルタ（POST_MAX_AGE_MIN 分を超えた情報を見送り）
- 1日あたり・1回あたりの投稿上限
- 文面ガード（URL・ドメイン・文字数・出典の確認）
- 日次カウント保存
- 設定読み込み
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

# 文面ガード：URL またはドメイン風の ASCII 文字列パターン
# 「出典：気象庁」を除外してから検索するため、日本語の「気象庁」はヒットしない
_URL_PATTERN = re.compile(
    r'https?://'                           # http:// または https://
    r'|www\.'                              # www.
    r'|\b[a-zA-Z0-9\-]{2,63}\.[a-zA-Z]{2,6}\b'  # example.com 形式
)


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

def get_config() -> dict:
    """
    環境変数から実行設定を読み込む。
    未設定の場合は安全側のデフォルト値を返す。

    Returns
    -------
    dict
        post_mode     : "dry" | "live"  （既定: "dry"）
        max_age_min   : int              （既定: 90）
        daily_limit   : int              （既定: 10）
        per_run_limit : int              （既定: 3）
        enable_p2p    : bool             （既定: False）
    """
    return {
        "post_mode":      os.environ.get("POST_MODE", "dry").lower(),
        "max_age_min":    int(os.environ.get("POST_MAX_AGE_MIN",    "90")),
        "daily_limit":    int(os.environ.get("POST_DAILY_LIMIT",    "10")),
        "per_run_limit":  int(os.environ.get("POST_PER_RUN_LIMIT",  "3")),
        "enable_p2p":     os.environ.get("ENABLE_P2P", "false").lower() == "true",
        "cooldown_hours": int(os.environ.get("POST_COOLDOWN_HOURS", "3")),
    }


# ---------------------------------------------------------------------------
# 鮮度フィルタ
# ---------------------------------------------------------------------------

def is_fresh(updated_str: str, max_age_min: int) -> bool:
    """
    updated_str（ISO8601 形式）が現在時刻から max_age_min 分以内なら True。
    パースできない・空の場合は True（フィルタしない）。

    Parameters
    ----------
    updated_str : Atom フィードの <updated> や XML の <ReportDateTime>
    max_age_min : 許容する最大経過分数
    """
    if not updated_str:
        return True
    try:
        dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        age_min = (
            datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        ).total_seconds() / 60
        return age_min <= max_age_min
    except Exception:
        return True  # パースエラーはフィルタしない（安全側）


# ---------------------------------------------------------------------------
# 文面ガード
# ---------------------------------------------------------------------------

def check_content(text: str) -> None:
    """
    投稿直前の文面検査。問題があれば ValueError を raise する。

    チェック項目
    ------------
    1. 空文字列でないこと
    2. 全角 140 字以内（len() でカウント）
    3. 「出典：気象庁」を含むこと
    4. URL またはドメイン風の ASCII 文字列を含まないこと

    Parameters
    ----------
    text : 投稿予定の文面

    Raises
    ------
    ValueError : いずれかのチェックに引っかかった場合
    """
    if not text or not text.strip():
        raise ValueError("文面が空です")

    if len(text) > 140:
        raise ValueError(f"140字超です: {len(text)} 字")

    if "出典：気象庁" not in text:
        raise ValueError("「出典：気象庁」が含まれていません")

    # 「出典：気象庁」の部分を除いてからドメインチェック
    check_text = text.replace("出典：気象庁", "")
    if _URL_PATTERN.search(check_text):
        raise ValueError(f"URL/ドメイン文字列を含みます: {text[:80]!r}")


# ---------------------------------------------------------------------------
# 日次カウント
# ---------------------------------------------------------------------------

def load_daily_count(state_dir: str) -> int:
    """
    今日の投稿済み件数を読み込む。
    日付が変わっていた場合・ファイルがない場合は 0 を返す。
    """
    path = os.path.join(state_dir, "daily_count.json")
    today = datetime.now(JST).strftime("%Y-%m-%d")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == today:
            return int(data.get("count", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return 0


def save_daily_count(count: int, state_dir: str) -> None:
    """今日の投稿済み件数をファイルに保存する。"""
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "daily_count.json")
    today = datetime.now(JST).strftime("%Y-%m-%d")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": today, "count": count}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# クールダウン（同一内容の再投稿抑制）
# ---------------------------------------------------------------------------

def load_warn_cooldown(state_dir: str) -> dict:
    """
    warn_cooldown.json を読み込む。
    フォーマット: {headline_key: ISO8601_datetime_str, ...}
    ファイルなし・JSON 不正の場合は空の dict を返す。
    """
    path = os.path.join(state_dir, "warn_cooldown.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_warn_cooldown(cooldown: dict, state_dir: str, retain_hours: int = 24) -> None:
    """
    warn_cooldown.json を保存する。
    retain_hours より古いエントリは削除してファイルの肥大化を防ぐ。

    Parameters
    ----------
    cooldown     : {headline_key: iso_datetime_str}
    state_dir    : 保存先ディレクトリ
    retain_hours : この時間より古いエントリを削除（既定 24 時間）
    """
    now_utc = datetime.now(timezone.utc)
    cleaned: dict = {}
    for key, ts_str in cooldown.items():
        try:
            ts    = datetime.fromisoformat(ts_str)
            age_h = (now_utc - ts.astimezone(timezone.utc)).total_seconds() / 3600
            if age_h < retain_hours:
                cleaned[key] = ts_str
        except Exception:
            pass  # 壊れたエントリは除去
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "warn_cooldown.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)


def is_in_cooldown(headline_key: str, cooldown: dict, cooldown_hours: int) -> bool:
    """
    headline_key がクールダウン中かどうかを返す。

    Returns True if the same headline_key was posted within cooldown_hours.
    Returns False if:
      - headline_key が空文字（クールダウン対象外）
      - headline_key が cooldown 辞書にない（初回）
      - 記録時刻から cooldown_hours 以上経過している
    """
    if not headline_key:
        return False
    if headline_key not in cooldown:
        return False
    try:
        last_ts = datetime.fromisoformat(cooldown[headline_key])
        age_h   = (
            datetime.now(timezone.utc) - last_ts.astimezone(timezone.utc)
        ).total_seconds() / 3600
        return age_h < cooldown_hours
    except Exception:
        return False  # パースエラーはクールダウンしない（安全側）


def update_warn_cooldown(headline_key: str, cooldown: dict) -> None:
    """
    cooldown 辞書の headline_key に現在 UTC 時刻を記録する。
    headline_key が空文字の場合は何もしない。
    """
    if headline_key:
        cooldown[headline_key] = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 投稿上限の適用
# ---------------------------------------------------------------------------

def apply_limits(
    candidates: list,
    daily_count: int,
    daily_limit: int,
    per_run_limit: int,
) -> tuple:
    """
    投稿上限を適用して、投稿対象と見送り対象に分ける。

    Parameters
    ----------
    candidates    : 投稿候補のリスト（freshnessフィルタ・内容チェック済み）
    daily_count   : 本日すでに投稿済みの件数
    daily_limit   : 本日の上限件数
    per_run_limit : 今回の実行での上限件数

    Returns
    -------
    (to_post, skipped_by_limit)
    to_post          : 実際に投稿するリスト
    skipped_by_limit : 上限で見送ったリスト
    """
    remaining_daily = daily_limit - daily_count
    remaining_run   = per_run_limit

    to_post: list = []
    skipped: list = []

    for c in candidates:
        if remaining_daily <= 0 or remaining_run <= 0:
            skipped.append(c)
        else:
            to_post.append(c)
            remaining_daily -= 1
            remaining_run   -= 1

    return to_post, skipped
