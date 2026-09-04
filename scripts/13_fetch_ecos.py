"""13_fetch_ecos.py — 수출입물가지수와 교역조건지수(한국은행 ECOS)를 받아 적재한다.

왜 필요한가
-----------
무역 자료는 명목 달러 금액이다. 달러 표시 수출은 세계 물가와 환율에 따라 움직이고,
그 부분은 한국 산업의 트렌드가 아니다. 수출입물가지수로 실질화해야 산업생산과 맞댈
수 있다. 성질별 자료의 단위중량당 가치(금액/중량)가 시장 가격과 얼마나 다른지도
이 지수로 잰다.

자료 (2026-09-02 탐침)
----------------------
ECOS Open API. 키는 `config/api_key.env`의 `ECOS_API_KEY`.

* 402Y014 수출물가지수(기본분류), 1971.01~. 품목 항목 239개(총지수·대분류·중분류·
  소분류·세분류 나무)이고 기준이 셋 — 달러(D)·원화(W)·계약통화(C). 응답에 가중치
  (WGT, 총지수 1000)가 붙어 온다. 반도체(30911AA)·승용차(312111AA)·자동차부품
  (31213AA)·석유제품(3041AA)·철강1차제품(3071AA)·컴퓨터및주변기기(3094AA)·통신및
  방송장비(30951AA)는 1980년대부터 있고 의약품(305411AA)은 2015.12, 반도체제조용기계
  (311241AA)는 2010.01부터다. **선박은 없다** — 수출물가지수에 선박이 들어 있지 않다.
* 401Y015 수입물가지수(기본분류), 1971.01~. 276개 항목, 기준 셋. 원유(201121AA)·
  천연가스(201122AA, 1985~)·나프타(304121AA, 1985~)·철광석(201211AA) 등.
* 403Y005 교역조건지수, 1988.01~. 순상품(A)·소득(B) 둘.

호출 한 번에 10만 행까지 온다. 표 × 기준으로 나누고(항목 자리는 `?` 와일드카드)
10만을 넘으면 이어서 받는다. 전부 열 번 안쪽, 1분이면 끝난다.

만드는 것
---------
fact_xmpi (yyyymm, imexp, basis, item_cd, value).
            imexp는 수출/수입/교역조건. basis는 D/W/C, 교역조건은 NULL.
dim_xmpi  (imexp, item_cd, name_ko, parent_cd, level, wgt, start_ym).
            wgt는 2020년 기준 가중치(총지수 1000). start_ym은 월 자료의 첫 달.

주의
----
* 교역조건지수는 수출·수입 총지수의 비가 **아니다**(어느 기준으로 나눠도 중위
  1.6p, 최대 12~19p 차이). 한국은행이 따로 편제한다. 검증은 상관으로만 한다.
* 세 기준 사이의 관계(달러 = 원화 / 환율)는 성립하지만 환율을 여기서 받지 않으므로
  검증하지 않는다.
* 과거값이 개정된다(가중치 개편, 기준연도 변경). 부분 갱신 없이 늘 전부 다시 받는다.

실행: python scripts/13_fetch_ecos.py
      python scripts/13_fetch_ecos.py --reload-only
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

import duckdb
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "processed", "kcsdb.duckdb")
RAW = os.path.join(ROOT, "data", "raw", "ecos")
ENV = os.path.join(ROOT, "config", "api_key.env")
BASE = "https://ecos.bok.or.kr/api"
PAGE = 100_000
FIRST = "197101"
STATS = [("402Y014", "수출"), ("401Y015", "수입")]
TOT = ("403Y005", "교역조건")
BASES = ("D", "W", "C")


def api_key() -> str:
    for line in open(ENV, encoding="utf-8"):
        if line.startswith("ECOS_API_KEY") and not line.startswith("#"):
            v = line.split("=", 1)[1].strip()
            if v:
                return v
    sys.exit(f"ECOS_API_KEY가 없다: {ENV} (ecos.bok.or.kr Open API에서 발급)")


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=300) as r:
        txt = r.read().decode("utf-8")
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        sys.exit(f"JSON이 아니다: {txt[:300]}")


def search(key: str, code: str, end_ym: str, *items: str) -> list:
    """StatisticSearch를 10만 행씩 이어 받는다."""
    out, start = [], 1
    tail = "/".join(items)
    while True:
        url = (f"{BASE}/StatisticSearch/{key}/json/kr/{start}/{start + PAGE - 1}/"
               f"{code}/M/{FIRST}/{end_ym}" + (f"/{tail}" if tail else ""))
        js = get_json(url)
        if "StatisticSearch" not in js:
            sys.exit(f"ECOS 오류 ({code} {tail}): {js}")
        body = js["StatisticSearch"]
        rows = body["row"]
        out += rows
        total = int(body["list_total_count"])
        if len(out) >= total or not rows:
            break
        start += PAGE
    return out


def item_list(key: str, code: str) -> pd.DataFrame:
    js = get_json(f"{BASE}/StatisticItemList/{key}/json/kr/1/1000/{code}")
    if "StatisticItemList" not in js:
        sys.exit(f"ECOS 오류 (항목 {code}): {js}")
    df = pd.DataFrame(js["StatisticItemList"]["row"])
    return df[(df["CYCLE"] == "M") & (df["GRP_CODE"] == "Group1")]


def to_frame(rows: list, imexp: str, basis: str | None) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["DATA_VALUE"], errors="coerce")
    df = df.dropna(subset=["value"])
    return pd.DataFrame({"yyyymm": df["TIME"].astype(int), "imexp": imexp,
                         "basis": basis, "item_cd": df["ITEM_CODE1"].astype(str),
                         "value": df["value"].astype(float),
                         "wgt": pd.to_numeric(df.get("WGT"), errors="coerce")})


def save_raw(stamp: str, name: str, rows: list) -> None:
    os.makedirs(RAW, exist_ok=True)
    with open(os.path.join(RAW, f"{stamp}_{name}.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)


def collect(key: str, stamp: str, keep_raw: bool):
    end_ym = dt.date.today().strftime("%Y%m")
    facts, dims = [], []
    for code, imexp in STATS:
        for basis in BASES:
            rows = search(key, code, end_ym, "?", basis)
            if keep_raw:
                save_raw(stamp, f"{code}_{basis}", rows)
            df = to_frame(rows, imexp, basis)
            facts.append(df)
            print(f"  {imexp}물가지수 {basis}기준 {len(df):>8,}행")
        it = item_list(key, code)
        d = pd.DataFrame({"imexp": imexp, "item_cd": it["ITEM_CODE"].astype(str),
                          "name_ko": it["ITEM_NAME"].str.strip(),
                          "parent_cd": it["P_ITEM_CODE"].where(it["P_ITEM_CODE"].notna(), None),
                          "start_ym": pd.to_numeric(it["START_TIME"], errors="coerce")})
        dims.append(d)
    rows = search(key, TOT[0], end_ym)
    if keep_raw:
        save_raw(stamp, TOT[0], rows)
    df = to_frame(rows, TOT[1], None)
    facts.append(df)
    print(f"  교역조건지수 {len(df):>8,}행")
    it = item_list(key, TOT[0])
    dims.append(pd.DataFrame({"imexp": TOT[1], "item_cd": it["ITEM_CODE"].astype(str),
                              "name_ko": it["ITEM_NAME"].str.strip(), "parent_cd": None,
                              "start_ym": pd.to_numeric(it["START_TIME"], errors="coerce")}))

    fact = pd.concat(facts, ignore_index=True)
    # 가중치는 항목마다 하나다 - 달러기준 응답에서 꺼내 사전에 붙인다
    w = (fact[fact.basis.isin(["D", None])].groupby(["imexp", "item_cd"])["wgt"]
         .agg(lambda s: s.dropna().iloc[-1] if s.notna().any() else np.nan).reset_index())
    dim = pd.concat(dims, ignore_index=True).merge(w, on=["imexp", "item_cd"], how="left")
    # concat을 거치면 None이 NaN으로 바뀐다. NaN은 None이 아니라서 깊이가 하나씩
    # 늘어난다 - 실제로 한 번 그렇게 되었다. 여기서 None으로 되돌린다.
    dim["parent_cd"] = dim["parent_cd"].where(dim["parent_cd"].notna(), None)
    dim["level"] = None
    for imexp in dim.imexp.unique():
        sub = dim[dim.imexp == imexp]
        parent = {k: (None if pd.isna(v) else v) for k, v in zip(sub.item_cd, sub.parent_cd)}
        def depth(c: str) -> int:
            n = 0
            while parent.get(c) is not None:
                c = parent[c]
                n += 1
                assert n < 10, f"{imexp} {c}: 상위 경로가 순환한다"
            return n
        dim.loc[sub.index, "level"] = sub.item_cd.map(depth)
    dim["level"] = dim["level"].astype(int)
    missing = set(zip(fact.imexp, fact.item_cd)) - set(zip(dim.imexp, dim.item_cd))
    assert not missing, f"자료에는 있는데 항목 목록에 없는 코드: {sorted(missing)[:10]}"
    fact = fact[["yyyymm", "imexp", "basis", "item_cd", "value"]]
    dim = dim[["imexp", "item_cd", "name_ko", "parent_cd", "level", "wgt", "start_ym"]]
    return fact, dim


def check(con: duckdb.DuckDBPyConnection) -> None:
    n, ymin, ymax = con.sql(
        "SELECT COUNT(*), MIN(yyyymm), MAX(yyyymm) FROM fact_xmpi").fetchone()
    assert n > 400_000, f"fact_xmpi가 {n:,}행뿐이다 - 받다 말았는지 볼 것"
    # 1. 행 유일 (basis가 NULL인 교역조건도 포함해 센다)
    dup = con.sql("""SELECT COUNT(*) FROM (SELECT yyyymm, imexp, COALESCE(basis,'-') b,
                     item_cd, COUNT(*) c FROM fact_xmpi GROUP BY ALL HAVING c > 1)"""
                  ).fetchone()[0]
    assert dup == 0, f"(연월, 방향, 기준, 항목) 중복이 {dup}건"
    # 2. 총지수가 세 기준 모두 1971.01부터 빈 달 없이 있는가
    for imexp in ("수출", "수입"):
        for b in BASES:
            t = con.sql("SELECT MIN(yyyymm), COUNT(*) FROM fact_xmpi WHERE imexp=? "
                        "AND basis=? AND item_cd='*AA'", params=[imexp, b]).fetchone()
            months = (ymax // 100 - 1971) * 12 + ymax % 100
            assert t == (197101, months), f"{imexp} {b} 총지수: {t}, 기대 (197101, {months})"
    # 3. 나무가 닫혀 있는가 - 상위 코드가 사전에 있는가, 총지수 가중치 1000
    orphan = con.sql("""SELECT COUNT(*) FROM dim_xmpi a LEFT JOIN dim_xmpi b
                        ON a.imexp=b.imexp AND a.parent_cd=b.item_cd
                        WHERE a.parent_cd IS NOT NULL AND b.item_cd IS NULL""").fetchone()[0]
    assert orphan == 0, f"상위 코드가 사전에 없는 항목 {orphan}개"
    for imexp in ("수출", "수입"):
        w = con.sql("SELECT wgt FROM dim_xmpi WHERE imexp=? AND item_cd='*AA'", params=[imexp]).fetchone()[0]
        assert abs(w - 1000) < 0.5, f"{imexp} 총지수 가중치가 {w}"
        lv = con.sql("SELECT level FROM dim_xmpi WHERE imexp=? AND item_cd='*AA'",
                     params=[imexp]).fetchone()[0]
        assert lv == 0, f"{imexp} 총지수의 level이 {lv} (0이어야 한다)"
    # 4. 주요 품목이 있고 반도체가 1980년대부터인가
    for imexp, cd, name in (("수출", "30911AA", "반도체"), ("수출", "312111AA", "승용차"),
                            ("수출", "3041AA", "석유제품"), ("수입", "201121AA", "원유")):
        r = con.sql("SELECT name_ko, start_ym FROM dim_xmpi WHERE imexp=? AND item_cd=?", params=[imexp, cd]).fetchone()
        assert r is not None and r[1] <= 199501, f"{imexp} {name}({cd}): {r}"
    # 5. 교역조건이 총지수 비와 같은 계열은 아니지만 상관은 높아야 한다
    j = con.sql("""SELECT x.value / m.value * 100 AS r, t.value AS tot FROM
                     (SELECT yyyymm, value FROM fact_xmpi WHERE imexp='수출' AND basis='D'
                      AND item_cd='*AA') x
                   JOIN (SELECT yyyymm, value FROM fact_xmpi WHERE imexp='수입' AND basis='D'
                         AND item_cd='*AA') m USING (yyyymm)
                   JOIN (SELECT yyyymm, value FROM fact_xmpi WHERE imexp='교역조건'
                         AND item_cd='A') t USING (yyyymm)""").df()
    c = np.corrcoef(j.r, j.tot)[0, 1]
    assert len(j) > 400 and c > 0.9, f"교역조건과 총지수 비의 상관이 {c:.3f} ({len(j)}달)"
    print(f"검증 통과 — fact_xmpi {n:,}행 {ymin}~{ymax}, 행 유일, 총지수 세 기준 완비, "
          f"나무 닫힘, 교역조건 상관 {c:.4f}")


def report(con: duckdb.DuckDBPyConnection) -> None:
    print("\n[1] 항목 수와 층")
    print(con.sql("""SELECT imexp AS 방향, level AS 층, COUNT(*) AS 항목,
                       COUNT(*) FILTER (WHERE start_ym <= 199501) AS "1995부터",
                       ROUND(SUM(wgt), 0) AS 가중치합
                     FROM dim_xmpi GROUP BY 1,2 ORDER BY 1,2""").df().to_string(index=False))
    print("\n[2] 수출물가지수 달러기준, 최근 12개월 전년비 (주요 품목)")
    print(con.sql("""
        WITH a AS (SELECT item_cd, yyyymm, value FROM fact_xmpi
                   WHERE imexp='수출' AND basis='D')
        SELECT a.item_cd, d.name_ko AS 품목, d.wgt AS 가중치,
               ROUND(AVG(a.value / b.value - 1) * 100, 1) AS "전년비%(12개월평균)"
        FROM a JOIN a b ON a.item_cd = b.item_cd AND a.yyyymm = b.yyyymm + 100
        JOIN dim_xmpi d ON d.imexp='수출' AND d.item_cd = a.item_cd
        WHERE a.yyyymm > (SELECT MAX(yyyymm) - 100 FROM fact_xmpi)
          AND a.item_cd IN ('*AA','30911AA','309112AA','312111AA','31213AA','3041AA',
                            '3071AA','3094AA','30951AA','305411AA','311241AA','310131AA')
        GROUP BY 1,2,3 ORDER BY 3 DESC""").df().to_string(index=False))


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
    fact, dim = collect(key, stamp, not a.no_raw)

    con = duckdb.connect(DB)
    try:
        for name, df in (("fact_xmpi", fact), ("dim_xmpi", dim)):
            con.register("_t", df)
            con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
            con.unregister("_t")
        check(con)
        report(con)
        print("\n적재")
        for name in ("fact_xmpi", "dim_xmpi"):
            print(f"  {name:10s} {con.sql(f'SELECT COUNT(*) FROM {name}').fetchone()[0]:>9,}행")
    finally:
        con.close()


if __name__ == "__main__":
    main()
