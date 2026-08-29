"""03e_build_nqi.py — 신성질별 분류 차원과 HS10 대응표를 만든다.

왜 필요한가
-----------
HS10은 5년마다 개정돼 계열이 끊긴다. 반면 관세청 신성질별 분류는 2012년에 만들어진 뒤
코드 체계가 유지되고, 관세청이 해마다 HSK와의 대응표를 갱신해 공표한다. 즉 이 분류를
매개로 하면 **공식 자료만으로** 개정을 건너뛴 안정적 계열을 얻을 수 있다.
`dim_hs10_to_2022`가 추정인 것과 달리 이쪽은 관세청이 직접 정한 대응이다.

대신 해상도를 잃는다. 세세분류가 758개뿐이라 품목 단위 분석에는 쓸 수 없고,
집계 수준이 높아도 무방한 분석(경기 민감도, 강건성 확인)에 맞다.

자료
----
data/external/관세청_HSK별_신성질별_20260101.xlsx
  공공데이터포털 15049720. 신청 없이 받을 수 있고 연간 갱신된다.
  시트가 둘(2025년/2026년)이고 열 이름이 서로 다르다 — 2026년 시트만
  'HS10단위부호'가 '국제적 상품분류체계(HS)10단위부호'로 되어 있다.

만드는 것
---------
dim_nqi          758행. 세세분류 코드와 대·중·소·세 계층.
dim_hs10_to_nqi  hs10 -> 세세분류. weight와 method(direct/chain).

과거 코드 처리
--------------
대응표는 현행(2025·2026년) HSK만 담아서 그대로 쓰면 2007~2011년 거래액의 21%가 빈다.
그래서 대응표에 없는 코드는 `dim_hs10_to_2022`(method='chain')로 현행 코드까지 이은 뒤
그 코드의 신성질별을 물려받게 했다. 한 과거 코드의 승계자들이 서로 다른 분류로 갈리면
분류별로 가중치를 합산해 여러 행으로 남긴다. 이렇게 하면 거래액 커버리지가
2007~2011년 95.3%, 그 뒤로는 99% 이상이 된다(잇기 전에는 78.6%였다).

승계자 가운데 대응표에 없는 것이 있으면 그 몫은 버려지므로 weight의 합이 1보다 작을 수
있다. 일부러 정규화하지 않았다 — 합이 1이 아니라는 사실 자체가 그 계열의 불완전함을
알려 주기 때문이다.

실행: python scripts/03e_build_nqi.py
"""

from __future__ import annotations

import os
import sys

import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "processed", "kcsdb.duckdb")
SRC = os.path.join(ROOT, "data", "external", "관세청_HSK별_신성질별_20260101.xlsx")

LEVELS = [("대분류", "nqi1"), ("중분류", "nqi2"), ("소분류", "nqi3"),
          ("세분류", "nqi4"), ("세세분류", "nqi5")]

# 관세청이 10일 단위 잠정치로 공표하는 10대 수출품목. 신성질별이 아니라
# **현행 수출 성질별** 분류로 정의된다. 항목마다 계층이 달라 (열, 값)으로 적는다.
# 2025년 실적으로 공표치와 대조해 확정했다(반도체 1,753억·비중 24.7%, 승용차 685억,
# 5대 주력 51.6% 대 공표 51.7%).
MAJOR10 = [
    ("반도체",       "x3", "- 반도체"),
    ("승용차",       "x4", "- 승용자동차"),
    ("철강제품",     "x2", "나. 철강제품"),
    ("석유제품",     "x3", "- 석유제품"),
    ("선박",         "x4", "- 선 박"),
    ("자동차부품",   "x4", "- 자동차 부품"),
    ("무선통신기기", "x4", "(무선통신기기)"),
    ("컴퓨터주변기기", "x4", "(컴퓨터 주변기기)"),
    ("정밀기기",     "x4", "- 정밀기기"),
    ("가전제품",     "x3", "- 가전제품"),
]


