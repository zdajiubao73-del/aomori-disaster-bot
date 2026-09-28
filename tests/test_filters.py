#!/usr/bin/env /usr/bin/python3
"""
test_filters.py - フィルタリングの正確性テスト
・青森県以外の情報が混ざらないこと
・震度3未満の地震は除外されること
・重複投稿が防止されること
"""

import os
import sys
import json
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import aomori_bot as bot

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

# ===== XML サンプル（str → encode で bytes に変換）=====
AOMORI_VPWW53 = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>青森県気象警報・注意報</Title>
    <ReportDateTime>2026-09-28T20:19:00+09:00</ReportDateTime>
    <InfoType>発表</InfoType>
    <Headline>
      <Text>津軽、下北では、強風に注意してください。津軽では、高波に注意してください。</Text>
    </Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"/>
</Report>""".encode("utf-8")

SHIGA_VPWW53 = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>滋賀県気象警報・注意報</Title>
    <ReportDateTime>2026-09-28T11:35:36+09:00</ReportDateTime>
    <InfoType>発表</InfoType>
    <Headline>
      <Text>滋賀県では、土砂災害に注意してください。</Text>
    </Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/meteorology1/"/>
</Report>""".encode("utf-8")

QUAKE_AOMORI = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>震源・震度に関する情報</Title>
    <ReportDateTime>2026-09-25T03:00:00+09:00</ReportDateTime>
    <Headline>
      <Text>25日02時55分ころ、地震がありました。</Text>
    </Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/seismology1/"
        xmlns:jmx_eb="http://xml.kishou.go.jp/jmaxml1/elementBasis1/">
    <Earthquake>
      <OriginTime>2026-09-25T02:55:00+09:00</OriginTime>
      <Hypocenter>
        <Area>
          <Name>青森県東方沖</Name>
          <Coordinate/>
        </Area>
      </Hypocenter>
      <jmx_eb:Magnitude type="Mj" description="M4.5">4.5</jmx_eb:Magnitude>
    </Earthquake>
    <Intensity>
      <Observation>
        <Pref>
          <Name>青森県</Name>
          <MaxInt>3</MaxInt>
          <Area>
            <Name>青森県三八上北</Name>
            <MaxInt>3</MaxInt>
          </Area>
        </Pref>
        <Pref>
          <Name>岩手県</Name>
          <MaxInt>2</MaxInt>
        </Pref>
      </Observation>
    </Intensity>
  </Body>
</Report>""".encode("utf-8")

QUAKE_NO_AOMORI = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>震源・震度に関する情報</Title>
    <ReportDateTime>2026-09-28T11:05:00+09:00</ReportDateTime>
    <Headline><Text>28日11時05分ころ、地震がありました。</Text></Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/seismology1/"
        xmlns:jmx_eb="http://xml.kishou.go.jp/jmaxml1/elementBasis1/">
    <Earthquake>
      <OriginTime>2026-09-28T11:05:00+09:00</OriginTime>
      <Hypocenter><Area><Name>岩手県沖</Name><Coordinate/></Area></Hypocenter>
      <jmx_eb:Magnitude type="Mj" description="M3.3">3.3</jmx_eb:Magnitude>
    </Earthquake>
    <Intensity>
      <Observation>
        <Pref>
          <Name>岩手県</Name>
          <MaxInt>1</MaxInt>
        </Pref>
      </Observation>
    </Intensity>
  </Body>
</Report>""".encode("utf-8")

QUAKE_AOMORI_INT2 = """<?xml version="1.0" encoding="UTF-8"?>
<Report xmlns="http://xml.kishou.go.jp/jmaxml1/">
  <Head xmlns="http://xml.kishou.go.jp/jmaxml1/informationBasis1/">
    <Title>震源・震度に関する情報</Title>
    <ReportDateTime>2026-09-26T08:00:00+09:00</ReportDateTime>
    <Headline><Text>26日08時00分ころ、地震がありました。</Text></Headline>
  </Head>
  <Body xmlns="http://xml.kishou.go.jp/jmaxml1/body/seismology1/"
        xmlns:jmx_eb="http://xml.kishou.go.jp/jmaxml1/elementBasis1/">
    <Earthquake>
      <OriginTime>2026-09-26T08:00:00+09:00</OriginTime>
      <Hypocenter><Area><Name>陸奥湾</Name><Coordinate/></Area></Hypocenter>
      <jmx_eb:Magnitude type="Mj" description="M3.0">3.0</jmx_eb:Magnitude>
    </Earthquake>
    <Intensity>
      <Observation>
        <Pref>
          <Name>青森県</Name>
          <MaxInt>2</MaxInt>
        </Pref>
      </Observation>
    </Intensity>
  </Body>
</Report>""".encode("utf-8")

