#!/usr/bin/env /usr/bin/python3
"""
aomori_bot.py - 青森県災害情報ボット
気象庁防災情報 XML を取得し、青森県に関係する警報・地震・津波情報を
X (Twitter) に自動投稿する。

POST_MODE=dry（既定）のときは投稿せずログ出力のみ。
POST_MODE=live のとき X API v2 で実際に投稿する。

使い方:
  /usr/bin/python3 aomori_bot.py              # 通常実行
  /usr/bin/python3 aomori_bot.py --reset      # seen_ids.json をリセットして実行
  POST_MODE=live /usr/bin/python3 aomori_bot.py  # 本番投稿

出典：気象庁防災情報XML / P2P地震情報
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import safety
import x_poster

# ===== 設定 =====
JST      = timezone(timedelta(hours=9))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
LOG_DIR   = os.path.join(BASE_DIR, "logs")

USER_AGENT    = "aomori-disaster-bot/1.0 (+https://github.com/zdajiubao73-del/aomori-disaster-bot)"
JMA_EXTRA_L   = "https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml"
JMA_EQVOL_L   = "https://www.data.jma.go.jp/developer/xml/feed/eqvol_l.xml"
P2P_QUAKE_URL = "https://api.p2pquake.net/v2/history?codes=551&limit=100"

AOMORI_AREA_CODE = "020000"
QUAKE_MIN_INT_STR = "3"   # JMA 震度 3 以上
P2P_MIN_SCALE     = 30    # P2P scale 値で震度 3 相当

# 対象プロダクトコード
#
# VPWW53 のみを気象警報・注意報の投稿元とする。
#
# 除外した種別と理由（資料: https://xml.kishou.go.jp/tec_material.html 表1.1）:
#   VPWW54: 地方気象情報（H27 フォーマット）… VPWW53 と同一内容の旧フォーマット版。
#           実測で 020000 に対して VPWW53 と同数・同タイミングで発行を確認。
#   VPWW55: 大雨警報・注意報（R06 フォーマット）… VPWW53 の大雨種別を分割した電文。
#           VPWW53 に同じ内容が含まれる。
#   VPWW56: 土砂崩れ注意報（R06 フォーマット）… VPWW53 の土砂種別を分割した電文。
#   VPWW58: 暴風・強風注意報（R06 フォーマット）… VPWW53 の暴風種別を分割した電文。
#   VPWW59: 波浪注意報（R06 フォーマット）… VPWW53 の波浪種別を分割した電文。
#   VPWW61: 竜巻・雷注意報（R06 フォーマット）… VPWW53 の雷/竜巻種別を分割した電文。
#           VPWW53 が「青森県では、落雷に注意してください。」を含む際に並行発行を確認。
#   VPHW62: 存在しないコード（実フィードで確認済み）。旧資料の誤記とみなして除外。
#
# VXWW50/VPOA50/VPHW50/VPHW51 は警報・注意報系とは別カテゴリの専用電文のため継続。
#
# 竜巻注意情報のコードについて:
#   VPHW50: 竜巻注意情報（無目撃）       … 全国フィードで実在を確認（2026-09-28 時点）。
#   VPHW51: 竜巻注意情報（目撃情報付き）… VPHW50 と同時刻に同一ヘッドラインで発行される。
#           テキストが同一のため _dedup_by_text で 1 件に集約する。
WEATHER_PRODUCTS = {
    "VPWW53",    # 府県気象情報（気象特別警報・警報・注意報）― 青森県の主たる電文
    "VXWW50",    # 土砂災害警戒情報
    "VPOA50",    # 記録的短時間大雨情報
    "VPHW50",    # 竜巻注意情報（無目撃）
    "VPHW51",    # 竜巻注意情報（目撃情報付き）
}
TSUNAMI_PRODUCTS = {"VTSE41", "VTSE51", "VTSE52"}
QUAKE_PRODUCTS   = {"VXSE53", "VXSE51", "VXSE52"}

AOMORI_KEYWORDS = ["青森", "津軽", "下北", "三八上北"]


# ===== ログ初期化 =====

def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    today    = datetime.now(JST).strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"{today}.log")
    fmt      = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ===== 状態管理 =====

def _seen_path():
    return os.path.join(STATE_DIR, "seen_ids.json")


def load_seen_ids() -> set:
    try:
        with open(_seen_path(), encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen_ids(seen_ids: set) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_seen_path(), "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=2)


def reset_seen_ids() -> None:
    path = _seen_path()
    if os.path.exists(path):
        os.remove(path)
        logging.info("seen_ids.json をリセットしました")


# ===== HTTP ユーティリティ =====

def fetch_url(url: str, if_modified_since: str = "") -> tuple:
    """
    URL を取得して (data_bytes, last_modified) を返す。
    304 Not Modified なら (None, None) を返す。
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if if_modified_since:
        req.add_header("If-Modified-Since", if_modified_since)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(), resp.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, None
        raise


