#!/bin/bash
# scripts/save_state.sh
# 状態ファイルを bot-state ブランチに保存する。
# 変化がなければコミットをスキップする。
# ハートビート（last_run.txt）は UTC 日付が変わったときだけ更新する
# （毎回更新すると 10 分おきに不要なコミットが 1 日 144 件発生するため）。
# プッシュ失敗時は 5 秒あけて最大 3 回リトライする。
#
# 環境変数:
#   BOT_STATE_DIR  ワークツリーのパス（既定: /tmp/bot-state）
#                  テスト時にこの変数を上書きして別パスを使用できる

set -euo pipefail

BOT_STATE_DIR="${BOT_STATE_DIR:-/tmp/bot-state}"
WORKSPACE_DIR="$(pwd)"

git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# 残存ワークツリーを事前クリーンアップ（前回の異常終了対策）
git worktree prune 2>/dev/null || true
if [ -d "$BOT_STATE_DIR" ]; then
    rm -rf "$BOT_STATE_DIR"
fi

# ── bot-state ブランチのワークツリーを設定 ──
if git fetch origin bot-state 2>/dev/null; then
    git worktree add "$BOT_STATE_DIR" origin/bot-state
else
    # 初回：孤立ブランチを作成
    # git 2.50 以降は --orphan と commit-ish を同時指定できない。
    # 正しい構文: git worktree add --orphan -b <branch> <path>
    git worktree add --orphan -b bot-state "$BOT_STATE_DIR"
fi

mkdir -p "$BOT_STATE_DIR/state"

# 状態ファイルをコピー
[ -f state/seen_ids.json    ] && cp state/seen_ids.json    "$BOT_STATE_DIR/state/"
[ -f state/daily_count.json ] && cp state/daily_count.json "$BOT_STATE_DIR/state/"

# ハートビート：UTC 日付が変わったときだけ last_run.txt を更新
# 60 日間コミットがないと GitHub Actions のスケジュールが停止するため、
# 1 日 1 コミットを確保する（再有効化は README 参照）
TODAY=$(date -u +"%Y-%m-%d")
LAST_RUN_FILE="$BOT_STATE_DIR/state/last_run.txt"
if [ ! -f "$LAST_RUN_FILE" ] || ! grep -q "^${TODAY}" "$LAST_RUN_FILE" 2>/dev/null; then
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$LAST_RUN_FILE"
    echo "ハートビート更新: ${TODAY}"
fi

cd "$BOT_STATE_DIR"
git add state/

if git diff --cached --quiet; then
    echo "状態に変化なし（コミットをスキップ）"
    cd "$WORKSPACE_DIR"
    git worktree remove "$BOT_STATE_DIR" 2>/dev/null || true
    exit 0
fi

git commit -m "chore(state): update bot state $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# プッシュ（失敗時は 5 秒あけて最大 3 回リトライ）
PUSH_OK=false
for attempt in 1 2 3; do
    if git push origin HEAD:bot-state; then
        echo "bot-state ブランチに状態を保存しました"
        PUSH_OK=true
        break
    fi
    if [ "$attempt" -lt 3 ]; then
        echo "プッシュ失敗（${attempt}/3回目）。5秒後に再試行します..."
        sleep 5
    fi
done

cd "$WORKSPACE_DIR"
git worktree remove "$BOT_STATE_DIR" 2>/dev/null || true

if [ "$PUSH_OK" != "true" ]; then
    echo "プッシュが3回失敗しました。ジョブを失敗にします。" >&2
    exit 1
fi
