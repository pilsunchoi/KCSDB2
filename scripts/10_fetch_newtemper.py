"""10_fetch_newtemper.py — 신성질별 국가별 수출입실적을 받아 적재한다.

자료
----
공공데이터포털 15101616 「관세청_신성질별 수출입실적(GW)」
  https://apis.data.go.kr/1220000/newtempertrade/getNewtempertradeList
  newtemper = 신(new)성질(temper). 같은 규약으로 성질별은 tempertrade이나
  그쪽은 구 분류(수출 151·수입 180개로 수출입 체계가 다르다)라 쓰지 않는다.

  필수 변수는 **imexTpcd**(1=수출, 2=수입)다. 이것을 빼면 "필수 요청변수가
  누락되었습니다"가 온다. strtYymm·endYymm은 여러 달을 한 번에 받을 수 있고
  **페이지네이션이 없다**(numOfRows·pageNo를 줘도 전량이 온다). 그래서 연 단위로
  나눠 받는다 — 연×방향 44회면 2005년부터 전 기간이 끝난다.

  **데이터셋 설명보다 넓다.** 설명에는 국가 축이 없는 것처럼 적혀 있으나 응답에
  statCd(국가코드)와 wgt(중량)가 들어 있다. 2005년 1월부터 제공된다.

왜 받는가 — 우리 도출로는 안 되기 때문이다
-------------------------------------------
`dim_hs10_to_nqi`로 `fact_trade`를 재집계하면 신성질별 계열을 만들 수 있다. 그런데
공식치와 맞대 보니 **총액은 맞는데 세세분류 배분이 옛 연도에서 크게 어긋난다.**

    절대오차합 / 총액 (수출)
    2007.01  25.8%    2010.01  11.7%    2013.01   8.9%
    2017.01   6.1%    2022.01   0.38%   2026.01   0.17%

이유는 분명하다. 현행 HS2022 코드에서 nqi5로 가는 `direct`(11,327행)만 관세청 공식
대응이고, 옛 코드는 우리가 추정한 `dim_hs10_to_2022`를 거치는 `chain`(6,038행)이라
**그 추정 오차를 그대로 물려받는다**(2007~2008년 수출액의 16~18%가 chain 경로다).

그러니 이 표는 두 가지를 준다.
  (1) **2005년부터의 정확한 신성질별 계열** — 우리가 만들 수 없는 것.
  (2) **`dim_hs10_to_2022`를 검증할 외부 기준** — 마트 `mart_nqi_check`가 남긴다.

만드는 것
---------
fact_nqi(yyyymm, stat_cd, nqi5, exp_dlr, exp_wgt, imp_dlr, imp_wgt)
  fact_trade와 같은 가로 형태로 둔다. 원본은 수출·수입이 세로로 오지만 값을 바꾸는
  것이 아니라 모양만 맞추는 것이고, 이렇게 해야 fact_trade와 같은 감각으로 질의된다.
  **범위가 fact_trade와 다르다** — 이 표만 2005.01부터다(fact_trade는 2007.01~).

mart_nqi_check(yyyymm, official, derived, abs_err, err_ratio, n_code_off, n_code_der)
  월별로 공식치와 우리 도출치를 맞댄 것. `err_ratio`가 배분이 얼마나 어긋났는지다.

원본 XML은 data/raw/newtemper/에 남기는데 **전 기간이면 1.8GB다.** 01이 받는 fact_trade
원본과 달리 이쪽은 44회·12분이면 다시 받을 수 있으므로 버려도 된다. 처음부터 안 남기려면
--no-raw를 준다.

실행: python scripts/10_fetch_newtemper.py            # 2005년부터 전 기간
      python scripts/10_fetch_newtemper.py --from 2024   # 최근만 다시
      python scripts/10_fetch_newtemper.py --no-raw      # 원본 XML을 남기지 않는다
      python scripts/10_fetch_newtemper.py --reload-only # 검증·마트만 다시
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import duckdb
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "processed", "kcsdb.duckdb")
RAW = os.path.join(ROOT, "data", "raw", "newtemper")
ENV = os.path.join(ROOT, "config", "api_key.env")
URL = "https://apis.data.go.kr/1220000/newtempertrade/getNewtempertradeList"
FIRST_YEAR = 2005
DIRS = {"1": "수출", "2": "수입"}


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
            sys.exit("활용신청이 안 되어 있다. 공공데이터포털 15101616에서 신청할 것.")
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
        ym = int(d["year"].replace(".", ""))
        rows.append((ym, d["statCd"], d["godsCd"], d["impexp"],
                     int(d["dlr"] or 0), int(d["wgt"] or 0)))
    return pd.DataFrame(rows, columns=["yyyymm", "stat_cd", "nqi5",
                                       "impexp", "dlr", "wgt"])


def widen(long: pd.DataFrame) -> pd.DataFrame:
    """수출·수입이 세로로 오는 것을 fact_trade와 같은 가로 형태로 바꾼다."""
    long = long.copy()
    long["side"] = np.where(long.impexp == "수출", "exp", "imp")
    g = long.groupby(["yyyymm", "stat_cd", "nqi5", "side"], as_index=False)[
        ["dlr", "wgt"]].sum()
    w = g.pivot_table(index=["yyyymm", "stat_cd", "nqi5"], columns="side",
                      values=["dlr", "wgt"], fill_value=0)
    w.columns = [f"{s}_{v[:3]}" for v, s in w.columns]
    w = w.reset_index()
    for c in ("exp_dlr", "exp_wgt", "imp_dlr", "imp_wgt"):
        if c not in w:
            w[c] = 0
    return w[["yyyymm", "stat_cd", "nqi5",
              "exp_dlr", "exp_wgt", "imp_dlr", "imp_wgt"]]


def build_check(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """공식치와 우리 도출치를 월별로 맞댄다. dim_hs10_to_2022 검증의 외부 기준이다."""
    return con.sql("""
        WITH off AS (SELECT yyyymm, nqi5, SUM(exp_dlr) v FROM fact_nqi GROUP BY 1,2),
             der AS (SELECT f.yyyymm, n.nqi5, SUM(f.exp_dlr*n.weight) v
                     FROM fact_trade f JOIN dim_hs10_to_nqi n ON n.hs10=f.hs10
                     GROUP BY 1,2),
             j AS (SELECT COALESCE(o.yyyymm,d.yyyymm) yyyymm,
                          COALESCE(o.nqi5,d.nqi5) nqi5,
                          COALESCE(o.v,0) o, COALESCE(d.v,0) d
                   FROM off o FULL JOIN der d USING (yyyymm, nqi5))
        SELECT yyyymm,
               SUM(o) AS official, SUM(d) AS derived,
               SUM(abs(d-o)) AS abs_err,
               SUM(abs(d-o))/NULLIF(SUM(o),0) AS err_ratio,
               COUNT(*) FILTER (WHERE o>0) AS n_code_off,
               COUNT(*) FILTER (WHERE d>0) AS n_code_der
        FROM j GROUP BY 1 HAVING SUM(o) > 0 ORDER BY 1""").df()


def check(con: duckdb.DuckDBPyConnection) -> None:
    """어긋나면 멈춘다."""
    n, ymin, ymax = con.sql(
        "SELECT COUNT(*), MIN(yyyymm), MAX(yyyymm) FROM fact_nqi").fetchone()
    assert n > 1_000_000, f"행이 {n:,}개뿐이다 - 받다 말았는지 볼 것"
    # 1. 행이 유일한가
    dup = con.sql("""SELECT COUNT(*) FROM (SELECT yyyymm, stat_cd, nqi5, COUNT(*) c
                     FROM fact_nqi GROUP BY 1,2,3 HAVING c > 1)""").fetchone()[0]
    assert dup == 0, f"(연월, 국가, 신성질) 중복이 {dup}건"
    # 2. 코드가 우리 차원에 다 있는가
    # dim_nqi는 현행 분류표의 스냅숏이라 폐지코드가 없다. hs10 폐지코드와 같은
    # 사정이다(봉인1). 실제로 열 개가 걸리는데 대부분 2021.12에 끝났고 거래액의
    # 0.07%라 결함이 아니다. **신성질별도 개정이 있다**는 증거로 읽어야 한다.
    bad = con.sql("""SELECT COUNT(DISTINCT nqi5) FROM fact_nqi f
                     WHERE NOT EXISTS (SELECT 1 FROM dim_nqi d WHERE d.nqi5=f.nqi5)"""
                  ).fetchone()[0]
    if bad:
        pct = con.sql("""SELECT SUM(CASE WHEN d.nqi5 IS NULL THEN f.exp_dlr ELSE 0 END)
                                / SUM(f.exp_dlr) * 100
                         FROM fact_nqi f LEFT JOIN dim_nqi d USING (nqi5)""").fetchone()[0]
        assert pct < 1.0, f"dim_nqi 미등재 코드가 수출액의 {pct:.2f}% - 너무 크다"
        print(f"  참고: dim_nqi에 없는 신성질 코드 {bad}개 (폐지코드, 수출액의 {pct:.3f}%)")
    miss = con.sql("""SELECT COUNT(DISTINCT stat_cd) FROM fact_nqi f
                      WHERE NOT EXISTS (SELECT 1 FROM dim_country c
                                        WHERE c.stat_cd=f.stat_cd)""").fetchone()[0]
    if miss:
        print(f"  참고: dim_country에 없는 국가코드 {miss}개 (관세청 자체 코드일 수 있다)")
    # 3. 총액이 fact_trade와 맞는가. 겹치는 기간만 본다.
    d = con.sql("""
        SELECT a.yyyymm, a.e AS nqi_e, b.e AS trade_e
        FROM (SELECT yyyymm, SUM(exp_dlr) e FROM fact_nqi GROUP BY 1) a
        JOIN (SELECT yyyymm, SUM(exp_dlr) e FROM fact_trade GROUP BY 1) b
          USING (yyyymm)""").df()
    gap = (d.nqi_e / d.trade_e - 1).abs()
    assert gap.max() < 0.01, (f"총액이 fact_trade와 최대 {gap.max()*100:.2f}% 어긋난다 "
                              f"({int(d.loc[gap.idxmax(),'yyyymm'])})")
    print(f"검증 통과 — {n:,}행 {ymin}~{ymax}, 행 유일, 코드 일치, "
          f"총액 최대 격차 {gap.max()*100:.3f}%")


def report(con: duckdb.DuckDBPyConnection, chk: pd.DataFrame) -> None:
    print("\n[1] 우리 도출치가 공식치와 얼마나 어긋나나 (수출, 연 평균)")
    c = chk.copy(); c["yr"] = c.yyyymm // 100
    g = c.groupby("yr").agg(공식억=("official", lambda x: x.sum() / 1e8),
                            절대오차합_비율=("err_ratio", "mean"),
                            합계차=("derived", "sum")).reset_index()
    g["합계차%"] = (g.합계차 / (c.groupby("yr").official.sum().values) - 1) * 100
    g["배분오차%"] = g.절대오차합_비율 * 100
    # fact_trade가 2007년부터라 그 전은 도출 자체가 안 된다. -100%로 찍히면
    # 오류처럼 보이므로 빈칸으로 둔다.
    # DuckDB의 /는 실수 나눗셈이라 200701/100 = 2007.01이 된다. 그대로 견주면
    # 2007년이 "도출 불가"로 잘못 찍힌다. 정수 나눗셈으로 자른다.
    first = con.sql("SELECT MIN(yyyymm) // 100 FROM fact_trade").fetchone()[0]
    g.loc[g.yr < first, ["합계차%", "배분오차%"]] = np.nan
    print(g[["yr", "공식억", "합계차%", "배분오차%"]].round(2)
          .to_string(index=False, na_rep="(도출 불가)"))
    print("\n    합계는 잘 맞는데 배분이 어긋난다는 것이 이 표의 요지다.")
    print(f"    {int(first)}년 이전은 fact_trade가 없어 도출할 수 없다 — "
          f"이 표만 갖는 기간이다.")

    print("\n[2] 신성질 대분류별 최근 12개월 (억 달러)")
    print(con.sql("""
        SELECT d.nqi1_nm AS 대분류,
               ROUND(SUM(f.exp_dlr)/1e8, 1) AS 수출,
               ROUND(SUM(f.imp_dlr)/1e8, 1) AS 수입
        FROM fact_nqi f JOIN dim_nqi d USING (nqi5)
        WHERE f.yyyymm > (SELECT MAX(yyyymm)-100 FROM fact_nqi)
        GROUP BY 1 ORDER BY 수출 DESC""").df().to_string(index=False))

    print("\n[3] 우리가 못 만들던 것 — 신성질 x 국가 (최근 12개월 수출 상위)")
    print(con.sql("""
        SELECT d.nqi5_nm AS 세세분류, c.name_ko_kcs AS 국가,
               ROUND(SUM(f.exp_dlr)/1e8, 1) AS 억달러
        FROM fact_nqi f JOIN dim_nqi d USING (nqi5)
        JOIN dim_country c USING (stat_cd)
        WHERE f.yyyymm > (SELECT MAX(yyyymm)-100 FROM fact_nqi)
        GROUP BY 1,2 ORDER BY 억달러 DESC LIMIT 8""").df().to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", type=int, default=FIRST_YEAR,
                    help="이 연도부터 받는다 (기본 2005)")
    ap.add_argument("--reload-only", action="store_true",
                    help="받지 않고 이미 적재된 fact_nqi로 검증·마트만 다시 만든다")
    ap.add_argument("--no-raw", action="store_true",
                    help="원본 XML을 남기지 않는다 (전 기간이면 1.8GB다)")
    a = ap.parse_args()
    if a.reload_only:
        con = duckdb.connect(DB)
        try:
            check(con)
            chk = build_check(con)
            con.register("_c", chk)
            con.execute("CREATE OR REPLACE TABLE mart_nqi_check AS SELECT * FROM _c")
            con.unregister("_c")
            report(con, chk)
            print(f"\n적재\n  {'mart_nqi_check':22s} {len(chk):>9,}행")
        finally:
            con.close()
        return
    os.makedirs(RAW, exist_ok=True)
    key = api_key()
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    years = range(a.frm, dt.date.today().year + 1)

    frames = []
    for y in years:
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
    wide = widen(long)
    print(f"\n세로 {len(long):,}행 -> 가로 {len(wide):,}행")

    con = duckdb.connect(DB)
    try:
        con.register("_w", wide)
        if a.frm > FIRST_YEAR:      # 부분 갱신이면 해당 연도만 갈아 끼운다
            con.execute(f"DELETE FROM fact_nqi WHERE yyyymm >= {a.frm}01")
            con.execute("INSERT INTO fact_nqi SELECT * FROM _w")
        else:
            con.execute("CREATE OR REPLACE TABLE fact_nqi AS SELECT * FROM _w")
        con.unregister("_w")
        check(con)
        chk = build_check(con)
        con.register("_c", chk)
        con.execute("CREATE OR REPLACE TABLE mart_nqi_check AS SELECT * FROM _c")
        con.unregister("_c")
        report(con, chk)
        print(f"\n적재\n  {'fact_nqi':22s} "
              f"{con.sql('SELECT COUNT(*) FROM fact_nqi').fetchone()[0]:>9,}행")
        print(f"  {'mart_nqi_check':22s} {len(chk):>9,}행")
    finally:
        con.close()


if __name__ == "__main__":
    main()
