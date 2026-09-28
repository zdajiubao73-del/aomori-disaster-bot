#!/usr/bin/env /usr/bin/python3
"""
test_state_scripts.py - 状態保存・復元スクリプトのテスト

一時ディレクトリの「空のベアリポジトリ」を相手にして 3 経路を検証する:
  (a) 初回: bot-state ブランチなし → ブランチを新規作成
  (b) 2回目: bot-state ブランチあり・状態変化あり → コミット・プッシュ
  (c) 変化なし: 状態ファイルが前回と同一 → コミットをスキップ

外部ネットワーク通信なし（ローカルのベアリポジトリを origin として使用）。
"""

import json
import os
import subprocess
import sys
import tempfile

# プロジェクトルート
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESTORE_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "restore_state.sh")
SAVE_SCRIPT    = os.path.join(PROJECT_ROOT, "scripts", "save_state.sh")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results = []


def ok(name, cond, info=""):
    status = PASS if cond else FAIL
    msg = f"  [{status}] {name}"
    if info:
        msg += f" — {info}"
    print(msg)
    _results.append((name, cond))


def run_script(script_path, cwd, env=None):
    """シェルスクリプトを実行して subprocess.CompletedProcess を返す"""
    result = subprocess.run(
        ["bash", script_path],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )
    return result


def git_cmd(args, cwd):
    """git コマンドを実行して subprocess.CompletedProcess を返す"""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result


def count_commits_on_branch(workspace, branch="bot-state"):
    """origin/bot-state ブランチのコミット数を返す（ブランチなしは 0）"""
    r = git_cmd(["fetch", "origin", branch], workspace)
    if r.returncode != 0:
        return 0
    r2 = git_cmd(["rev-list", "--count", f"origin/{branch}"], workspace)
    if r2.returncode != 0:
        return 0
    return int(r2.stdout.strip())


def read_seen_ids_from_branch(workspace):
    """origin/bot-state:state/seen_ids.json を読んでリストを返す"""
    r = git_cmd(["fetch", "origin", "bot-state"], workspace)
    if r.returncode != 0:
        return None
    r2 = git_cmd(["show", "origin/bot-state:state/seen_ids.json"], workspace)
    if r2.returncode != 0:
        return None
    try:
        return json.loads(r2.stdout)
    except Exception:
        return None


# ============================================================
print("\n== 状態保存スクリプト テスト ==")