def read_sheets() -> pd.DataFrame:
    """두 시트를 읽어 하나로 합친다. 최신(2026년) 시트를 우선한다."""
    xl = pd.ExcelFile(SRC)
    out = []
    for sheet in ("2026년", "2025년"):
        d = xl.parse(sheet, dtype=str)
        key = [c for c in d.columns if "HS" in c and "10단위부호" in c][0]
        R = "관세청 현행 수출 성질별 분류현행수출"
        cols = {key: "hs10", R + "1단위분류": "x1", R + "3단위분류": "x2",
                R + "소분류": "x3", R + "세분류": "x4"}
        for ko, en in LEVELS:
            cols[f"관세청 신성질별 분류{ko}코드"] = en
            cols[f"관세청 신성질별 분류{ko}명"] = en + "_nm"
        d = d.rename(columns=cols)[list(cols.values())]
        d["src_year"] = sheet[:4]
        out.append(d)
    both = pd.concat(out, ignore_index=True)
    both = both.dropna(subset=["hs10", "nqi5"])
    return both.drop_duplicates("hs10", keep="first")


def build(con: duckdb.DuckDBPyConnection, mp: pd.DataFrame) -> None:
    con.register("mp", mp)

    # 계층 차원. 이름은 코드마다 하나로 정해진다.
    con.execute("""
        CREATE OR REPLACE TABLE dim_nqi AS
        SELECT nqi5, ANY_VALUE(nqi5_nm) AS nqi5_nm,
               ANY_VALUE(nqi4) AS nqi4, ANY_VALUE(nqi4_nm) AS nqi4_nm,
               ANY_VALUE(nqi3) AS nqi3, ANY_VALUE(nqi3_nm) AS nqi3_nm,
               ANY_VALUE(nqi2) AS nqi2, ANY_VALUE(nqi2_nm) AS nqi2_nm,
               ANY_VALUE(nqi1) AS nqi1, ANY_VALUE(nqi1_nm) AS nqi1_nm
        FROM mp GROUP BY nqi5""")

    # 현행 코드는 직접, 그 밖에는 dim_hs10_to_2022로 이어 붙인다.
    con.execute("""
        CREATE OR REPLACE TABLE dim_hs10_to_nqi AS
        WITH direct AS (
            SELECT hs10, nqi5, 1.0 AS weight, 'direct' AS method FROM mp
        ),
        -- dim_hs10_to_2022는 판본(past_version)마다 행을 갖는다. 그대로 합치면
        -- 한 코드의 가중치가 판본 수만큼 부풀어 3까지 간다. 코드마다 가장 나중
        -- 판본, 곧 현행 분류에 가장 가까운 대응만 쓴다.
        era AS (
            SELECT hs_past, MAX(past_version) AS pv
            FROM dim_hs10_to_2022 WHERE method = 'chain' GROUP BY 1
        ),
        chained AS (
            SELECT c.hs_past AS hs10, m.nqi5, SUM(c.weight) AS weight, 'chain' AS method
            FROM dim_hs10_to_2022 c
            JOIN era e ON e.hs_past = c.hs_past AND e.pv = c.past_version
            JOIN mp m ON m.hs10 = c.hs2022
            WHERE c.method = 'chain'
              AND c.hs_past NOT IN (SELECT hs10 FROM mp)
            GROUP BY 1, 2
        )
        SELECT * FROM direct
        UNION ALL
        SELECT * FROM chained""")


def build_major10(con: duckdb.DuckDBPyConnection) -> None:
    """10대 수출품목 꼬리표. 과거 코드는 신성질별과 같은 방식으로 이어 붙인다."""
    when = chr(10).join(
        f"    WHEN {col} = '{val}' THEN '{name}'" for name, col, val in MAJOR10)
    con.execute(f"""
        CREATE OR REPLACE TABLE dim_hs10_to_major10 AS
        WITH lab AS (
            SELECT hs10, CASE
{when}
            END AS item FROM mp
        ),
        direct AS (SELECT hs10, item, 1.0 AS weight, 'direct' AS method
                   FROM lab WHERE item IS NOT NULL),
        era AS (SELECT hs_past, MAX(past_version) AS pv
                FROM dim_hs10_to_2022 WHERE method = 'chain' GROUP BY 1),
        chained AS (
            SELECT c.hs_past AS hs10, l.item, SUM(c.weight) AS weight, 'chain' AS method
            FROM dim_hs10_to_2022 c
            JOIN era e ON e.hs_past = c.hs_past AND e.pv = c.past_version
            JOIN lab l ON l.hs10 = c.hs2022
            WHERE c.method = 'chain' AND l.item IS NOT NULL
              AND c.hs_past NOT IN (SELECT hs10 FROM lab WHERE item IS NOT NULL)
            GROUP BY 1, 2)
        SELECT * FROM direct UNION ALL SELECT * FROM chained""")