# ===== extract_product_area のテスト =====
print("\n== URL パース ==")
product, area = bot.extract_product_area(
    "https://www.data.jma.go.jp/developer/xml/data/20260928111928_0_VPWW53_020000.xml"
)
ok("Aomori VPWW53 プロダクトコード", product == "VPWW53", f"got={product}")
ok("Aomori エリアコード 020000",     area    == "020000", f"got={area}")

product2, area2 = bot.extract_product_area(
    "https://www.data.jma.go.jp/developer/xml/data/20260928113536_0_VPWW53_250000.xml"
)
ok("Shiga (250000) エリアコード != 020000", area2 != "020000", f"got={area2}")

# ===== 気象警報XML 青森判定 =====
print("\n== 気象警報XML 青森フィルタ ==")

result_aomori = bot.parse_weather_xml(AOMORI_VPWW53, "VPWW53")
ok("青森XML → 投稿文が生成される", result_aomori is not None,
   str(result_aomori)[:60] if result_aomori else "None")
ok("青森XML → '津軽' が含まれる",       result_aomori and "津軽" in result_aomori)
ok("青森XML → '出典：気象庁' が含まれる", result_aomori and "出典：気象庁" in result_aomori)

result_shiga = bot.parse_weather_xml(SHIGA_VPWW53, "VPWW53")
ok("滋賀XML → None（除外される）", result_shiga is None, f"got={result_shiga}")

# ===== 140字チェック =====
print("\n== 140文字制限 ==")
short_text = (
    "【気象注意報】9月28日20時19分 津軽、下北では、強風に注意してください。"
    "津軽では、高波に注意してください。\n出典：気象庁"
)
long_text = "【気象注意報】" + "あ" * 150 + "\n出典：気象庁"

ok("短い文 → そのまま",  bot.truncate140(short_text) == short_text)
ok("長い文 → 140字以内", len(bot.truncate140(long_text)) <= 140,
   f"got={len(bot.truncate140(long_text))}")
ok("長い文 → '…' で終わる", bot.truncate140(long_text).endswith("…"))

# ===== 地震 XML フィルタ =====
print("\n== 地震XML フィルタ ==")

r_aomori3 = bot.parse_earthquake_xml(QUAKE_AOMORI)
ok("青森震度3 → 投稿文生成",            r_aomori3 is not None,
   str(r_aomori3)[:80] if r_aomori3 else "None")
ok("青森震度3 → '青森' が含まれる",     r_aomori3 and "青森" in r_aomori3)
ok("青森震度3 → '震度3' か '3' を含む", r_aomori3 and "3" in r_aomori3)
ok("青森震度3 → '出典：気象庁' を含む", r_aomori3 and "出典：気象庁" in r_aomori3)
ok("青森震度3 → 140字以内",
   r_aomori3 and len(r_aomori3) <= 140, f"{len(r_aomori3) if r_aomori3 else 'N/A'}")

r_no_aomori = bot.parse_earthquake_xml(QUAKE_NO_AOMORI)
ok("岩手のみ地震 → None",              r_no_aomori is None, f"got={r_no_aomori}")

r_int2 = bot.parse_earthquake_xml(QUAKE_AOMORI_INT2)
ok("青森震度2 → None（震度3未満除外）", r_int2 is None, f"got={r_int2}")

# ===== 重複防止テスト =====
print("\n== 重複防止 ==")

seen = set()
DUMMY_ID = "test_entry_001"
seen.add(DUMMY_ID)