with tempfile.TemporaryDirectory() as tmpdir:

    # ─── 環境構築 ───────────────────────────────────────────
    # remote: ベアリポジトリ（GitHub の役割を担う）
    remote = os.path.join(tmpdir, "remote.git")
    subprocess.run(["git", "init", "--bare", remote], capture_output=True, check=True)

    # workspace: 作業ディレクトリ（GitHub Actions ランナーの役割を担う）
    workspace = os.path.join(tmpdir, "workspace")
    os.makedirs(workspace)

    # git 初期設定（ローカル config のみ）
    subprocess.run(["git", "init", workspace],                                  capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name",  "test-runner"],              cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"],     cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote],                  cwd=workspace, capture_output=True, check=True)

    # main ブランチに初期コミットを作成・プッシュ（git fetch が機能するために必要）
    init_file = os.path.join(workspace, "README.md")
    with open(init_file, "w") as f:
        f.write("test")
    subprocess.run(["git", "add", "."],                                          cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"],                              cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "push", "-u", "origin", "HEAD:main"],                 cwd=workspace, capture_output=True, check=True)

    # state/ と logs/ ディレクトリを作成
    os.makedirs(os.path.join(workspace, "state"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "logs"),  exist_ok=True)

    # スクリプトに渡す環境変数
    # BOT_STATE_DIR を tmpdir 内のパスに固定してテスト間の競合を防ぐ
    base_env = os.environ.copy()
    base_env["BOT_STATE_DIR"] = os.path.join(tmpdir, "bot-state-wt")
    base_env["GIT_CONFIG_NOSYSTEM"] = "1"   # システム git config を無視

    # ─── (a) 初回: bot-state ブランチなし ───────────────────
    print("\n--- (a) 初回（bot-state ブランチなし）---")

    r = run_script(RESTORE_SCRIPT, workspace, env=base_env)
    ok("restore: 初回 → 終了コード 0",
       r.returncode == 0,
       f"rc={r.returncode}\nstdout={r.stdout[:100]}\nstderr={r.stderr[:100]}")
    ok("restore: 初回 → 「初回実行」メッセージ",
       "初回" in r.stdout,
       r.stdout.strip()[:100])

    # 初回の状態ファイルを作成
    seen_v1 = ["item_001", "item_002"]
    with open(os.path.join(workspace, "state", "seen_ids.json"), "w") as f:
        json.dump(seen_v1, f)

    r = run_script(SAVE_SCRIPT, workspace, env=base_env)
    ok("save: 初回 → 終了コード 0",
       r.returncode == 0,
       f"rc={r.returncode}\nstdout={r.stdout[:200]}\nstderr={r.stderr[:200]}")
    ok("save: 初回 → bot-state ブランチが作成された",
       count_commits_on_branch(workspace) >= 1,
       f"commits={count_commits_on_branch(workspace)}")

    # ─── (b) 2回目: bot-state ブランチあり・変化あり ────────
    print("\n--- (b) 2回目（bot-state ブランチあり・変化あり）---")

    r = run_script(RESTORE_SCRIPT, workspace, env=base_env)
    ok("restore: 2回目 → 終了コード 0",
       r.returncode == 0,
       f"rc={r.returncode}")
    ok("restore: 2回目 → 「復元しました」メッセージ",
       "復元しました" in r.stdout,
       r.stdout.strip()[:100])

    # seen_ids.json が復元されたか確認
    seen_path = os.path.join(workspace, "state", "seen_ids.json")
    if os.path.exists(seen_path):
        with open(seen_path) as f:
            restored = json.load(f)
        ok("restore: 2回目 → seen_ids.json の内容が一致",
           restored == seen_v1,
           f"restored={restored}")
    else:
        ok("restore: 2回目 → seen_ids.json の内容が一致", False, "file not found")

    # 新しいアイテムを追加して変化を作る
    seen_v2 = seen_v1 + ["item_003"]
    with open(seen_path, "w") as f:
        json.dump(seen_v2, f)

    commits_before = count_commits_on_branch(workspace)
    r = run_script(SAVE_SCRIPT, workspace, env=base_env)
    ok("save: 2回目（変化あり）→ 終了コード 0",
       r.returncode == 0,
       f"rc={r.returncode}\nstdout={r.stdout[:200]}\nstderr={r.stderr[:200]}")
    commits_after = count_commits_on_branch(workspace)
    ok("save: 2回目（変化あり）→ コミットが増えた",
       commits_after > commits_before,
       f"before={commits_before}, after={commits_after}")

    # bot-state ブランチの seen_ids.json が更新されているか確認
    saved_ids = read_seen_ids_from_branch(workspace)
    ok("save: 2回目 → bot-state ブランチの seen_ids.json が更新された",
       saved_ids == seen_v2,
       f"saved={saved_ids}")

    # ─── (c) 変化なし: コミットをスキップ ───────────────────
    print("\n--- (c) 変化なし（コミットをスキップ）---")

    # restore で最新状態を取得（seen_v2 が workspace に書き込まれる）
    r = run_script(RESTORE_SCRIPT, workspace, env=base_env)
    ok("restore: 変化なし確認前 → 終了コード 0",
       r.returncode == 0,
       f"rc={r.returncode}")

    # last_run.txt はすでに今日の日付で作成済みなので変化なしのはず
    # seen_ids.json もそのまま（変更しない）
    commits_before2 = count_commits_on_branch(workspace)
    r = run_script(SAVE_SCRIPT, workspace, env=base_env)
    ok("save: 変化なし → 終了コード 0",
       r.returncode == 0,
       f"rc={r.returncode}\nstdout={r.stdout[:200]}\nstderr={r.stderr[:200]}")
    commits_after2 = count_commits_on_branch(workspace)
    ok("save: 変化なし → コミットをスキップ（件数が増えない）",
       commits_after2 == commits_before2,
       f"before={commits_before2}, after={commits_after2}")
    ok("save: 変化なし → 「スキップ」メッセージ",
       "スキップ" in r.stdout or "変化なし" in r.stdout,
       r.stdout.strip()[:100])

# ─── (d) ブランチなし → 初回として成功（ls-remote 方式） ────────
print("\n--- (d) ブランチなし → 初回として成功（ls-remote 方式）---")

with tempfile.TemporaryDirectory() as tmpdir_d:
    remote_d = os.path.join(tmpdir_d, "remote.git")
    subprocess.run(["git", "init", "--bare", remote_d], capture_output=True, check=True)

    ws_d = os.path.join(tmpdir_d, "workspace")
    os.makedirs(ws_d)
    subprocess.run(["git", "init", ws_d],                                          capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name",  "test"],    cwd=ws_d,           capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.invalid"], cwd=ws_d,       capture_output=True, check=True)
    subprocess.run(["git", "remote", "add", "origin", remote_d], cwd=ws_d,         capture_output=True, check=True)
    os.makedirs(os.path.join(ws_d, "state"), exist_ok=True)
    os.makedirs(os.path.join(ws_d, "logs"),  exist_ok=True)

    env_d = os.environ.copy()
    env_d["GIT_CONFIG_NOSYSTEM"] = "1"

    r = run_script(RESTORE_SCRIPT, ws_d, env=env_d)
    ok("(d) ブランチなし → 終了コード 0",
       r.returncode == 0,
       f"rc={r.returncode}\nstdout={r.stdout[:100]}\nstderr={r.stderr[:100]}")
    ok("(d) ブランチなし → 「初回実行」メッセージ",
       "初回" in r.stdout,
       r.stdout.strip()[:100])

