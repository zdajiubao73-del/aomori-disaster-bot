#!/bin/bash
# scripts/restore_state.sh
# bot-state ブランチから状態ファイルを復元する。
#
# 終了コードの意味:
#   0 （ブランチあり）… 復元成功
#   0 （ブランチなし）… 初回実行。何もしない。
#   1              … fetch 失敗・取り出し失敗・通信エラーなど。ジョブを失敗にする。

set -euo pipefail

mkdir -p state logs

# ── ブランチの存在を確認 ──────────────────────────────────────────
# git ls-remote --exit-code: 参照が見つからないとき終了コード 2 を返す
#   0  = ブランチあり → 復元する
#   2  = ブランチなし → 初回実行（何もしない）
#   その他 = 通信エラーなど → エラー終了してジョブを失敗にする
LS_RC=0
git ls-remote --exit-code --heads origin bot-state || LS_RC=$?

if [ "$LS_RC" -eq 2 ]; then
    echo "bot-state ブランチが存在しません（初回実行）"
    exit 0
elif [ "$LS_RC" -ne 0 ]; then
    echo "origin への接続に失敗しました（ls-remote exit=${LS_RC}）" >&2
    exit 1
fi

# ── ブランチあり → fetch・取り出し ────────────────────────────────
# fetch または取り出しに失敗した場合は set -e によりエラー終了する
git fetch origin bot-state
git show origin/bot-state:state/seen_ids.json    > state/seen_ids.json
git show origin/bot-state:state/daily_count.json > state/daily_count.json 2>/dev/null || true

echo "状態ファイルを bot-state ブランチから復元しました"