# ===== XML ローカル名ヘルパー =====

def local(tag: str) -> str:
    """{namespace}localname → localname"""
    return tag.split("}")[-1] if "}" in tag else tag


def find_text(root, *local_names) -> str:
    """
    root 以下を BFS で検索し、local_names のいずれかに
    最初に一致するテキストを返す。
    """
    for elem in root.iter():
        if local(elem.tag) in local_names and elem.text and elem.text.strip():
            return elem.text.strip()
    return ""


# ===== Atom フィード解析 =====

ATOM_NS = "http://www.w3.org/2005/Atom"


def parse_atom_feed(data: bytes) -> list:
    root    = ET.fromstring(data)
    entries = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        link     = entry.find(f"{{{ATOM_NS}}}link")
        href     = link.get("href") if link is not None else ""
        entry_id = entry.findtext(f"{{{ATOM_NS}}}id") or href
        title    = entry.findtext(f"{{{ATOM_NS}}}title") or ""
        updated  = entry.findtext(f"{{{ATOM_NS}}}updated") or ""
        entries.append({
            "title":   title,
            "href":    href,
            "id":      entry_id,
            "updated": updated,
        })
    return entries


def extract_product_area(url: str) -> tuple:
    """
    URL: .../YYYYMMDDHHMMSS_0_VPWW53_020000.xml
    → (product_code, area_code)
    """
    base  = url.rsplit("/", 1)[-1].replace(".xml", "")
    parts = base.split("_")
    if len(parts) >= 4:
        return parts[-2], parts[-1]
    return "", ""


# ===== 投稿文生成ユーティリティ =====

def format_jst(dt_str: str) -> str:
    """ISO8601 or 'YYYY/MM/DD HH:MM' を 'M月D日H時M分' に変換"""
    if not dt_str:
        return ""
    try:
        if "T" in dt_str:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(dt_str[:16], "%Y/%m/%d %H:%M").replace(tzinfo=JST)
        return dt.astimezone(JST).strftime("%-m月%-d日%-H時%-M分")
    except Exception:
        return dt_str[:16]


def truncate140(text: str) -> str:
    """全角 140 文字以内（超過時は末尾を '…' で切り詰め）"""
    if len(text) <= 140:
        return text
    return text[:139] + "…"


def classify_weather_label(product: str, headline: str) -> str:
    """プロダクトコードとヘッドラインからラベルを決定"""
    if product == "VXWW50":
        return "土砂災害警戒情報"
    if product == "VPOA50":
        return "記録的短時間大雨情報"
    if product in {"VPHW50", "VPHW51"}:
        return "竜巻注意情報"
    if "特別警報" in headline:
        return "気象特別警報"
    if "警報" in headline:
        return "気象警報"
    return "気象注意報"


def intensity_to_str(int_str: str) -> str:
    """JMA 震度文字列を日本語表記に変換"""
    return {"5-": "5弱", "5+": "5強", "6-": "6弱", "6+": "6強"}.get(int_str, int_str)


def int_level(int_str: str) -> float:
    """震度文字列を比較可能な数値に変換"""
    mapping = {
        "1": 1, "2": 2, "3": 3, "4": 4,
        "5-": 5, "5+": 5.5, "6-": 6, "6+": 6.5, "7": 7,
    }
    return mapping.get(int_str, 0)


def p2p_scale_to_str(scale: int) -> str:
    """P2P scale → 震度文字列"""
    tbl = {
        10: "1", 20: "2", 30: "3", 40: "4",
        45: "5弱", 50: "5強", 55: "6弱", 60: "6強", 70: "7",
    }
    return tbl.get(scale, str(scale))


# ===== VPWW53 Kind/Status チェック =====