def check_major10(con: duckdb.DuckDBPyConnection) -> None:
    """10대 품목 정의를 관세청 공표치와 대조한다.

    2025년 실적(억 달러)과 전년 대비 증가율(%)이 둘 다 맞아야 한다. 수준만 맞추면
    우연히 맞을 수 있지만 증가율까지 맞으면 정의가 같다고 볼 만하다.
    출처: 관세청 '수출입 통계로 본 2025년 대한민국'.
    """
    d = con.sql("""
        SELECT m.item, f.yyyymm // 100 AS y, SUM(f.exp_dlr * m.weight) / 1e8 AS v
        FROM fact_trade f JOIN dim_hs10_to_major10 m USING (hs10)
        WHERE f.yyyymm // 100 IN (2024, 2025) GROUP BY 1, 2""").df()
    lv = d[d.y == 2025].set_index("item").v
    gr = (lv / d[d.y == 2024].set_index("item").v - 1) * 100
    for item, want in [("반도체", 1753), ("승용차", 685)]:
        got = lv[item]
        assert abs(got - want) <= 3, f"{item} 2025년 {got:.0f}억 != 공표 {want}억"
    for item, want in [("반도체", 21.9), ("승용차", 0.3), ("선박", 24.0)]:
        got = gr[item]
        assert abs(got - want) <= 0.3, f"{item} 증가율 {got:.1f}% != 공표 {want}%"
    print("10대 품목 정의 — 공표치 대조 5건 통과 (수준 2, 증가율 3)")


def check(con: duckdb.DuckDBPyConnection) -> None:
    """한 코드의 가중치 합이 1을 넘으면 판본이 겹쳐 들어온 것이다."""
    worst = con.sql("""SELECT MAX(s) FROM (
        SELECT hs10, SUM(weight) AS s FROM dim_hs10_to_nqi GROUP BY 1)""").fetchone()[0]
    assert worst <= 1.0001, f"가중치 합이 1을 넘는다: 최대 {worst}"
    print(f"가중치 합 최대 {worst:.4f} — 정상")


def report(con: duckdb.DuckDBPyConnection) -> None:
    n_dim = con.sql("SELECT COUNT(*) FROM dim_nqi").fetchone()[0]
    rows, codes = con.sql(
        "SELECT COUNT(*), COUNT(DISTINCT hs10) FROM dim_hs10_to_nqi").fetchone()
    print(f"dim_nqi         {n_dim:>6,}행 (세세분류)")
    print(f"dim_hs10_to_nqi {rows:>6,}행 / hs10 {codes:,}종")
    print()
    print(con.sql("""
        SELECT method, COUNT(*) AS 행, COUNT(DISTINCT hs10) AS hs10종,
               ROUND(MIN(weight), 4) AS w최소, ROUND(MAX(weight), 4) AS w최대
        FROM dim_hs10_to_nqi GROUP BY 1 ORDER BY 1""").df().to_string(index=False))
    print()
    print("거래액 커버리지")
    print(con.sql("""
        WITH tr AS (SELECT hs10, SUM(exp_dlr + imp_dlr) AS v FROM fact_trade GROUP BY 1),
             w  AS (SELECT hs10, SUM(weight) AS w FROM dim_hs10_to_nqi GROUP BY 1)
        SELECT CASE WHEN yr < 2012 THEN '2007~2011' WHEN yr < 2017 THEN '2012~2016'
                    WHEN yr < 2022 THEN '2017~2021' ELSE '2022~2026' END AS 구간,
               ROUND(100.0 * SUM(v * COALESCE(w, 0)) / SUM(v), 2) AS "커버 %"
        FROM (SELECT f.hs10, f.yyyymm // 100 AS yr, SUM(f.exp_dlr + f.imp_dlr) AS v
              FROM fact_trade f GROUP BY 1, 2)
        LEFT JOIN w USING (hs10)
        GROUP BY 1 ORDER BY 1""").df().to_string(index=False))


def main() -> None:
    if not os.path.exists(SRC):
        sys.exit(f"원본이 없다: {SRC}\n공공데이터포털 15049720에서 받는다.")
    mp = read_sheets()
    print(f"대응표 {len(mp):,}종 hs10, 세세분류 {mp.nqi5.nunique()}개")
    con = duckdb.connect(DB)
    try:
        build(con, mp)
        build_major10(con)
        check(con)
        check_major10(con)
        report(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
