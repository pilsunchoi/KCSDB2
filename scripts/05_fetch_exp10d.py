"""05_fetch_exp10d.py — 수출 10대 품목 10일 단위 잠정치를 받아 적재한다.

자료
----
공공데이터포털 15157908 「관세청_수출 주요품목별 10일 단위 잠정치 통계」
  https://apis.data.go.kr/1220000/prlstMmUtPrviExpAcrs/getPrlstMmUtPrviExpAcrs
  요청 변수는 strtYymm, endYymm 둘뿐이다. 2016년 1월부터 제공된다.
  공표는 상순분이 11일, 중순분이 21일, 월 전체가 익월 1일이다.

알아 둘 것 넷
-------------
1. **누적치다.** priodDt가 '01~10', '01~20', '01~말일'이고 값은 그 시점까지의 누적이다.
   상순·중순·하순 증분을 얻으려면 차분해야 한다(뷰 v_exp10d_seg가 해 준다).
   셋째 값의 끝자리는 달마다 달라 '01~28', '01~30', '01~31'이 모두 나온다.

2. **품목 이름이 응답에 없다.** itemUsdAmt00~10 번호뿐이라 순서를 알아야 한다.
   2025년 12월 실적을 `dim_hs10_to_major10`과 대조해 확정했다. 열 개 모두 소수점
   둘째 자리까지 일치했다(승용차·석유제품만 0.1% 미만 차이).

3. **단위는 천 달러다.** 원본 그대로 저장한다. 쉼표와 앞 공백이 섞여 있어 정리해야 한다.

4. **같은 시점을 여러 번 관측한다.** 당월은 잠정치이고 전월까지는 신고 정정·취하를
   반영해 현행화된다. 그래서 값이 바뀔 때마다 새 행을 쌓고 `fetched_at`으로 구분한다.
   이것이 있어야 나중에 "그때 알 수 있던 값"으로 예측 성능을 정직하게 잴 수 있다.
   값이 그대로면 새 행을 만들지 않는다.

만드는 것
---------
fact_exp10d(base_ym, cutoff, priod_dt, item, exp_kusd, fetched_at)
  cutoff 10 = 상순, 20 = 중순, 99 = 월 전체(누적). dim_workday10d와 코드가 같되
  **저쪽은 증분 구간, 이쪽은 누적**이라는 점이 다르다.
v_exp10d_seg  최신 관측만 남기고 누적을 구간 증분으로 바꾼 뷰.
  seg_kusd = 그 구간에만 해당하는 증분, cum_kusd = 원본 누적치. **이름을 구분한 이유가
  있다.** 둘을 헷갈리면 cutoff=99가 월 전체가 아니라 하순 증분이 되어 값이 40%로 나온다.

원본 XML은 data/raw/exp10d/에 받은 시각으로 남긴다(gitignore).

실행: python scripts/05_fetch_exp10d.py            # 최근 2년만 갱신
      python scripts/05_fetch_exp10d.py --full     # 2016년부터 전 기간
"""

from __future__ import annotations

import datetime as dt
import os
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "processed", "kcsdb.duckdb")
RAW = os.path.join(ROOT, "data", "raw", "exp10d")
ENV = os.path.join(ROOT, "config", "api_key.env")
URL = "https://apis.data.go.kr/1220000/prlstMmUtPrviExpAcrs/getPrlstMmUtPrviExpAcrs"
FIRST_YEAR = 2016

# 응답의 itemUsdAmt 번호와 품목. 2025년 12월 실적 대조로 확정했다.
ITEMS = {
    "00": "총수출",   "01": "반도체",       "02": "철강제품",
    "03": "승용차",   "04": "석유제품",     "05": "무선통신기기",
    "06": "선박",     "07": "자동차부품",   "08": "컴퓨터주변기기",
    "09": "정밀기기", "10": "가전제품",
}


def api_key() -> str:
    for line in open(ENV, encoding="utf-8"):
        if line.startswith("DATA_GO_KR_API_KEY") and not line.startswith("#"):
            return line.split("=", 1)[1].strip()
    sys.exit(f"DATA_GO_KR_API_KEY가 없다: {ENV}")