def _has_new_kind(root) -> bool:
    """
    Body/Warning 内の Kind の (Name, Status) ペアを検査し、投稿すべき変化があれば True。

    判定ルール（降順で評価）:
      - Status が「発表」「移行」など既定のスキップ対象以外 → True（投稿する）
      - Status が「解除」かつ Kind 名が「警報」を含む（特別警報含む）→ True（警報解除は投稿）
      - Status が「解除」かつ Kind 名が「注意報」のみ → スキップ（注意報解除は見送り）
      - Status が「継続」または「発表警報・注意報はなし」→ スキップ
      - 上記以外が残らない（全件スキップ）→ False（投稿しない）

    Kind 要素が見つからない場合は True（安全側：投稿する）。

    対象要素: Body > Warning > Item > Kind > (Name, Status)
    """
    kind_entries: list[tuple[str, str]] = []   # [(kind_name, status), ...]

    for elem in root.iter():
        if local(elem.tag) != "Warning":
            continue
        # Warning 配下の Kind 要素を収集
        for kind_elem in elem.iter():
            if local(kind_elem.tag) != "Kind":
                continue
            kind_name  = ""
            kind_status = ""
            for child in kind_elem:
                t = local(child.tag)
                if t == "Name" and child.text:
                    kind_name = child.text.strip()
                elif t == "Status" and child.text:
                    kind_status = child.text.strip()
            if kind_status:
                kind_entries.append((kind_name, kind_status))

    if not kind_entries:
        return True   # Kind/Status 情報なし → 安全側（投稿する）

    _skip = {"継続", "発表警報・注意報はなし"}

    for kind_name, status in kind_entries:
        if status in _skip:
            continue
        if status == "解除":
            # 警報（特別警報含む）の解除は投稿する。
            # 判定: 種別名に「警報」が含まれ、かつ「注意報」が含まれない
            # 例: 「大雨警報」→ 投稿、「濃霧注意報」→ 見送り
            if "警報" in kind_name and "注意報" not in kind_name:
                return True   # 警報解除 → 投稿
            continue          # 注意報解除 → 見送り
        # 「発表」「警報から注意報へ移行」など上記以外は新規扱い
        return True

    return False


# ===== 解除フィルタ =====

def _is_cancel_to_skip(headline: str, info_type: str) -> bool:
    """
    注意報の解除なら True（投稿しない）、警報の解除なら False（投稿する）。
    解除でなければ False（通常処理）。

    判定ロジック:
      「解除」が info_type またはヘッドラインに含まれる = 解除メッセージ
      ヘッドラインから「注意報」を除いた後に「警報」が残る = 警報の解除 → 投稿
      残らない = 注意報のみの解除 → スキップ
    """
    is_cancel = "解除" in (info_type or "") or "解除" in headline
    if not is_cancel:
        return False
    # 「注意報」を除いたヘッドラインに「警報」が残るか確認
    # 例: "大雨警報が解除" → 残る → 投稿
    #     "大雨注意報が解除" → 残らない → スキップ
    #     "特別警報が解除"  → 残る → 投稿
    headline_sans_chui = headline.replace("注意報", "")
    return "警報" not in headline_sans_chui


# ===== 気象警報・注意報 XML 解析 =====

