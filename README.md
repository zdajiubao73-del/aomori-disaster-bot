# 青森県災害情報ボット

青森県に関係する気象警報・注意報・地震・津波の情報を、
気象庁の発表をもとに X (Twitter) へ自動投稿するボットです。

---

## 機能

- 気象庁防災情報 XML フィードを 10 分おきに確認
- 青森県に関係する情報のみを抽出して投稿
  - 気象特別警報・警報・注意報
  - 土砂災害警戒情報
  - 記録的短時間大雨情報
  - 竜巻注意情報
  - 津波警報・注意報
  - 地震情報（気象庁 VXSE53、青森県内最大震度 3 以上）
- 注意報の解除は投稿しない（警報の解除は投稿する）
- 過去 90 分を超えた情報は投稿しない（鮮度フィルタ）
- 1 日 10 件・1 回の実行で 3 件を上限とする（設定で変更可）

---

## 仕組み

```
GitHub Actions (10 分おき)
    │
    ├─ 状態復元（bot-state ブランチ）
    ├─ 気象庁 XML フィード取得
    ├─ 安全装置（鮮度・文面・上限チェック）
    ├─ X API v2 で投稿（POST /2/tweets）
    └─ 状態保存（bot-state ブランチ）
```

出典：[気象庁防災情報 XML](https://www.data.jma.go.jp/developer/xml/feed/)

---

## セットアップ

### 必要なもの

- GitHub アカウント（Actions が使えるリポジトリ）
- X Developer アカウントと従量課金クレジット
  （投稿 1 件あたり約 $0.015。URL 付き投稿は約 $0.20）

### 1. リポジトリの準備

```bash
git clone <このリポジトリ>
cd aomori-disaster-bot
```

### 2. Secrets の登録（GitHub リポジトリ設定）

Settings → Secrets and variables → Actions → Secrets

| 名前 | 内容 |
|------|------|
| `X_API_KEY` | X アプリの API Key |
| `X_API_SECRET` | X アプリの API Key Secret |
| `X_ACCESS_TOKEN` | アクセストークン |
| `X_ACCESS_TOKEN_SECRET` | アクセストークンシークレット |

### 3. Variables の登録（GitHub リポジトリ設定）

Settings → Secrets and variables → Actions → Variables

| 名前 | 既定値 | 説明 |
|------|--------|------|
| `POST_MODE` | `dry` | `dry` = 投稿しない　`live` = 実際に投稿 |
| `POST_MAX_AGE_MIN` | `90` | この分数より古い情報は投稿しない |
| `POST_DAILY_LIMIT` | `10` | 1 日の投稿上限件数 |
| `POST_PER_RUN_LIMIT` | `3` | 1 回の実行の投稿上限件数 |
| `ENABLE_P2P` | `false` | P2P 地震情報の利用（`true` で有効） |

### 4. 本番投稿の開始

Variables の `POST_MODE` を `dry` から `live` に変更するだけで投稿が始まります。
`dry` に戻すと投稿が止まります（停止スイッチ）。

---

## 安全に関する注意事項

- このボットの情報は、**気象庁や自治体の公式情報の代替ではありません**。
- 配信が遅れる場合や、情報が欠ける場合があります。
- 緊急時は必ず気象庁・自治体の公式サイトを確認してください。

---

## GitHub Actions が停止した場合の再有効化

GitHub はリポジトリへのコミットが 60 日間ない場合、スケジュール実行（`schedule`）を自動停止します。
このボットは毎日 1 回 bot-state ブランチにコミットしてその停止を防ぎます（ハートビート）。
万が一停止してしまった場合は、以下の手順で再有効化してください。

1. GitHub リポジトリ → Actions タブを開く
2. 左側の「Aomori Disaster Bot」をクリック
3. 画面上部の「Enable workflow」ボタンをクリック

または、リポジトリに空コミットを push するだけでも再有効化されます。

```bash
git commit --allow-empty -m "chore: re-enable GitHub Actions schedule"
git push
```

---

## ローカル実行

```bash
# ドライラン（既定）
/usr/bin/python3 aomori_bot.py

# ドライラン + 疑似電文を含める（sample_data/ の動作確認用）
# 公開リポジトリの Actions ログは誰でも読めるため、既定ではオフです。
INCLUDE_SAMPLES=true /usr/bin/python3 aomori_bot.py

# ドライラン・状態リセット
/usr/bin/python3 aomori_bot.py --reset

# テスト実行
/usr/bin/python3 tests/test_filters.py
/usr/bin/python3 tests/test_post_safety.py
```

---

## 設定ファイル

`.env.example` を `.env` にコピーして API キーを設定すると、ローカルで `live` モードを試せます。
`.env` は `.gitignore` 済みで、絶対にコミットしないでください。

---

## 出典と免責

- 気象情報の出典：気象庁（気象庁防災情報 XML / https://www.data.jma.go.jp/developer/xml/）
- このボットは気象庁の発表情報をもとに自動生成・配信しています。
- 情報の正確性・完全性・適時性を保証するものではありません。
- 投稿内容に誤りや問題がある場合は、GitHub の Issue でお知らせください。