def fetch(key: str, a: str, b: str) -> str:
    """디코딩 키를 urlencode로 감싼다. '+'를 그대로 두면 서버가 공백으로 읽는다."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    qs = urllib.parse.urlencode({"serviceKey": key, "strtYymm": a, "endYymm": b})
    with urllib.request.urlopen(f"{URL}?{qs}", timeout=60, context=ctx) as r:
        return r.read().decode("utf-8")


def parse(xml: str, fetched: dt.datetime) -> pd.DataFrame:
    rows = []
    for it in ET.fromstring(xml).findall(".//item"):
        d = {e.tag: (e.text or "").strip() for e in it}
        ym, pd_ = int(d["priodMon"]), d["priodDt"]
        cut = 10 if pd_.endswith("10") else 20 if pd_.endswith("20") else 99
        for num, name in ITEMS.items():
            raw = d.get(f"itemUsdAmt{num}", "").replace(",", "").strip()
            if raw:
                rows.append((ym, cut, pd_, name, int(raw), fetched))
    return pd.DataFrame(rows, columns=["base_ym", "cutoff", "priod_dt",
                                       "item", "exp_kusd", "fetched_at"])


def upsert(con: duckdb.DuckDBPyConnection, new: pd.DataFrame) -> int:
    """값이 바뀐 것만 새 행으로 쌓는다. 개정 이력이 남고 표가 부풀지 않는다."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS fact_exp10d (
            base_ym INTEGER, cutoff SMALLINT, priod_dt VARCHAR,
            item VARCHAR, exp_kusd BIGINT, fetched_at TIMESTAMP)""")
    con.register("new", new)
    n = con.sql("""
        WITH last AS (   -- 시점·품목마다 가장 최근에 본 값
            SELECT base_ym, cutoff, item, exp_kusd,
                   ROW_NUMBER() OVER (PARTITION BY base_ym, cutoff, item
                                      ORDER BY fetched_at DESC) rn
            FROM fact_exp10d)
        SELECT COUNT(*) FROM new n
        LEFT JOIN (SELECT * FROM last WHERE rn = 1) l
          ON l.base_ym = n.base_ym AND l.cutoff = n.cutoff AND l.item = n.item
        WHERE l.exp_kusd IS NULL OR l.exp_kusd <> n.exp_kusd""").fetchone()[0]
    con.execute("""
        WITH last AS (
            SELECT base_ym, cutoff, item, exp_kusd,
                   ROW_NUMBER() OVER (PARTITION BY base_ym, cutoff, item
                                      ORDER BY fetched_at DESC) rn
            FROM fact_exp10d)
        INSERT INTO fact_exp10d
        SELECT n.* FROM new n
        LEFT JOIN (SELECT * FROM last WHERE rn = 1) l
          ON l.base_ym = n.base_ym AND l.cutoff = n.cutoff AND l.item = n.item
        WHERE l.exp_kusd IS NULL OR l.exp_kusd <> n.exp_kusd""")
    return n


def make_view(con: duckdb.DuckDBPyConnection) -> None:
    """누적치를 상순·중순·하순 증분으로 바꾸고 최신 관측만 남긴다."""
    con.execute("""
        CREATE OR REPLACE VIEW v_exp10d_seg AS
        WITH latest AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY base_ym, cutoff, item
                                         ORDER BY fetched_at DESC) rn
            FROM fact_exp10d),
        cum AS (SELECT base_ym, cutoff, item, exp_kusd FROM latest WHERE rn = 1)
        SELECT c.base_ym, c.cutoff,
               CASE c.cutoff WHEN 10 THEN '상순' WHEN 20 THEN '중순' ELSE '하순' END AS seg,
               c.item,
               c.exp_kusd - COALESCE(p.exp_kusd, 0) AS seg_kusd,   -- 구간 증분
               c.exp_kusd AS cum_kusd                              -- 누적(원본 그대로)
        FROM cum c
        LEFT JOIN cum p ON p.base_ym = c.base_ym AND p.item = c.item
                       AND p.cutoff = CASE c.cutoff WHEN 20 THEN 10 WHEN 99 THEN 20 END""")


def main() -> None:
    full = "--full" in sys.argv
    os.makedirs(RAW, exist_ok=True)
    key = api_key()
    now = dt.datetime.now().replace(microsecond=0)
    this_year = now.year
    years = range(FIRST_YEAR, this_year + 1) if full else range(this_year - 1, this_year + 1)

    frames = []
    for y in years:
        xml = fetch(key, f"{y}01", f"{y}12")
        stamp = now.strftime("%Y%m%d_%H%M%S")
        with open(os.path.join(RAW, f"{stamp}_{y}.xml"), "w", encoding="utf-8") as f:
            f.write(xml)
        df = parse(xml, now)
        frames.append(df)
        print(f"  {y}: {len(df):>4}행")
    new = pd.concat(frames, ignore_index=True)

    con = duckdb.connect(DB)
    try:
        added = upsert(con, new)
        make_view(con)
        tot, ymin, ymax, vint = con.sql("""
            SELECT COUNT(*), MIN(base_ym), MAX(base_ym), COUNT(DISTINCT fetched_at)
            FROM fact_exp10d""").fetchone()
        print(f"\n받은 {len(new):,}행 중 {added:,}행이 새 값 (나머지는 이전과 같아 건너뜀)")
        print(f"fact_exp10d 누적 {tot:,}행 | {ymin}~{ymax} | 관측 시각 {vint}개")
        print()
        print(con.sql("""
            SELECT base_ym AS 연월, seg AS 구간,
                   ROUND(SUM(seg_kusd) FILTER (WHERE item='총수출') * 1000 / 1e8, 1) AS 총수출_억,
                   ROUND(SUM(seg_kusd) FILTER (WHERE item='반도체') * 1000 / 1e8, 1) AS 반도체_억
            FROM v_exp10d_seg GROUP BY 1, 2, cutoff
            ORDER BY 연월 DESC, cutoff LIMIT 6""").df().to_string(index=False))
    finally:
        con.close()


if __name__ == "__main__":
    main()