def _parse_weather_xml_full(data: bytes, product: str) -> tuple:
    """
    内部実装：VPWW53 等を解析して (text, info_type, label, headline) を返す。
    青森県に関係ない場合・スキップ対象は (None, info_type, label, headline) を返す。

    Returns
    -------
    (post_text_or_None, info_type, label, headline)
      headline : クールダウンキーとして使う生テキスト（XML の Headline/Text）。
                 投稿しない場合も返す（クールダウン記録不要なので呼び出し側で無視する）。
    """
    root = ET.fromstring(data)

    title       = find_text(root, "Title")
    report_dt   = find_text(root, "ReportDateTime")
    info_type   = find_text(root, "InfoType") or "発表"
    headline    = find_text(root, "Text")

    # Headline 直下の Text を探す（フォールバック）
    if not headline:
        for elem in root.iter():
            if local(elem.tag) == "Headline":
                headline = find_text(elem, "Text")
                if headline:
                    break

    if not headline:
        return None, info_type, "", ""

    # 青森県関連チェック
    if not any(kw in headline for kw in AOMORI_KEYWORDS):
        if "青森" not in title:
            return None, info_type, "", ""

    label  = classify_weather_label(product, headline)

    # 注意報の解除はスキップ
    if _is_cancel_to_skip(headline, info_type):
        return None, info_type, label, headline

    # VPWW53: Kind/Status が全て「継続」または「発表警報・注意報はなし」なら
    # 状態変化なし（定時再発表・同一内容の再送）→ 投稿しない
    if product == "VPWW53" and not _has_new_kind(root):
        logging.info(
            f"  継続のみ: VPWW53 の全 Kind が継続ステータス → 投稿しない"
            f"（headline={headline[:40]}）"
        )
        return None, info_type, label, headline

    dt_str = format_jst(report_dt)
    prefix = f"{dt_str} " if dt_str else ""

    # 「出典：気象庁」を必ず末尾に残し、140字を超える場合は句点「。」で切り詰める。
    # 句点がない場合は 139字 + 「…」の省略形にする。
    _CITATION = "\n出典：気象庁"
    body = f"【{label}】{prefix}{headline}"
    if len(body) + len(_CITATION) <= 140:
        text = body + _CITATION
    else:
        max_body = 140 - len(_CITATION)
        last_period = body[:max_body].rfind("。")
        if last_period > 0:
            text = body[:last_period + 1] + _CITATION
        else:
            text = body[:max_body - 1] + "…" + _CITATION

    return text, info_type, label, headline


def parse_weather_xml(data: bytes, product: str):
    """
    公開 API（後方互換）: 投稿文を返す。青森県関係なし・不要な場合は None。
    """
    text, _, _, _ = _parse_weather_xml_full(data, product)
    return text


# ===== 地震 XML 解析 (VXSE53) =====

def parse_earthquake_xml(data: bytes):
    """
    VXSE53 地震 XML を解析。青森県で震度 3 以上の場合のみ投稿文を返す。
    """
    root = ET.fromstring(data)

    origin_time = find_text(root, "OriginTime")

    hypo_name = ""
    for elem in root.iter():
        if local(elem.tag) == "Hypocenter":
            hypo_name = find_text(elem, "Name")
            if hypo_name:
                break

    magnitude = ""
    for elem in root.iter():
        if local(elem.tag) == "Magnitude" and elem.text:
            magnitude = elem.text.strip()
            break

    aomori_max_int = None
    for pref_elem in root.iter():
        if local(pref_elem.tag) != "Pref":
            continue
        pref_name = find_text(pref_elem, "Name")
        if "青森" not in pref_name:
            continue
        for child in pref_elem.iter():
            if local(child.tag) == "MaxInt" and child.text:
                candidate = child.text.strip()
                if int_level(candidate) > int_level(aomori_max_int or "0"):
                    aomori_max_int = candidate
                break

    if not aomori_max_int:
        return None
    if int_level(aomori_max_int) < 3:
        return None

    dt_str   = format_jst(origin_time)
    int_disp = intensity_to_str(aomori_max_int)
    text = (
        f"【地震情報】{dt_str}頃 {hypo_name}を震源とする地震"
        f"（M{magnitude}）が発生しました。"
        f"青森県内の最大震度は{int_disp}です。\n出典：気象庁"
    )
    return truncate140(text)


# ===== 津波 XML 解析 =====

def parse_tsunami_xml(data: bytes):
    """VTSE41/51/52 を解析。青森県関連のみ返す。"""
    root     = ET.fromstring(data)
    headline = find_text(root, "Text")
    report_dt = find_text(root, "ReportDateTime")

    if not headline:
        return None

    if not any(kw in headline for kw in AOMORI_KEYWORDS + ["東北", "太平洋", "日本海"]):
        return None

    dt_str = format_jst(report_dt)
    prefix = f"{dt_str} " if dt_str else ""
    text   = f"【津波情報】{prefix}{headline}\n出典：気象庁"
    return truncate140(text)


# ===== P2P 地震情報 =====

