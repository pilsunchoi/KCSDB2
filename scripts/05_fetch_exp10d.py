"""05_fetch_exp10d.py — 10일 단위 잠정치 네 계열을 받아 적재한다.

자료
----
공공데이터포털의 관세청 10일 단위 잠정치는 넷이다. 요청 변수는 넷 다 strtYymm,
endYymm 둘뿐이고 2016년 1월부터 제공된다. 공표는 상순분이 11일, 중순분이 21일,
월 전체가 익월 1일이다.

  exp_item  15157908  수출 주요품목별  prlstMmUtPrviExpAcrs
  exp_cnty  15157941  수출 주요국가별  cntyMmUtPrviExpAcrs
  imp_item  15157901  수입 주요품목별  prlstMmUtPrviImpAcrs
  imp_cnty  15157909  수입 주요국가별  cntyMmUtPrviImpAcrs

넷 다 활용신청이 필요하고 자동승인이다. 신청 전에 부르면 403 '등록되지 않은
서비스키'가 온다(없는 엔드포인트 이름은 400이 오므로 둘을 구분할 수 있다).

알아 둘 것 다섯
---------------
1. **누적치다.** priodDt가 '01~10', '01~20', '01~말일'이고 값은 그 시점까지의 누적이다.
   상순·중순·하순 증분을 얻으려면 차분해야 한다(뷰 v_exp10d_seg가 해 준다).
   셋째 값의 끝자리는 달마다 달라 '01~28', '01~30', '01~31'이 모두 나온다.

2. **항목 이름이 응답에 없다.** 네 계열 모두 itemUsdAmt00~10 번호뿐이라 순서를
   알아야 한다. 아래 ITEMS의 순서는 2026년 8월 1~20일 보도자료 붙임 표와 33개 값을
   전부 대조해 확정한 것이다(수출 품목별은 앞서 2025년 12월 실적으로 확정했다).
   **번호가 금액 순이 아니다** — 수출 국가별 04가 베트남(58.3억)인데 03이
   유럽연합(35.5억)이고, 수입 품목별 05가 반도체제조장비인데 03이 기계류다.
   순서를 짐작으로 정하면 틀린다.

3. **단위는 천 달러다.** 원본 그대로 저장한다. 쉼표와 앞 공백이 섞여 있어 정리해야 한다.
   수출은 FOB, 수입은 과세가격(CIF) 기준이라 **수준을 맞바로 견주면 안 된다.**

4. **같은 시점을 여러 번 관측한다.** 당월은 잠정치이고 전월까지는 신고 정정·취하를
   반영해 현행화된다. 그래서 값이 바뀔 때마다 새 행을 쌓고 `fetched_at`으로 구분한다.
   이것이 있어야 나중에 "그때 알 수 있던 값"으로 예측 성능을 정직하게 잴 수 있다.
   값이 그대로면 새 행을 만들지 않는다. **이 이력은 원리상 복구할 수 없다**(02c 참조).

5. **총계가 두 계열에서 겹친다.** 수출 품목별의 총수출과 수출 국가별의 총수출은 같은
   값이어야 하고 수입도 마찬가지다. 이것을 매번 대조한다 — 번호 대응이 어긋나거나
   한쪽만 갱신되면 여기서 걸린다.

6. **월 전체 총계는 신성질별 공식 실적과도 맞는다.** `fact_nqi`의 월별 합과 대조한다.
   겹치는 127개월에서 최대 격차가 수출 0.00025%, 수입 0.0000014%였다. 품목으로는
   내려가지 못한다 — 10대 품목은 **현행 수출 성질별**이라 신성질별과 정의가 다르고,
   이름이 겹치는 반도체·선박조차 0.3~2% 어긋난다.

7. **품목별 월 전체값은 성질별 공식 실적과 맞는다.** 10대 품목은 현행 수출·수입
   **성질별** 분류의 마디로 정의되므로 `fact_temper`의 부호를 묶으면 같은 정의의
   계열이 나온다. 수출 37부호·수입 42부호를 묶어 2,794쌍을 맞대니 최대 격차가
   0.0001%였다. **이것이 품목 단위로 검증할 수 있는 유일한 경로다** — 신성질별은
   총액만 맞고 `dim_hs10_to_major10` 경로는 우리 매핑을 재는 것이라 순환이다.

만드는 것
---------
fact_exp10d(series, base_ym, cutoff, priod_dt, item, amt_kusd, fetched_at)
  cutoff 10 = 상순, 20 = 중순, 99 = 월 전체(누적). dim_workday10d와 코드가 같되
  **저쪽은 증분 구간, 이쪽은 누적**이라는 점이 다르다.
v_exp10d_seg  최신 관측만 남기고 누적을 구간 증분으로 바꾼 뷰.
  seg_kusd = 그 구간에만 해당하는 증분, cum_kusd = 원본 누적치. **이름을 구분한 이유가
  있다.** 둘을 헷갈리면 cutoff=99가 월 전체가 아니라 하순 증분이 되어 값이 40%로 나온다.
  **뷰를 쓸 때는 series를 반드시 걸러야 한다.** 안 그러면 품목과 국가가 섞인다.

옛 스키마(series 없음, exp_kusd)는 처음 실행할 때 자동으로 옮긴다. 기존 행은
series='exp_item'이 되고 vintage 이력은 그대로 보존된다.

원본 XML은 data/raw/exp10d/에 받은 시각으로 남긴다(gitignore).

실행: python scripts/05_fetch_exp10d.py                 # 네 계열, 최근 2년만 갱신
      python scripts/05_fetch_exp10d.py --full          # 2016년부터 전 기간
      python scripts/05_fetch_exp10d.py --series exp_item,exp_cnty   # 골라 받기
"""

