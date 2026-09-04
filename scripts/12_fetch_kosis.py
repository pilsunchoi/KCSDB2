"""12_fetch_kosis.py — 광공업생산지수와 경기종합지수(국가데이터처 KOSIS)를 받아 적재한다.

왜 필요한가
-----------
무역 트렌드 연구의 둘째 갈래가 수출입을 산업생산과 대조한다. 성질별 코드(품목군
열다섯)와 한국표준산업분류(KSIC) 중분류·소분류를 맞대려면 산업별 생산지수가 소분류
(3자리, 예: C261 반도체 제조업)까지 있어야 하고, 셋째 갈래(10일 잠정치의 nowcasting)는
경기종합지수와 그 구성지표를 기준 모형의 재료로 쓴다.

자료 (2026-09-02 탐침)
----------------------
KOSIS 공유서비스 OpenAPI. 기관코드 101(국가데이터처, 옛 통계청). 키는
`config/api_key.env`의 `KOSIS_API_KEY`.

* DT_1F02001 시도/산업별 광공업생산지수(2020=100), 1975.01~. 전국(00)만 받는다.
  산업 115항목 = 총지수 1 + 광업·제조업 합계 1 + 대분류 3 + 중분류 29 + 소분류 81.
  **소분류까지 1995.01부터 전부 있다**(2020년에 신설된 다섯과 D353 증기온수 2010~만
  예외). 항목은 생산·출하·재고 × 원지수·계절조정 여섯.
* DT_1F02016 내수/수출 광공업출하지수(2020=100), 1985.01~. 중분류까지이고 소분류는
  C26(전자부품·컴퓨터·통신) 다섯만 있다. 수출출하지수는 산업별 수출 물량에 가장
  가까운 공식 계열이다.
* DT_1F02061 명절과조업효과조정 광공업생산지수, 1990.01~. 총지수 하나뿐이다.
* DT_1C8015 경기종합지수(2020=100)(10차), 1970.01~. 선행·동행·후행 지수와 순환변동치,
  구성지표의 전월비.
* DT_1C8016 경기종합지수 구성지표 시계열(10차), 1970.01~. 구성지표의 원계열 수준.
  **구성지표는 2003.01부터만 있다**(지수 자체는 1970부터).

호출은 표 × 항목 × 10년 창으로 나눈다. 한 번에 2만 행 넘게도 오지만 한도를 문서에서
찾지 못했으므로 창을 잘라 안전하게 간다. 전부 서른 번 안쪽이고 1분이면 끝난다.

만드는 것
---------
fact_ip   (yyyymm, ksic, measure, value). measure는 아래 열한 가지.
            prod / prod_sa      생산지수 원지수 / 계절조정
            ship / ship_sa      생산자제품 출하지수
            inv  / inv_sa       생산자제품 재고지수
            ship_dom / ship_dom_sa   내수출하지수
            ship_exp / ship_exp_sa   수출출하지수
            prod_cal            명절·조업일수 조정 생산지수(총지수만)
dim_ksic  (ksic, name_ko, name_en, level, parent, wgt). level 0 총지수, 1 대분류(A는
            광업+제조업 합계), 2 중분류, 3 소분류. wgt는 올해 지수 작성 가중치(총지수
            10000) — 연쇄 라스파이레스라 과거 해에 그대로 쓸 수는 없다.
fact_cli  (yyyymm, tbl, cli_cd, value). tbl은 DT_1C8015 / DT_1C8016.
dim_cli   (tbl, cli_cd, name_ko, name_en).

주의
----
* 경기종합지수 구성지표의 「광공업생산지수」(B0201)는 DT_1F02001의 계절조정 총지수와
  **같지 않다**(최대 7.7p 차이, 상관 0.99 이상). 경기종합지수 편제에서 따로 조정한
  계열이라 그렇다. 검증은 상관으로만 한다.
* 계절조정 계열은 해마다 다시 추정되어 과거값이 바뀐다. 그래서 부분 갱신 없이 늘
  전부 다시 받는다.
* KOSIS 값에 '-'가 섞여 온다(결측). 숫자로 못 바꾸는 값은 버린다.
* 표 주석(CMMT)에서 확인한 것: 2015년부터 10차 산업분류 기준이라 장기 시계열은
  참고할 것, 전국지수는 연쇄 라스파이레스, 최근 2개월은 잠정치, 매년 1월분 공표 때
  연간보정으로 최근 몇 해가 수정된다, 2020년 이전 지수는 소수 셋째 자리까지.

실행: python scripts/12_fetch_kosis.py            (받고 적재하고 검증)
      python scripts/12_fetch_kosis.py --reload-only   (적재된 표로 검증만)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request

import duckdb
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "processed", "kcsdb.duckdb")
RAW = os.path.join(ROOT, "data", "raw", "kosis")
ENV = os.path.join(ROOT, "config", "api_key.env")
URL_DATA = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
URL_META = "https://kosis.kr/openapi/statisticsData.do"
ORG = "101"
FIRST_IP = 1995        # 우리 무역 자료(fact_temper)가 1995년부터라 여기에 맞춘다
FIRST_CLI = 1970

# (표, 분류 인자, {항목: measure}, 이름)
IP_TABLES = [
    ("DT_1F02001", {"objL1": "00", "objL2": "ALL"},
     {"T10": "prod", "T11": "ship", "T12": "inv",
      "T20": "prod_sa", "T21": "ship_sa", "T22": "inv_sa"},
     "시도/산업별 광공업생산지수"),
    ("DT_1F02016", {"objL1": "ALL"},
     {"T10": "ship_dom", "T11": "ship_exp",
      "T20": "ship_dom_sa", "T21": "ship_exp_sa"},
     "내수/수출 광공업출하지수"),
    ("DT_1F02061", {"objL1": "ALL"}, {"T02": "prod_cal"},
     "명절과조업효과조정 광공업생산지수"),
]
CLI_TABLES = [("DT_1C8015", "경기종합지수"), ("DT_1C8016", "경기종합지수 구성지표")]


def api_key() -> str:
    for line in open(ENV, encoding="utf-8"):
        if line.startswith("KOSIS_API_KEY") and not line.startswith("#"):
            v = line.split("=", 1)[1].strip()
            if v:
                return v
    sys.exit(f"KOSIS_API_KEY가 없다: {ENV} (kosis.kr 공유서비스에서 발급)")


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=180) as r:
        txt = r.read().decode("utf-8")
    try:
        js = json.loads(txt)
    except json.JSONDecodeError:
        sys.exit(f"JSON이 아니다: {txt[:300]}")
    if isinstance(js, dict):        # 오류는 {"err": "...", "errMsg": "..."} 꼴
        sys.exit(f"KOSIS 오류: {js}")
    return js


def fetch(key: str, tbl: str, objs: dict, itm: str, start: str, end: str) -> list:
    q = {"method": "getList", "apiKey": key, "itmId": itm, "format": "json",
         "jsonVD": "Y", "prdSe": "M", "startPrdDe": start, "endPrdDe": end,
         "orgId": ORG, "tblId": tbl, **objs}
    return get_json(URL_DATA + "?" + urllib.parse.urlencode(q))


def meta_items(key: str, tbl: str, typ: str = "ITM") -> pd.DataFrame:
    q = {"method": "getMeta", "apiKey": key, "orgId": ORG, "tblId": tbl,
         "type": typ, "format": "json", "jsonVD": "Y"}
    return pd.DataFrame(get_json(URL_META + "?" + urllib.parse.urlencode(q)))


def windows(first_year: int, step: int) -> list[tuple[str, str]]:
    """[first, 오늘]을 step년 창으로 자른다."""
    out, y, last = [], first_year, dt.date.today().year
    while y <= last:
        y2 = min(y + step - 1, last)
        out.append((f"{y}01", f"{y2}12"))
        y = y2 + 1
    return out


def rows_to_frame(rows: list, code_col: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["yyyymm", "code", "value"])
    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["DT"], errors="coerce")   # '-'는 결측
    df = df.dropna(subset=["value"])
    return pd.DataFrame({"yyyymm": df["PRD_DE"].astype(int),
                         "code": df[code_col].astype(str),
                         "value": df["value"].astype(float)})


def save_raw(stamp: str, name: str, rows: list) -> None:
    os.makedirs(RAW, exist_ok=True)
    with open(os.path.join(RAW, f"{stamp}_{name}.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)


def collect(key: str, stamp: str, keep_raw: bool):
    # ---- 생산지수 -----------------------------------------------------------
    ip = []
    for tbl, objs, items, label in IP_TABLES:
        # 산업 코드는 마지막 분류 축에 있다 (DT_1F02001은 시도가 C1, 산업이 C2)
        code_col = "C2" if "objL2" in objs else "C1"
        for itm, measure in items.items():
            n = 0
            for s, e in windows(FIRST_IP, 10):
                rows = fetch(key, tbl, objs, itm, s, e)
                if keep_raw:
                    save_raw(stamp, f"{tbl}_{itm}_{s}", rows)
                df = rows_to_frame(rows, code_col)
                df.insert(2, "measure", measure)
                ip.append(df)
                n += len(df)
            print(f"  {label} {itm}->{measure:12s} {n:>7,}행")
    fact_ip = pd.concat(ip, ignore_index=True).rename(columns={"code": "ksic"})
    fact_ip = fact_ip[["yyyymm", "ksic", "measure", "value"]]

    # ---- 산업 사전 (DT_1F02001의 산업 축 메타) ----------------------------------
    m = meta_items(key, "DT_1F02001")
    m = m[m["OBJ_ID"] == "B"]
    dim_ksic = pd.DataFrame({
        "ksic": m["ITM_ID"].astype(str),
        "name_ko": m["ITM_NM"].str.strip(),
        "name_en": m["ITM_NM_ENG"].str.strip(),
    })
    dim_ksic["level"] = dim_ksic["ksic"].map(
        lambda c: 0 if c == "0" else {1: 1, 3: 2, 4: 3}[len(c)])
    dim_ksic["parent"] = dim_ksic["ksic"].map(
        lambda c: None if c == "0" else
        ("0" if c in ("A", "D") else "A") if len(c) == 1 else c[:len(c) - (2 if len(c) == 3 else 1)])
    # 가중치: 올해 지수 작성에 쓰는 전국 가중치(총지수 10000). 연쇄 라스파이레스라
    # 과거 해에는 그대로 적용할 수 없지만, 소분류 둘을 하나로 묶을 때의 비율로는 쓴다.
    w = meta_items(key, "DT_1F02001", "WGT")
    w = w[(w["C1"] == "00") & (w["ITM_ID"] == "T10")]
    w = pd.DataFrame({"ksic": w["C2"].astype(str),
                      "wgt": pd.to_numeric(w["WGT_CO"], errors="coerce")})
    dim_ksic = dim_ksic.merge(w, on="ksic", how="left")
    assert abs(dim_ksic.loc[dim_ksic.ksic == "0", "wgt"].iloc[0] - 10000) < 0.5
    # 소분류·중분류가 사전에 있는데 자료에 없는 코드가 있으면 알린다 (반대는 오류)
    missing = set(fact_ip["ksic"]) - set(dim_ksic["ksic"])
    assert not missing, f"자료에는 있는데 사전에 없는 산업코드: {sorted(missing)}"

    # ---- 경기종합지수 ---------------------------------------------------------
    cli, dims = [], []
    for tbl, label in CLI_TABLES:
        n = 0
        for s, e in windows(FIRST_CLI, 20):
            rows = fetch(key, tbl, {"objL1": "ALL"}, "T1", s, e)
            if keep_raw:
                save_raw(stamp, f"{tbl}_{s}", rows)
            df = rows_to_frame(rows, "C1")
            df.insert(1, "tbl", tbl)
            cli.append(df)
            n += len(df)
        print(f"  {label:14s} {n:>7,}행")
        m = meta_items(key, tbl)
        m = m[m["OBJ_ID"] == "A"]
        dims.append(pd.DataFrame({"tbl": tbl, "cli_cd": m["ITM_ID"].astype(str),
                                  "name_ko": m["ITM_NM"].str.strip(),
                                  "name_en": m["ITM_NM_ENG"].str.strip()}))
    fact_cli = pd.concat(cli, ignore_index=True).rename(columns={"code": "cli_cd"})
    fact_cli = fact_cli[["yyyymm", "tbl", "cli_cd", "value"]]
    dim_cli = pd.concat(dims, ignore_index=True)
    return fact_ip, dim_ksic, fact_cli, dim_cli


def check(con: duckdb.DuckDBPyConnection) -> None:
    """어긋나면 멈춘다."""
    n, ymin, ymax = con.sql(
        "SELECT COUNT(*), MIN(yyyymm), MAX(yyyymm) FROM fact_ip").fetchone()
    assert n > 200_000, f"fact_ip가 {n:,}행뿐이다 - 받다 말았는지 볼 것"
    # 1. 행 유일
    for tbl, keys in (("fact_ip", "yyyymm, ksic, measure"),
                      ("fact_cli", "yyyymm, tbl, cli_cd")):
        dup = con.sql(f"SELECT COUNT(*) FROM (SELECT {keys}, COUNT(*) c FROM {tbl} "
                      f"GROUP BY ALL HAVING c > 1)").fetchone()[0]
        assert dup == 0, f"{tbl} ({keys}) 중복 {dup}건"
    # 2. 총지수 원계열이 1995.01부터 빈 달 없이 있는가
    tot = con.sql("SELECT yyyymm FROM fact_ip WHERE ksic='0' AND measure='prod' "
                  "ORDER BY 1").df()["yyyymm"]
    months = (ymax // 100 - 1995) * 12 + (ymax % 100)
    assert tot.iloc[0] == 199501 and len(tot) == months, (
        f"총지수 생산 원계열이 1995.01부터 {months}달이어야 하는데 "
        f"{tot.iloc[0]}부터 {len(tot)}달")
    # 3. 소분류가 1995.01부터 충분히 있는가 (탐침에서 76개였다)
    k = con.sql("""SELECT COUNT(*) FROM (
                     SELECT f.ksic, MIN(f.yyyymm) m0 FROM fact_ip f
                     JOIN dim_ksic d USING (ksic)
                     WHERE d.level = 3 AND f.measure = 'prod' GROUP BY 1)
                   WHERE m0 = 199501""").fetchone()[0]
    assert k >= 70, f"1995.01부터 있는 소분류가 {k}개뿐이다"
    # 4. 열한 measure가 다 있는가, 수출출하 총지수가 1995부터인가
    ms = set(r[0] for r in con.sql("SELECT DISTINCT measure FROM fact_ip").fetchall())
    want = {m for _, _, items, _ in IP_TABLES for m in items.values()}
    assert ms == want, f"measure가 다르다: 없음 {want - ms}, 남음 {ms - want}"
    x0 = con.sql("SELECT MIN(yyyymm) FROM fact_ip WHERE ksic='0' "
                 "AND measure='ship_exp'").fetchone()[0]
    assert x0 == 199501, f"수출출하 총지수가 {x0}부터다"
    # 5. 계절조정 연평균이 원지수 연평균과 가까운가 (계절조정은 연평균을 보존한다)
    r = con.sql("""SELECT yyyymm//100 y,
                     AVG(value) FILTER (WHERE measure='prod') o,
                     AVG(value) FILTER (WHERE measure='prod_sa') s
                   FROM fact_ip WHERE ksic='0' AND yyyymm//100 < ?
                   GROUP BY 1""", params=[ymax // 100]).df()
    gap = (r.s / r.o - 1).abs().max()
    assert gap < 0.02, f"총지수 계절조정 연평균이 원지수와 {gap*100:.2f}% 벌어진다"
    # 6. 경기종합지수 구성지표의 광공업생산지수와 상관 (같은 계열이 아니라 상관만)
    j = con.sql("""SELECT a.value ip, b.value cli FROM
                     (SELECT yyyymm, value FROM fact_ip
                      WHERE ksic='0' AND measure='prod_sa') a
                   JOIN (SELECT yyyymm, value FROM fact_cli
                         WHERE tbl='DT_1C8016' AND cli_cd='B0201') b USING (yyyymm)""").df()
    assert len(j) > 200, "경기종합지수 구성지표(2003~)와 겹치는 달이 적다"
    c = np.corrcoef(j.ip, j.cli)[0, 1]
    assert c > 0.99, f"계절조정 총지수와 경기종합지수 B0201의 상관이 {c:.3f}"
    print(f"검증 통과 — fact_ip {n:,}행 {ymin}~{ymax}, 소분류 {k}개가 1995.01부터, "
          f"계절조정 연평균 격차 최대 {gap*100:.2f}%, B0201 상관 {c:.4f} "
          f"(최대 {np.abs(j.ip - j.cli).max():.1f}p 차이)")


def report(con: duckdb.DuckDBPyConnection) -> None:
    print("\n[1] 산업 사전")
    print(con.sql("""SELECT level AS 층, COUNT(*) AS 코드,
                       COUNT(*) FILTER (WHERE m0 = 199501) AS "1995부터",
                       MIN(m0) AS 최초, MAX(m0) AS 가장늦은시작
                     FROM dim_ksic d JOIN (SELECT ksic, MIN(yyyymm) m0 FROM fact_ip
                                           WHERE measure='prod' GROUP BY 1) USING (ksic)
                     GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
    print("\n[2] 최근 12개월 생산지수(원지수) 전년 동월 대비, 주요 소분류")
    print(con.sql("""
        WITH a AS (SELECT ksic, yyyymm, value FROM fact_ip WHERE measure='prod')
        SELECT a.ksic, d.name_ko AS 산업,
               ROUND(AVG(a.value / b.value - 1) * 100, 1) AS "전년비%(12개월평균)"
        FROM a JOIN a b ON a.ksic = b.ksic AND a.yyyymm = b.yyyymm + 100
        JOIN dim_ksic d ON d.ksic = a.ksic
        WHERE a.yyyymm > (SELECT MAX(yyyymm) - 100 FROM fact_ip)
          AND a.ksic IN ('0','C','C192','C20','C212','C241','C261','C263','C264',
                         'C27','C282','C29','C301','C303','C311')
        GROUP BY 1,2 ORDER BY 1""").df().to_string(index=False))
    print("\n[3] 경기종합지수 표")
    print(con.sql("""SELECT tbl, COUNT(DISTINCT cli_cd) AS 지표, MIN(yyyymm) AS 시작,
                       MAX(yyyymm) AS 끝, COUNT(*) AS 행
                     FROM fact_cli GROUP BY 1 ORDER BY 1""").df().to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reload-only", action="store_true",
                    help="받지 않고 이미 적재된 표로 검증만 다시 한다")
    ap.add_argument("--no-raw", action="store_true", help="원본 JSON을 남기지 않는다")
    a = ap.parse_args()

    if a.reload_only:
        con = duckdb.connect(DB)
        try:
            check(con)
            report(con)
        finally:
            con.close()
        return

    key = api_key()
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    print("받는 중")
    fact_ip, dim_ksic, fact_cli, dim_cli = collect(key, stamp, not a.no_raw)

    con = duckdb.connect(DB)
    try:
        for name, df in (("fact_ip", fact_ip), ("dim_ksic", dim_ksic),
                         ("fact_cli", fact_cli), ("dim_cli", dim_cli)):
            con.register("_t", df)
            con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
            con.unregister("_t")
        check(con)
        report(con)
        print("\n적재")
        for name in ("fact_ip", "dim_ksic", "fact_cli", "dim_cli"):
            print(f"  {name:10s} {con.sql(f'SELECT COUNT(*) FROM {name}').fetchone()[0]:>9,}행")
    finally:
        con.close()


if __name__ == "__main__":
    main()