def process_p2p_quakes(quakes: list, seen_ids: set, enable_p2p: bool = True) -> list:
    """
    P2P 地震情報 API から青森県で震度 3 以上の地震を抽出。

    Parameters
    ----------
    enable_p2p : False のとき何も処理せず空リストを返す（既定: True）
                 実行時は safety.get_config() の enable_p2p で制御される。
    """
    if not enable_p2p:
        return []

    posts = []
    for q in quakes:
        qid = "p2p_" + str(q.get("id", ""))
        if qid in seen_ids:
            continue

        points     = q.get("points", [])
        aomori_pts = [p for p in points if "青森" in p.get("pref", "")]
        if not aomori_pts:
            seen_ids.add(qid)
            continue

        max_scale = max(p.get("scale", 0) for p in aomori_pts)
        if max_scale < P2P_MIN_SCALE:
            seen_ids.add(qid)
            continue

        eq    = q.get("earthquake", {})
        hypo  = eq.get("hypocenter", {})
        name  = hypo.get("name", "不明")
        mag   = hypo.get("magnitude", "不明")
        etime = eq.get("time", "")

        int_str = p2p_scale_to_str(max_scale)
        dt_str  = format_jst(etime)
        text = (
            f"【地震情報】{dt_str}頃 {name}を震源とする地震"
            f"（M{mag}）が発生しました。"
            f"青森県内の最大震度は{int_str}です。\n出典：気象庁"
        )
        posts.append({
            "id":           qid,
            "type":         "earthquake_p2p",
            "text":         truncate140(text),
            "source":       "P2P地震情報",
            "updated":      etime,
            "headline_key": "",   # 地震は内容が毎回異なるためクールダウン対象外
        })
        # P2P は関数内で即座に seen_ids に追加する
        # （同一実行内の重複を防ぐため。JMA フィードとは異なる設計）
        seen_ids.add(qid)
    return posts


# ===== フィード処理 =====

def process_weather_feed(seen_ids: set) -> list:
    """
    extra_l.xml を取得し、青森県の気象警報・注意報の投稿候補を返す。

    注意: 投稿候補は seen_ids に追加しない（run() で投稿成功後に追加する）。
    投稿不要と判断したエントリは seen_ids に追加して次回スキップする。
    """
    posts = []
    logging.info("Fetching extra_l.xml ...")
    data, _ = fetch_url(JMA_EXTRA_L)
    entries  = parse_atom_feed(data)
    logging.info(f"  {len(entries)} エントリ取得")

    for entry in entries:
        eid = entry["id"]
        if eid in seen_ids:
            continue

        product, area = extract_product_area(entry["href"])

        # 気象警報・注意報は青森エリアコードのみ処理
        if product in WEATHER_PRODUCTS:
            if area != AOMORI_AREA_CODE:
                seen_ids.add(eid)
                continue
        # 津波・地震はエリアコード関係なし（内容で判定）
        elif product not in (TSUNAMI_PRODUCTS | QUAKE_PRODUCTS):
            seen_ids.add(eid)
            continue

        logging.info(f"  処理中: {entry['href']}")
        try:
            xml_data, _ = fetch_url(entry["href"])
        except Exception as e:
            logging.warning(f"  取得失敗（スキップ）: {e}")
            seen_ids.add(eid)
            continue

        post_text = None
        headline_key = ""
        if product in WEATHER_PRODUCTS:
            # _parse_weather_xml_full を直接呼び出してクールダウンキーも取得する
            post_text, _, _, headline_key = _parse_weather_xml_full(xml_data, product)
        elif product in TSUNAMI_PRODUCTS:
            post_text = parse_tsunami_xml(xml_data)
        # 地震は eqvol_l.xml で別途処理

        if post_text:
            # 投稿候補として返す（seen_ids にはまだ追加しない）
            posts.append({
                "id":           eid,
                "type":         product,
                "text":         post_text,
                "source":       "JMA XML (extra_l)",
                "updated":      entry["updated"],
                "headline_key": headline_key,
            })
        else:
            # 投稿不要 → 即座にスキップ済みとして記録
            seen_ids.add(eid)

    return posts