# ─── (e) origin 接続エラー → restore が失敗 ────────────────────
print("\n--- (e) origin 接続エラー → restore が失敗 ---")

with tempfile.TemporaryDirectory() as tmpdir_e:
    ws_e = os.path.join(tmpdir_e, "workspace")
    os.makedirs(ws_e)
    subprocess.run(["git", "init", ws_e],                                          capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name",  "test"],    cwd=ws_e,           capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.invalid"], cwd=ws_e,       capture_output=True, check=True)
    # 存在しないパスを origin に設定して接続エラーを再現する
    subprocess.run(["git", "remote", "add", "origin",
                    "/nonexistent/path/does/not/exist.git"],    cwd=ws_e,           capture_output=True, check=True)
    os.makedirs(os.path.join(ws_e, "state"), exist_ok=True)
    os.makedirs(os.path.join(ws_e, "logs"),  exist_ok=True)

    env_e = os.environ.copy()
    env_e["GIT_CONFIG_NOSYSTEM"] = "1"

    r = run_script(RESTORE_SCRIPT, ws_e, env=env_e)
    ok("(e) 接続エラー → 終了コード ≠ 0",
       r.returncode != 0,
       f"rc={r.returncode}\nstdout={r.stdout[:100]}\nstderr={r.stderr[:100]}")

# ─── スクリプトの存在確認 ────────────────────────────────────
print("\n--- スクリプトの存在確認 ---")
ok("scripts/restore_state.sh が存在する",   os.path.exists(RESTORE_SCRIPT))
ok("scripts/save_state.sh が存在する",      os.path.exists(SAVE_SCRIPT))

# リトライロジックがスクリプト内に記述されているか確認
with open(SAVE_SCRIPT) as f:
    _save_content = f.read()
ok("save_state.sh にリトライロジックが含まれる",
   "for attempt in 1 2 3" in _save_content,
   "retry loop check")
ok("save_state.sh に sleep 5 が含まれる",
   "sleep 5" in _save_content,
   "sleep check")
ok("save_state.sh に --orphan -b の修正済みコマンドが含まれる",
   "--orphan -b bot-state" in _save_content,
   "orphan fix check")

# ─── (f) bot.yml の条件確認 ─────────────────────────────────
print("\n--- (f) bot.yml の条件確認 ---")
BOT_YML = os.path.join(PROJECT_ROOT, ".github", "workflows", "bot.yml")
with open(BOT_YML) as f:
    _yml_content = f.read()
ok("(f) bot.yml に restore ステップの id が含まれる",
   "id: restore" in _yml_content,
   "id: restore check")
ok("(f) bot.yml の save ステップに steps.restore.outcome 条件が含まれる",
   "steps.restore.outcome" in _yml_content,
   "restore.outcome check")

# ─── サンプルの既定オフ確認 ──────────────────────────────────
print("\n--- サンプルの既定オフ確認 ---")
BOT_PY = os.path.join(PROJECT_ROOT, "aomori_bot.py")
with open(BOT_PY) as f:
    _bot_content = f.read()
ok("aomori_bot.py に INCLUDE_SAMPLES 環境変数ゲートが含まれる",
   'os.environ.get("INCLUDE_SAMPLES"' in _bot_content,
   "INCLUDE_SAMPLES env check")
ok("INCLUDE_SAMPLES のデフォルトは false（条件 == 'true' のみ有効）",
   '"true"' in _bot_content and 'os.environ.get("INCLUDE_SAMPLES", "")' in _bot_content,
   "default-off check")
ok("restore_state.sh に ls-remote --exit-code が含まれる",
   "ls-remote --exit-code" in open(RESTORE_SCRIPT).read(),
   "ls-remote check")

# ─── 結果集計 ────────────────────────────────────────────────
print()
total  = len(_results)
passed = sum(1 for _, r in _results if r)
failed = total - passed
bar = "-" * 40
print(bar)
print(f"テスト結果: {passed}/{total} PASS  ({failed} FAIL)")
print(bar)

sys.exit(0 if failed == 0 else 1)