from __future__ import annotations

import argparse
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
BASE = "https://apis.data.go.kr/1220000"
FIRST_YEAR = 2016

# 번호 -> 이름. 2026년 8월 1~20일 보도자료 붙임 표와 전수 대조해 확정했다.
ITEMS = {
    "exp_item": ["총수출", "반도체", "철강제품", "승용차", "석유제품", "무선통신기기",
                 "선박", "자동차부품", "컴퓨터주변기기", "정밀기기", "가전제품"],
    "exp_cnty": ["총수출", "중국", "미국", "유럽연합", "베트남", "홍콩",
                 "일본", "대만", "인도", "싱가포르", "말레이시아"],
    "imp_item": ["총수입", "반도체", "원유", "기계류", "가스", "반도체제조장비",
                 "정밀기기", "석유제품", "무선통신기기", "승용차", "석탄"],
    "imp_cnty": ["총수입", "중국", "미국", "유럽연합", "일본", "베트남",
                 "호주", "대만", "사우디아라비아", "러시아", "말레이시아"],
}
SERIES = {                       # key: (데이터셋 번호, 엔드포인트, 설명)
    "exp_item": ("15157908", "prlstMmUtPrviExpAcrs", "수출 주요품목별"),
    "exp_cnty": ("15157941", "cntyMmUtPrviExpAcrs", "수출 주요국가별"),
    "imp_item": ("15157901", "prlstMmUtPrviImpAcrs", "수입 주요품목별"),
    "imp_cnty": ("15157909", "cntyMmUtPrviImpAcrs", "수입 주요국가별"),
}
TOTAL = {"exp_item": "총수출", "exp_cnty": "총수출",
         "imp_item": "총수입", "imp_cnty": "총수입"}


def api_key() -> str:
    for line in open(ENV, encoding="utf-8"):
        if line.startswith("DATA_GO_KR_API_KEY") and not line.startswith("#"):
            return line.split("=", 1)[1].strip()
    sys.exit(f"DATA_GO_KR_API_KEY가 없다: {ENV}")