def process_eqvol_feed(seen_ids: set) -> list:
    """
    eqvol_l.xml を取得し、青森県で震度 3 以上の地震の投稿候補を返す。

    注意: 投稿候補は seen_ids に追加しない（run() で投稿成功後に追加する）。
    """
    posts = []
    logging.info("Fetching eqvol_l.xml ...")
    data, _ = fetch_url(JMA_EQVOL_L)
    entries  = parse_atom_feed(data)
    logging.info(f"  {len(entries)} エントリ取得")

    for entry in entries:
        eid = entry["id"]
        if eid in seen_ids:
            continue

        product, _ = extract_product_area(entry["href"])

        # 既定では VXSE53（震源・震度情報）のみ。VXSE51/52 は除外
        if product != "VXSE53":
            seen_ids.add(eid)
            continue

        logging.info(f"  処理中: {entry['href']}")
        try:
            xml_data, _ = fetch_url(entry["href"])
        except Exception as e:
            logging.warning(f"  取得失敗（スキップ）: {e}")
            seen_ids.add(eid)
            continue

        post_text = parse_earthquake_xml(xml_data)

        if post_text:
            posts.append({
                "id":           eid,
                "type":         product,
                "text":         post_text,
                "source":       "JMA XML (eqvol_l)",
                "updated":      entry["updated"],
                "headline_key": "",   # 地震は内容が毎回異なるためクールダウン対象外
            })
        else:
            seen_ids.add(eid)

    return posts


# ===== サンプルデータ処理（ドライラン・テスト用） =====

def process_sample_data(seen_ids: set) -> list:
    """
    sample_data/ にある疑似 XML を処理して投稿予定文を生成する。
    dry-run モードでの確認・テスト用。本番（live）モードでは呼ばれない。
    """
    posts = []
    sample_dir = os.path.join(BASE_DIR, "sample_data")
    if not os.path.exists(sample_dir):
        return posts

    for fname in sorted(os.listdir(sample_dir)):
        if not fname.endswith(".xml"):
            continue

        fpath     = os.path.join(sample_dir, fname)
        sample_id = "sample_" + fname

        if sample_id in seen_ids:
            continue

        product = ""
        for code in (WEATHER_PRODUCTS | TSUNAMI_PRODUCTS | QUAKE_PRODUCTS):
            if code in fname.upper():
                product = code
                break

        try:
            with open(fpath, "rb") as f:
                xml_data = f.read()
        except Exception as e:
            logging.warning(f"サンプルファイル読み込み失敗: {fname}: {e}")
            continue

        post_text = None
        if product in WEATHER_PRODUCTS:
            post_text = parse_weather_xml(xml_data, product)
        elif product in TSUNAMI_PRODUCTS:
            post_text = parse_tsunami_xml(xml_data)
        elif product in QUAKE_PRODUCTS:
            post_text = parse_earthquake_xml(xml_data)

        seen_ids.add(sample_id)   # サンプルは即座に記録済みにする

        if post_text:
            posts.append({
                "id":           sample_id,
                "type":         product,
                "text":         post_text,
                "source":       f"疑似電文（{fname}）",
                "is_sample":    True,
                "updated":      "",   # サンプルは鮮度フィルタ対象外
                "headline_key": "",   # サンプルはクールダウン対象外
            })

    return posts


# ===== VPWW53/54 重複排除 =====

def _dedup_by_text(posts: list) -> list:
    """
    投稿文が同じエントリを 1 件にまとめる（VPWW53/54 二重発行対策）。
    最初に出現したものを残し、後続は seen_ids に追加する（呼び出し側で処理）。

    Returns
    -------
    (deduped_posts, duplicate_ids)
    deduped_posts : 重複を除いたリスト
    duplicate_ids : 重複として除外したエントリの ID セット
    """
    seen_texts: dict[str, str] = {}  # text → id
    deduped: list = []
    duplicate_ids: set = set()

    for post in posts:
        t = post["text"]
        if t in seen_texts:
            duplicate_ids.add(post["id"])
            logging.info(
                f"  重複排除: {post['id']} (同一文面 = {seen_texts[t]})"
            )
        else:
            seen_texts[t] = post["id"]
            deduped.append(post)

    return deduped, duplicate_ids


# ===== 出力（dry-run 用） =====

def print_posts(posts: list) -> None:
    if not posts:
        logging.info("新しい青森県の情報はありませんでした。")
        return

    bar = "=" * 60
    logging.info(f"\n{bar}")
    logging.info(
        f"[DRY RUN] {len(posts)} 件の投稿予定文を生成しました"
        f"（POST_MODE=live のときのみ実際に投稿されます）"
    )
    logging.info(bar)

    for i, post in enumerate(posts, 1):
        note = " ★疑似電文" if post.get("is_sample") else ""
        logging.info(f"\n--- 投稿 {i}/{len(posts)}{note} ---")
        logging.info(f"  種別    : {post['type']}")
        logging.info(f"  データ源: {post['source']}")
        logging.info(
            f"  文字数  : {len(post['text'])} 字"
            f"（140 字以内: {'OK' if len(post['text']) <= 140 else 'NG'}）"
        )
        logging.info(f"  投稿予定文:\n{post['text']}")


