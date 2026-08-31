"""11_fetch_temper.py — 성질별 수출입실적(관세청 공식)을 받아 적재한다.

왜 필요한가
-----------
10일 단위 잠정치의 10대 품목은 **현행 수출 성질별 분류**의 마디로 정의된다.
그래서 이 계열만이 10일 자료를 품목 단위로 검증할 수 있다. 신성질별(`fact_nqi`)은
총액은 맞지만 품목 정의가 달라 반도체 0.3~0.7%, 선박 2%가 상시 어긋난다.
`dim_hs10_to_major10`을 거치는 기존 대조는 **우리 매핑이 맞는지**를 재는 것이라
매핑이 틀리면 무엇이 틀렸는지 가려낼 수 없다. 이쪽은 관세청이 이미 그 단위로
집계해 둔 값이라 외부 기준이 된다.

덤으로 이 표는 **1995년부터**다. fact_trade(2007~)나 fact_nqi(2005~)보다 훨씬 길다.

자료
----
공공데이터포털 15102109(관세청_성질별 수출입실적(GW)). 엔드포인트 `Idfytempertrade`.
필수 변수는 `imexTpcd`(1=수출/2=수입)이고 조회는 1년까지다. 연 단위로 나누면
32년 x 2방향 = 64회로 끝난다. 국가별이 필요하면 15100476(`ntempertrade`)이 따로
있는데 `cntyCd`가 필수라 전 국가를 돌면 호출이 수천 회가 된다.

**수출과 수입은 분류 체계가 다르다.** 수출 153부호, 수입 180부호이고 나무가
아예 다르다 — 같은 부호 11201이 수출에서는 기타 육류, 수입에서는 쌀이다.
그래서 fact를 가로로 눕히지 않고 `imexp`를 키에 둔 세로 형태로 담는다.
`fact_nqi`와 다른 점이 이것이다.

만드는 것
---------
fact_temper  (yyyymm, imexp, temper_cd, dlr, wgt). 관세청 응답 필드만.
dim_temper   부호별 이름과 계층(1단위/3단위/소분류/세분류), 그리고 10대 품목 꼬리표.
             이름은 API 응답에서(폐지부호까지 나온다), 계층은 신성질별 별표에서 온다.

실행: python scripts/11_fetch_temper.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "processed", "kcsdb.duckdb")
RAW = os.path.join(ROOT, "data", "raw", "temper")
ENV = os.path.join(ROOT, "config", "api_key.env")
SRC = os.path.join(ROOT, "data", "external",
                   "관세청_HSK별_신성질별_20260101.xlsx")
URL = "https://apis.data.go.kr/1220000/Idfytempertrade/getIdfytempertradeList"
FIRST_YEAR = 1995
DIRS = {"1": "수출", "2": "수입"}

# 10대 품목이 어느 마디인가. 항목마다 계층이 달라 (열, 값)으로 적는다.
# 이름은 10일 자료(fact_exp10d)의 item과 정확히 같게 맞춘다 — 대조가 이름으로 붙는다.
MAJOR10 = {
    "수출": [("반도체", "x3", "- 반도체"),
             ("승용차", "x4", "- 승용자동차"),
             ("철강제품", "x2", "나. 철강제품"),
             ("석유제품", "x3", "- 석유제품"),
             ("선박", "x4", "- 선 박"),
             ("자동차부품", "x4", "- 자동차 부품"),
             ("무선통신기기", "x4", "(무선통신기기)"),
             ("컴퓨터주변기기", "x4", "(컴퓨터 주변기기)"),
             ("정밀기기", "x4", "- 정밀기기"),
             ("가전제품", "x3", "- 가전제품")],
    "수입": [("반도체", "x3", "- 반도체"),
             ("원유", "x4", "(원 유)"),
             ("기계류", "x3", "- 기계류"),
             ("가스", "x4", "(가 스)"),
             ("반도체제조장비", "x3", "- 반도체 제조용 장비"),
             ("정밀기기", "x3", "- 정밀기기"),
             ("석유제품", "x3", "- 석유제품"),
             ("무선통신기기", "x4", "(무선통신기기)"),
             ("승용차", "x4", "(승용차)"),
             ("석탄", "x4", "(석 탄)")],
}


def api_key() -> str:
    for line in open(ENV, encoding="utf-8"):
        if line.startswith("DATA_GO_KR_API_KEY") and not line.startswith("#"):
            return line.split("=", 1)[1].strip()
    sys.exit(f"DATA_GO_KR_API_KEY가 없다: {ENV}")


def fetch(key: str, year: int, tp: str) -> str:
    qs = urllib.parse.urlencode({"serviceKey": key, "strtYymm": f"{year}01",
                                 "endYymm": f"{year}12", "imexTpcd": tp})
    try:
        with urllib.request.urlopen(f"{URL}?{qs}", timeout=180) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", "replace")
        if "SERVICE_KEY_IS_NOT_REGISTERED" in body:
            sys.exit("활용신청이 안 되어 있다. 공공데이터포털 15102109에서 신청할 것.")
        raise


def parse(xml: str) -> pd.DataFrame:
    root = ET.fromstring(xml)
    msg = root.findtext(".//resultMsg") or ""
    if "정상" not in msg:
        sys.exit(f"응답이 정상이 아니다: {msg}")
    rows = []
    for it in root.findall(".//item"):
        d = {e.tag: (e.text or "").strip() for e in it}
        # year가 '2026.01' 꼴이다. 숫자만 남겨 yyyymm으로 만든다.
        rows.append((int(d["year"].replace(".", "")), d["impexp"], d["godsCd"],
                     d.get("godsKor", ""), int(d["dlr"] or 0), int(d["wgt"] or 0)))
    return pd.DataFrame(rows, columns=["yyyymm", "imexp", "temper_cd",
                                       "name_ko", "dlr", "wgt"])


def hierarchy() -> pd.DataFrame:
    """별표에서 방향별 계층을 읽는다. 부호마다 경로가 하나로 정해진다."""
    d = pd.ExcelFile(SRC).parse("2026년", dtype=str)
    out = []
    for lab, ko in (("수출", "수출"), ("수입", "수입")):
        R = f"관세청 현행 {ko} 성질별 분류현행{ko}"
        cols = {R + "성질부호": "temper_cd", R + "1단위분류": "x1",
                R + "3단위분류": "x2", R + "소분류": "x3", R + "세분류": "x4"}
        t = d.rename(columns=cols)[list(cols.values())]
        t = t.dropna(subset=["temper_cd"]).drop_duplicates("temper_cd")
        t.insert(0, "imexp", lab)
        out.append(t)
    h = pd.concat(out, ignore_index=True)
    dup = h.groupby(["imexp", "temper_cd"]).size().max()
    assert dup == 1, f"부호 하나가 여러 경로를 갖는다 - 별표를 볼 것 ({dup}중)"
    return h


def build_dim(long: pd.DataFrame) -> pd.DataFrame:
    """부호 사전. 이름은 최근 관측을 쓴다 - 폐지부호는 API에만 있다."""
    nm = (long.sort_values("yyyymm").groupby(["imexp", "temper_cd"], as_index=False)
          .name_ko.last())
    dim = nm.merge(hierarchy(), on=["imexp", "temper_cd"], how="left")
    dim["major10"] = None
    for lab, spec in MAJOR10.items():
        for item, col, val in spec:
            hit = (dim.imexp == lab) & (dim[col] == val)
            assert hit.any(), f"{lab} {item}: 별표에서 '{val}'을 못 찾았다"
            dim.loc[hit, "major10"] = item
    return dim[["imexp", "temper_cd", "name_ko", "x1", "x2", "x3", "x4",
                "major10"]]


def check(con: duckdb.DuckDBPyConnection) -> None:
    """어긋나면 멈춘다. 10일 자료와의 대조가 이 스크립트의 목적이다."""
    n, ymin, ymax = con.sql(
        "SELECT COUNT(*), MIN(yyyymm), MAX(yyyymm) FROM fact_temper").fetchone()
    assert n > 100_000, f"행이 {n:,}개뿐이다 - 받다 말았는지 볼 것"
    # 1. 행이 유일한가.
    dup = con.sql("""SELECT COUNT(*) FROM (SELECT yyyymm, imexp, temper_cd,
                     COUNT(*) c FROM fact_temper GROUP BY 1,2,3 HAVING c > 1)"""
                  ).fetchone()[0]
    assert dup == 0, f"(연월, 방향, 부호) 중복이 {dup}건"
    # 2. 총액이 fact_nqi와 맞는가. 겹치는 기간만 본다.
    #    **fact_trade와 맞대면 안 된다.** 우리 월 자료는 국가별로 받아 적재해서
    #    국적이 정해지지 않은 몫(AS·GS·TK·Z1·ZZ)이 빠져 있다. 그래서 이쪽이
    #    늘 크게 나오고 2022.11 수입은 3%까지 벌어진다. fact_nqi는 같은 방식으로
    #    관세청이 집계한 것이라 이것과 맞대는 것이 옳다.
    d = con.sql("""
        SELECT a.yyyymm, a.e, a.i, b.qe, b.qi FROM
          (SELECT yyyymm,
             SUM(dlr) FILTER (WHERE imexp='수출') AS e,
             SUM(dlr) FILTER (WHERE imexp='수입') AS i
           FROM fact_temper GROUP BY 1) a
        JOIN (SELECT yyyymm, SUM(exp_dlr) qe, SUM(imp_dlr) qi
              FROM fact_nqi GROUP BY 1) b USING (yyyymm)""").df()
    assert len(d), "fact_nqi와 겹치는 달이 없다 - 10을 먼저 돌릴 것"
    for col, ref, lab in (("e", "qe", "수출"), ("i", "qi", "수입")):
        gap = (d[col] / d[ref] - 1).abs()
        assert gap.max() < 0.001, (
            f"{lab} 총액이 fact_nqi와 최대 {gap.max()*100:.3f}% 어긋난다 "
            f"({int(d.loc[gap.idxmax(), 'yyyymm'])})")
    # 3. 10대 품목이 10일 자료와 맞는가 — 이것이 목적이다.
    worst = major10_gap(con)
    if worst is None:
        print("  참고: fact_exp10d가 없어 10대 품목 대조를 건너뛴다")
    else:
        bad = worst[worst.rel > 0.001]
        assert bad.empty, ("10대 품목이 10일 자료와 어긋난다:\n"
                           + bad.head(10).to_string(index=False))
        mx = worst.loc[worst.rel.idxmax()]
        # Series.item은 열이 아니라 메서드다. mx.item으로 꺼내면 bound method가
        # 그대로 찍힌다 - 실제로 한 번 그렇게 찍혔다.
        print(f"  10대 품목 대조: {len(worst):,}쌍 전부 0.1% 이내, 최대 "
              f"{mx.rel*100:.4f}% ({int(mx.yyyymm)} {mx.imexp} {mx['item']})")
    print(f"검증 통과 — {n:,}행 {ymin}~{ymax}, 행 유일, fact_nqi와 총액 일치")


def major10_gap(con: duckdb.DuckDBPyConnection) -> pd.DataFrame | None:
    """10일 자료의 월 전체값과 성질별 롤업을 맞댄다. 없으면 None."""
    if not con.sql("SELECT COUNT(*) FROM duckdb_tables() "
                   "WHERE table_name='fact_exp10d'").fetchone()[0]:
        return None
    return con.sql("""
        WITH roll AS (
            SELECT f.yyyymm, f.imexp, d.major10 AS item, SUM(f.dlr) AS v
            FROM fact_temper f JOIN dim_temper d USING (imexp, temper_cd)
            WHERE d.major10 IS NOT NULL GROUP BY 1,2,3
            UNION ALL
            SELECT yyyymm, imexp,
                   CASE WHEN imexp='수출' THEN '총수출' ELSE '총수입' END,
                   SUM(dlr) FROM fact_temper GROUP BY 1,2,3),
        ten AS (
            SELECT base_ym AS yyyymm,
                   CASE WHEN series LIKE 'exp%' THEN '수출' ELSE '수입' END AS imexp,
                   item, MAX(amt_kusd) * 1000.0 AS v
            FROM fact_exp10d WHERE cutoff = 99 AND series IN ('exp_item','imp_item')
            GROUP BY 1,2,3)
        SELECT r.yyyymm, r.imexp, r.item, r.v AS temper, t.v AS ten,
               ABS(r.v / NULLIF(t.v, 0) - 1) AS rel
        FROM roll r JOIN ten t USING (yyyymm, imexp, item)
        ORDER BY rel DESC""").df()


def report(con: duckdb.DuckDBPyConnection) -> None:
    print("\n[1] 이 표만 갖는 기간 (fact_trade는 2007년부터다)")
    print(con.sql("""
        SELECT yyyymm // 100 AS 연도,
               ROUND(SUM(dlr) FILTER (WHERE imexp='수출')/1e8, 0) AS 수출억,
               ROUND(SUM(dlr) FILTER (WHERE imexp='수입')/1e8, 0) AS 수입억
        FROM fact_temper WHERE yyyymm < 200701
        GROUP BY 1 ORDER BY 1""").df().to_string(index=False))

    print("\n[2] 10대 수출품목 최근 12개월 (억 달러)")
    print(con.sql("""
        SELECT d.major10 AS 품목, ROUND(SUM(f.dlr)/1e8, 1) AS 억달러
        FROM fact_temper f JOIN dim_temper d USING (imexp, temper_cd)
        WHERE f.imexp='수출' AND d.major10 IS NOT NULL
          AND f.yyyymm > (SELECT MAX(yyyymm)-100 FROM fact_temper)
        GROUP BY 1 ORDER BY 2 DESC""").df().to_string(index=False))

    print("\n[3] 부호 수와 계층 미등재 (별표는 현행 스냅숏이라 폐지부호가 없다)")
    print(con.sql("""
        SELECT imexp AS 방향, COUNT(*) AS 부호,
               COUNT(*) FILTER (WHERE x1 IS NULL) AS 계층없음,
               COUNT(*) FILTER (WHERE major10 IS NOT NULL) AS 십대품목
        FROM dim_temper GROUP BY 1 ORDER BY 1""").df().to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", type=int, default=FIRST_YEAR,
                    help="이 연도부터 받는다 (기본 1995)")
    ap.add_argument("--reload-only", action="store_true",
                    help="받지 않고 이미 적재된 fact_temper로 검증만 다시 한다")
    ap.add_argument("--no-raw", action="store_true", help="원본 XML을 남기지 않는다")
    a = ap.parse_args()

    if a.reload_only:
        con = duckdb.connect(DB)
        try:
            check(con)
            report(con)
        finally:
            con.close()
        return

    os.makedirs(RAW, exist_ok=True)
    key = api_key()
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    frames = []
    for y in range(a.frm, dt.date.today().year + 1):
        got = []
        for tp, lab in DIRS.items():
            xml = fetch(key, y, tp)
            if not a.no_raw:
                with open(os.path.join(RAW, f"{stamp}_{y}_{lab}.xml"), "w",
                          encoding="utf-8") as f:
                    f.write(xml)
            got.append(parse(xml))
        df = pd.concat(got, ignore_index=True)
        frames.append(df)
        print(f"  {y}: {len(df):>7,}행")
    long = pd.concat(frames, ignore_index=True)
    dim = build_dim(long)
    fact = long[["yyyymm", "imexp", "temper_cd", "dlr", "wgt"]]

    con = duckdb.connect(DB)
    try:
        con.register("_f", fact)
        con.register("_d", dim)
        if a.frm > FIRST_YEAR:      # 부분 갱신이면 해당 연도만 갈아 끼운다
            con.execute(f"DELETE FROM fact_temper WHERE yyyymm >= {a.frm}01")
            con.execute("INSERT INTO fact_temper SELECT * FROM _f")
        else:
            con.execute("CREATE OR REPLACE TABLE fact_temper AS SELECT * FROM _f")
            con.execute("CREATE OR REPLACE TABLE dim_temper AS SELECT * FROM _d")
        con.unregister("_f")
        con.unregister("_d")
        check(con)
        report(con)
        print(f"\n적재\n  {'fact_temper':16s} "
              f"{con.sql('SELECT COUNT(*) FROM fact_temper').fetchone()[0]:>9,}행")
        print(f"  {'dim_temper':16s} "
              f"{con.sql('SELECT COUNT(*) FROM dim_temper').fetchone()[0]:>9,}행")
    finally:
        con.close()


if __name__ == "__main__":
    main()