# P2P重複テスト
quake_data = [
    {
        "id": "abc123",
        "earthquake": {
            "hypocenter": {"name": "青森県東方沖", "magnitude": 4.5},
            "time": "2026/09/25 02:55",
        },
        "points": [{"pref": "青森県", "addr": "八戸市", "scale": 30}],
    }
]
seen2 = set()
posts1 = bot.process_p2p_quakes(quake_data, seen2)
posts2 = bot.process_p2p_quakes(quake_data, seen2)   # 2回目（重複）

ok("P2P 1回目 → 1件生成",          len(posts1) == 1, f"got={len(posts1)}")
ok("P2P 2回目 → 0件（重複防止）",  len(posts2) == 0, f"got={len(posts2)}")

# ===== P2P スケール変換 =====
print("\n== P2P スケール変換 ==")
ok("scale 30 → '3'",   bot.p2p_scale_to_str(30) == "3")
ok("scale 45 → '5弱'", bot.p2p_scale_to_str(45) == "5弱")
ok("scale 70 → '7'",   bot.p2p_scale_to_str(70) == "7")

# P2P 震度3未満除外
quake_low = [
    {
        "id": "low001",
        "earthquake": {
            "hypocenter": {"name": "陸奥湾", "magnitude": 3.0},
            "time": "2026/09/26 08:00",
        },
        "points": [{"pref": "青森県", "addr": "青森市", "scale": 20}],
    }
]
seen3 = set()
posts_low = bot.process_p2p_quakes(quake_low, seen3)
ok("P2P 青森震度2 → 除外される", len(posts_low) == 0, f"got={len(posts_low)}")

# ===== URLドメイン文字列チェック =====
print("\n== 投稿文にドメイン文字列が入らないこと ==")
domain_pattern = re.compile(r'\b[a-zA-Z0-9\-]+\.[a-zA-Z]{2,4}\b')

for label, xml_bytes, product_code in [
    ("青森警報XML", AOMORI_VPWW53, "VPWW53"),
    ("地震XML",     QUAKE_AOMORI,  "VXSE53"),
]:
    if product_code == "VXSE53":
        text = bot.parse_earthquake_xml(xml_bytes) or ""
    else:
        text = bot.parse_weather_xml(xml_bytes, product_code) or ""

    check_text = text.replace("出典：気象庁", "")
    domains = domain_pattern.findall(check_text)
    ok(f"{label} → ドメイン文字列なし", len(domains) == 0, f"found={domains}")

# ===== 疑似電文 parse_weather_xml テスト =====
print("\n== 疑似電文の parse (土砂・記録的大雨・竜巻・津波) ==")
sample_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data")

for fname, product_code, expected_kw in [
    ("sample_VXWW50_020000.xml",        "VXWW50", "土砂災害警戒情報"),
    ("sample_VPOA50_020000.xml",        "VPOA50", "記録的短時間大雨情報"),
    ("sample_VPHW62_020000.xml",        "VPHW62", "竜巻注意情報"),
    ("sample_VTSE51_010000.xml",        "VTSE51", "津波情報"),
    ("sample_VXSE53_aomori_int3.xml",  "VXSE53", "地震情報"),
]:
    fpath = os.path.join(sample_dir, fname)
    if not os.path.exists(fpath):
        ok(f"{fname} → スキップ（ファイルなし）", True, "skip")
        continue
    with open(fpath, "rb") as f:
        xml_data = f.read()
    if product_code == "VXSE53":
        text = bot.parse_earthquake_xml(xml_data)
    elif product_code == "VTSE51":
        text = bot.parse_tsunami_xml(xml_data)
    else:
        text = bot.parse_weather_xml(xml_data, product_code)

    ok(f"{fname} → 投稿文生成", text is not None, str(text)[:60] if text else "None")
    if text:
        ok(f"  → '{expected_kw}' を含む", expected_kw in text, f"text={text[:60]}")
        ok(f"  → 140字以内", len(text) <= 140, f"{len(text)}字")
        ok(f"  → '出典：気象庁' を含む", "出典：気象庁" in text)

# ===== 結果集計 =====
print()
total  = len(_results)
passed = sum(1 for _, r in _results if r)
failed = total - passed
bar    = "-" * 40
print(bar)
print(f"テスト結果: {passed}/{total} PASS  ({failed} FAIL)")
print(bar)

sys.exit(0 if failed == 0 else 1)