# ===== メイン =====

def run(reset: bool = False) -> list:
    """
    ボットのメイン処理。

    Parameters
    ----------
    reset : True のとき seen_ids.json を削除してから実行

    Returns
    -------
    list : 今回の実行で投稿した（dry-run 含む）投稿情報のリスト

    Exit
    ----
    sys.exit(1) : X API への投稿で 401/402/403/429 または通信エラーが発生した場合
    """
    os.makedirs(STATE_DIR, exist_ok=True)
    setup_logging()

    if reset:
        reset_seen_ids()

    # 設定を読み込む
    cfg = safety.get_config()
    logging.info(
        f"起動: mode={cfg['post_mode']}  "
        f"max_age={cfg['max_age_min']}分  "
        f"daily={cfg['daily_limit']}件  "
        f"per_run={cfg['per_run_limit']}件  "
        f"p2p={'ON' if cfg['enable_p2p'] else 'OFF'}"
    )

    seen_ids = load_seen_ids()

    # クールダウン状態を読み込む
    cooldown = safety.load_warn_cooldown(STATE_DIR)

    # live モードでは認証情報を事前確認する
    credentials = None
    if cfg["post_mode"] == "live":
        try:
            credentials = x_poster.load_credentials()
        except ValueError as e:
            logging.critical(f"認証情報エラー: {e}")
            sys.exit(1)

    # ----- フィードを取得 -----
    candidates: list = []

    try:
        candidates += process_weather_feed(seen_ids)
    except Exception as e:
        logging.error(f"気象フィードエラー: {e}")

    try:
        candidates += process_eqvol_feed(seen_ids)
    except Exception as e:
        logging.error(f"地震フィードエラー: {e}")

    if cfg["enable_p2p"]:
        try:
            logging.info("Fetching P2P earthquake data ...")
            p2p_data, _ = fetch_url(P2P_QUAKE_URL)
            quakes       = json.loads(p2p_data)
            p2p_posts    = process_p2p_quakes(quakes, seen_ids, enable_p2p=True)
            logging.info(f"  {len(p2p_posts)} 件の新規 P2P 地震情報")
            candidates += p2p_posts
        except Exception as e:
            logging.error(f"P2P 地震情報エラー: {e}")

    # ----- VPWW53/54 重複排除 -----
    candidates, dup_ids = _dedup_by_text(candidates)
    for did in dup_ids:
        seen_ids.add(did)   # 重複は見送り扱いで記録

    # ----- 鮮度フィルタ -----
    fresh_candidates = []
    for post in candidates:
        if safety.is_fresh(post.get("updated", ""), cfg["max_age_min"]):
            fresh_candidates.append(post)
        else:
            logging.info(
                f"  鮮度フィルタ: {post['id']} を見送り"
                f"（{cfg['max_age_min']} 分超）"
            )
            seen_ids.add(post["id"])   # 古いものは見送り扱いで記録

    # ----- 文面ガード -----
    checked_candidates = []
    for post in fresh_candidates:
        try:
            safety.check_content(post["text"])
            checked_candidates.append(post)
        except ValueError as e:
            logging.error(f"  文面ガード NG: {post['id']} - {e}")
            seen_ids.add(post["id"])

    # ----- クールダウン確認 -----
    # 同じ見出し（headline_key）が設定した時間内に投稿済みなら見送る。
    # クールダウンは live モードの実投稿後のみ更新する（dry-run では更新しない）。
    no_cooldown_candidates = []
    for post in checked_candidates:
        hkey = post.get("headline_key", "")
        if safety.is_in_cooldown(hkey, cooldown, cfg["cooldown_hours"]):
            logging.info(
                f"  クールダウン: {post['id']} を見送り"
                f"（{cfg['cooldown_hours']}時間以内に同一内容を投稿済み）"
            )
            seen_ids.add(post["id"])
        else:
            no_cooldown_candidates.append(post)

    # ----- 上限適用 -----
    daily_count = safety.load_daily_count(STATE_DIR)
    to_post, skipped_by_limit = safety.apply_limits(
        no_cooldown_candidates,
        daily_count,
        cfg["daily_limit"],
        cfg["per_run_limit"],
    )

    if skipped_by_limit:
        logging.warning(
            f"[LIMIT] 上限により {len(skipped_by_limit)} 件を見送りました。"
            f" （1日 {cfg['daily_limit']} 件 / 1回 {cfg['per_run_limit']} 件）"
        )
        for s in skipped_by_limit:
            logging.warning(
                f"  見送り（上限）: {s['id']}  種別={s.get('type', '-')}"
            )
            logging.warning(
                f"  内容（先頭80字）: {s.get('text', '')[:80]!r}"
            )
            seen_ids.add(s["id"])  # 記録済みにして同じ情報の繰り返し通知を防ぐ

    # ----- dry-run モード：INCLUDE_SAMPLES=true のときだけサンプルを処理 -----
    # 公開リポジトリの Actions ログは誰でも読めるため、疑似電文は既定オフ。
    # ローカル確認用: INCLUDE_SAMPLES=true /usr/bin/python3 aomori_bot.py
    sample_posts: list = []
    _include_samples = os.environ.get("INCLUDE_SAMPLES", "").lower() == "true"
    if cfg["post_mode"] != "live" and _include_samples:
        try:
            sample_posts = process_sample_data(seen_ids)
        except Exception as e:
            logging.error(f"サンプルデータエラー: {e}")

    # ----- 投稿 / ドライラン -----
    posted: list = []

    if cfg["post_mode"] == "live":
        # 本番投稿
        for post in to_post:
            try:
                tweet_id = x_poster.post_tweet(post["text"], credentials)
                logging.info(
                    f"  投稿成功: {post['id']} → tweet_id={tweet_id}"
                )
            except x_poster.DuplicatePostError as e:
                # X が「重複コンテンツ」403 を返した → 投稿済み扱いで記録して次へ進む
                # （daily_count は増やさない：新規投稿ではないため）
                logging.warning(
                    f"  重複コンテンツ（投稿済み扱い）: {post['id']} - {e}"
                )
                seen_ids.add(post["id"])
                save_seen_ids(seen_ids)
                continue
            except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                logging.critical(
                    f"X API 投稿失敗: {post['id']} - {e}\n"
                    f"このアイテムは次回に再試行されます。"
                )
                # 失敗したアイテムは seen_ids に追加しない
                save_seen_ids(seen_ids)  # これまでの成功分を保存して終了
                safety.save_daily_count(daily_count, STATE_DIR)
                safety.save_warn_cooldown(cooldown, STATE_DIR)
                sys.exit(1)

            # 投稿成功 → 即座に記録・クールダウン更新
            seen_ids.add(post["id"])
            save_seen_ids(seen_ids)
            daily_count += 1
            safety.save_daily_count(daily_count, STATE_DIR)
            safety.update_warn_cooldown(post.get("headline_key", ""), cooldown)
            posted.append(post)

    else:
        # dry-run
        all_dry = to_post + sample_posts
        print_posts(all_dry)
        for post in to_post:
            seen_ids.add(post["id"])
        posted = to_post

    # ----- 最終保存 -----
    save_seen_ids(seen_ids)
    safety.save_daily_count(daily_count, STATE_DIR)
    # クールダウンは live モードの場合のみ実投稿があったとして保存する。
    # dry モードでは cooldown に変更がなくても保存して状態ファイルを維持する。
    safety.save_warn_cooldown(cooldown, STATE_DIR)

    logging.info(
        f"完了: 投稿={len(posted)}件  "
        f"本日合計={daily_count}件"
    )

    # 上限超過があった場合の終了コード。
    # live モード: 終了コード 1 → GitHub Actions の失敗通知メールが届く。
    # dry  モード: 警告ログのみ。終了コード 0（通知メールが来てうるさいため）。
    # 見送り分はすでに seen_ids に追加済みなので、次回実行で同じ通知は出ない。
    if skipped_by_limit:
        if cfg["post_mode"] == "live":
            logging.warning(
                "[LIMIT] 上限超過が発生したため終了コード 1 で終了します"
                "（GitHub Actions の失敗通知メールが届きます）。"
            )
            sys.exit(1)
        else:
            logging.warning(
                "[LIMIT] 上限超過がありました（dryモード: 終了コード 0 のまま続行）"
            )

    return posted


if __name__ == "__main__":
    reset_flag = "--reset" in sys.argv
    run(reset=reset_flag)