def fetch(key: str, ep: str, a: str, b: str) -> str:
    """디코딩 키를 urlencode로 감싼다. '+'를 그대로 두면 서버가 공백으로 읽는다."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    qs = urllib.parse.urlencode({"serviceKey": key, "strtYymm": a, "endYymm": b})
    url = f"{BASE}/{ep}/get{ep[0].upper() + ep[1:]}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=60, context=ctx) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", "replace")
        if "SERVICE_KEY_IS_NOT_REGISTERED" in body:
            sys.exit(f"{ep}: 활용신청이 안 되어 있다. "
                     f"공공데이터포털에서 신청할 것(자동승인).")
        raise


def parse(xml: str, series: str, fetched: dt.datetime) -> pd.DataFrame:
    names = ITEMS[series]
    rows = []
    for it in ET.fromstring(xml).findall(".//item"):
        d = {e.tag: (e.text or "").strip() for e in it}
        ym, pd_ = int(d["priodMon"]), d["priodDt"]
        cut = 10 if pd_.endswith("10") else 20 if pd_.endswith("20") else 99
        for num, name in enumerate(names):
            raw = d.get(f"itemUsdAmt{num:02d}", "").replace(",", "").strip()
            if raw:
                rows.append((series, ym, cut, pd_, name, int(raw), fetched))
    return pd.DataFrame(rows, columns=["series", "base_ym", "cutoff", "priod_dt",
                                       "item", "amt_kusd", "fetched_at"])


def migrate(con: duckdb.DuckDBPyConnection) -> None:
    """옛 스키마(series 없음, exp_kusd)를 옮긴다. vintage 이력은 그대로 둔다."""
    tabs = {r[0] for r in con.sql("SHOW TABLES").fetchall()}
    if "fact_exp10d" not in tabs:
        return
    cols = {r[0] for r in con.sql("DESCRIBE fact_exp10d").fetchall()}
    if "exp_kusd" in cols:
        con.execute("ALTER TABLE fact_exp10d RENAME COLUMN exp_kusd TO amt_kusd")
        print("  옮김: exp_kusd -> amt_kusd (수입도 담게 되어 이름을 중립으로)")
    if "series" not in cols:
        con.execute("ALTER TABLE fact_exp10d ADD COLUMN series VARCHAR")
        con.execute("UPDATE fact_exp10d SET series = 'exp_item' WHERE series IS NULL")
        n = con.sql("SELECT COUNT(*) FROM fact_exp10d").fetchone()[0]
        print(f"  옮김: series 열 추가, 기존 {n:,}행을 exp_item으로 표시")


def upsert(con: duckdb.DuckDBPyConnection, new: pd.DataFrame) -> int:
    """값이 바뀐 것만 새 행으로 쌓는다. 개정 이력이 남고 표가 부풀지 않는다."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS fact_exp10d (
            series VARCHAR, base_ym INTEGER, cutoff SMALLINT, priod_dt VARCHAR,
            item VARCHAR, amt_kusd BIGINT, fetched_at TIMESTAMP)""")
    con.register("new", new)
    where = """
        WITH last AS (   -- 계열·시점·항목마다 가장 최근에 본 값
            SELECT series, base_ym, cutoff, item, amt_kusd,
                   ROW_NUMBER() OVER (PARTITION BY series, base_ym, cutoff, item
                                      ORDER BY fetched_at DESC) rn
            FROM fact_exp10d)
        SELECT {sel} FROM new n
        LEFT JOIN (SELECT * FROM last WHERE rn = 1) l
          ON l.series = n.series AND l.base_ym = n.base_ym
         AND l.cutoff = n.cutoff AND l.item = n.item
        WHERE l.amt_kusd IS NULL OR l.amt_kusd <> n.amt_kusd"""
    n = con.sql(where.format(sel="COUNT(*)")).fetchone()[0]
    con.execute("INSERT INTO fact_exp10d BY NAME " + where.format(sel="n.*"))
    con.unregister("new")
    return n


def make_view(con: duckdb.DuckDBPyConnection) -> None:
    """누적치를 상순·중순·하순 증분으로 바꾸고 최신 관측만 남긴다."""
    con.execute("""
        CREATE OR REPLACE VIEW v_exp10d_seg AS
        WITH latest AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY series, base_ym, cutoff, item
                                         ORDER BY fetched_at DESC) rn
            FROM fact_exp10d),
        cum AS (SELECT series, base_ym, cutoff, item, amt_kusd
                FROM latest WHERE rn = 1)
        SELECT c.series, c.base_ym, c.cutoff,
               CASE c.cutoff WHEN 10 THEN '상순' WHEN 20 THEN '중순' ELSE '하순' END AS seg,
               c.item,
               c.amt_kusd - COALESCE(p.amt_kusd, 0) AS seg_kusd,   -- 구간 증분
               c.amt_kusd AS cum_kusd                              -- 누적(원본 그대로)
        FROM cum c
        LEFT JOIN cum p ON p.series = c.series AND p.base_ym = c.base_ym
                       AND p.item = c.item
                       AND p.cutoff = CASE c.cutoff WHEN 20 THEN 10 WHEN 99 THEN 20 END""")


def check(con: duckdb.DuckDBPyConnection, got: list[str]) -> None:
    """어긋나면 멈춘다. 번호 대응이 틀리면 여기서 걸린다."""
    # 1. 총계는 품목 계열과 국가 계열에서 같아야 한다.
    for d in ("exp", "imp"):
        if f"{d}_item" not in got or f"{d}_cnty" not in got:
            continue
        bad = con.sql(f"""
            SELECT COUNT(*) FROM
              (SELECT base_ym, cutoff, cum_kusd FROM v_exp10d_seg
               WHERE series='{d}_item' AND item='{TOTAL[d + "_item"]}') a
            JOIN
              (SELECT base_ym, cutoff, cum_kusd FROM v_exp10d_seg
               WHERE series='{d}_cnty' AND item='{TOTAL[d + "_cnty"]}') b
            USING (base_ym, cutoff)
            WHERE a.cum_kusd <> b.cum_kusd""").fetchone()[0]
        assert bad == 0, (f"{d}: 품목 계열과 국가 계열의 총계가 {bad}개 시점에서 다르다 "
                          f"- 번호 대응이나 갱신 시점을 의심할 것")
    # 2. 열 항목의 합이 총계를 넘으면 안 된다.
    over = con.sql("""
        WITH t AS (SELECT series, base_ym, cutoff,
                     SUM(cum_kusd) FILTER (WHERE item LIKE '총%') tot,
                     SUM(cum_kusd) FILTER (WHERE item NOT LIKE '총%') part
                   FROM v_exp10d_seg GROUP BY 1,2,3)
        SELECT COUNT(*) FROM t WHERE part > tot""").fetchone()[0]
    assert over == 0, f"열 항목의 합이 총계를 넘는 시점이 {over}개다"
    # 3. 계열마다 항목이 열하나여야 한다.
    bad = con.sql("""
        SELECT COUNT(*) FROM (
          SELECT series, base_ym, cutoff, COUNT(*) n FROM v_exp10d_seg
          GROUP BY 1,2,3) WHERE n <> 11""").fetchone()[0]
    assert bad == 0, f"항목이 11개가 아닌 시점이 {bad}개다"
    # 4. 월 전체 총계는 신성질별 공식 실적(fact_nqi)의 합과 같아야 한다.
    #    둘 다 관세청 공식 집계라 총액 층위에서는 일치한다. 품목으로는 못 내려간다
    #    - 10대 품목은 현행 수출 성질별이고 신성질별과 정의가 다르기 때문이다.
    nqi = con.sql("SELECT COUNT(*) FROM duckdb_tables() "
                  "WHERE table_name = 'fact_nqi'").fetchone()[0]
    if nqi:
        worst = con.sql("""
            WITH a AS (SELECT base_ym AS ym, series, MAX(amt_kusd) * 1000.0 AS v
                       FROM fact_exp10d WHERE cutoff = 99
                         AND item IN ('총수출', '총수입') GROUP BY 1, 2),
                 n AS (SELECT yyyymm AS ym, SUM(exp_dlr) AS e, SUM(imp_dlr) AS i
                       FROM fact_nqi GROUP BY 1)
            SELECT a.series, a.ym,
                   ABS(a.v / (CASE WHEN a.series LIKE 'exp%' THEN n.e
                                   ELSE n.i END) - 1) AS rel
            FROM a JOIN n USING (ym) ORDER BY rel DESC LIMIT 1""").fetchone()
        if worst:
            assert worst[2] < 1e-4, (
                f"{worst[0]} {worst[1]}: 월 전체 총계가 fact_nqi와 "
                f"{worst[2]:.2%} 어긋난다 - 어느 한쪽의 정정 반영 시점을 의심할 것")
            print(f"  신성질별 대조: 최대 격차 {worst[2]:.6%} ({worst[0]} {worst[1]})")
    else:
        print("  신성질별 대조: fact_nqi가 없어 건너뛴다 (03e/10 먼저 실행)")
    # 5. 품목별 월 전체값은 성질별 공식 실적(fact_temper)의 롤업과 같아야 한다.
    #    10대 품목이 현행 수출·수입 성질별 분류의 마디로 정의되어 있어, 부호를
    #    묶으면 같은 정의의 계열이 나온다. 총계만 재는 4번과 달리 품목까지 잰다.
    tmp = con.sql("SELECT COUNT(*) FROM duckdb_tables() "
                  "WHERE table_name = 'fact_temper'").fetchone()[0]
    if tmp:
        worst = con.sql("""
            WITH roll AS (
                SELECT f.yyyymm, f.imexp, d.major10 AS item, SUM(f.dlr) AS v
                FROM fact_temper f JOIN dim_temper d USING (imexp, temper_cd)
                WHERE d.major10 IS NOT NULL GROUP BY 1,2,3),
            ten AS (
                SELECT base_ym AS yyyymm,
                       CASE WHEN series LIKE 'exp%' THEN '수출' ELSE '수입' END AS imexp,
                       item, MAX(amt_kusd) * 1000.0 AS v
                FROM fact_exp10d
                WHERE cutoff = 99 AND series IN ('exp_item', 'imp_item')
                GROUP BY 1,2,3)
            SELECT r.yyyymm, r.imexp, r.item,
                   ABS(r.v / NULLIF(t.v, 0) - 1) AS rel
            FROM roll r JOIN ten t USING (yyyymm, imexp, item)
            ORDER BY rel DESC LIMIT 1""").fetchone()
        if worst:
            assert worst[3] < 1e-3, (
                f"{worst[1]} {worst[2]} {worst[0]}: 월 전체값이 성질별 롤업과 "
                f"{worst[3]:.2%} 어긋난다 - 품목 정의나 정정 반영을 의심할 것")
            print(f"  성질별 품목 대조: 최대 격차 {worst[3]:.6%} "
                  f"({worst[1]} {worst[2]} {worst[0]})")
    else:
        print("  성질별 품목 대조: fact_temper가 없어 건너뛴다 (11 먼저 실행)")
    print("검증 통과 — 총계 교차 일치, 부분합 <= 총계, 계열마다 항목 11개")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="2016년부터 전 기간")
    ap.add_argument("--series", default=",".join(SERIES),
                    help="쉼표로 구분한 계열 이름 (기본 넷 다)")
    a = ap.parse_args()
    want = [s.strip() for s in a.series.split(",") if s.strip()]
    for s in want:
        if s not in SERIES:
            sys.exit(f"모르는 계열: {s} (가능: {', '.join(SERIES)})")

    os.makedirs(RAW, exist_ok=True)
    key = api_key()
    now = dt.datetime.now().replace(microsecond=0)
    years = (range(FIRST_YEAR, now.year + 1) if a.full
             else range(now.year - 1, now.year + 1))
    stamp = now.strftime("%Y%m%d_%H%M%S")

    frames = []
    for s in want:
        did, ep, desc = SERIES[s]
        got = []
        for y in years:
            xml = fetch(key, ep, f"{y}01", f"{y}12")
            with open(os.path.join(RAW, f"{stamp}_{s}_{y}.xml"), "w",
                      encoding="utf-8") as f:
                f.write(xml)
            got.append(parse(xml, s, now))
        df = pd.concat(got, ignore_index=True)
        frames.append(df)
        print(f"  {s:9s} {desc} ({did}): {len(df):>5,}행")
    new = pd.concat(frames, ignore_index=True)

    con = duckdb.connect(DB)
    try:
        migrate(con)
        added = upsert(con, new)
        make_view(con)
        check(con, want)
        print(f"\n받은 {len(new):,}행 중 {added:,}행이 새 값 (나머지는 이전과 같아 건너뜀)")
        print(con.sql("""
            SELECT series AS 계열, COUNT(*) AS 행수, MIN(base_ym) AS 시작,
                   MAX(base_ym) AS 끝, COUNT(DISTINCT fetched_at) AS 관측시각
            FROM fact_exp10d GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
        print()
        last = con.sql("SELECT MAX(base_ym) FROM fact_exp10d").fetchone()[0]
        print(f"최근 시점 {last} 상위 항목 (억 달러, 구간 증분 아닌 누적)")
        print(con.sql(f"""
            SELECT series AS 계열, seg AS 구간, item AS 항목,
                   ROUND(cum_kusd / 1e5, 1) AS 억달러
            FROM v_exp10d_seg WHERE base_ym = {last} AND item NOT LIKE '총%'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY series, cutoff
                                       ORDER BY cum_kusd DESC) <= 3
            ORDER BY series, cutoff DESC, 억달러 DESC""").df().to_string(index=False))
    finally:
        con.close()


if __name__ == "__main__":
    main()
