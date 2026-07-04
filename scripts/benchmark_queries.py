"""
과제 3 최소 측정: 분석 쿼리 성능 실측

분석 계층이 반복 실행할 무거운 쿼리 4종의 실행시간·병목 측정.
인덱스 필요 여부 판정용. 측정 후 대부분 초 단위면 인덱스 불필요 확정.

측정 원칙:
- 각 쿼리 2회 실행, 2번째 시간 채택(1회차는 디스크→캐시 로드로 느림).
- concordance 조인은 집계로 감싸 결과 폭발 방지.
- 버전 매핑은 성능 측정과 무관하므로 단순 조인.

읽기 전용. 실행: python scripts\\benchmark_queries.py
"""
from pathlib import Path
import duckdb
import time

DB = Path(__file__).resolve().parent.parent / "data" / "processed" / "kcsdb.duckdb"
con = duckdb.connect(str(DB), read_only=True)

QUERIES = {
    "Q1_HS6_국가_월_집계": """
        SELECT SUBSTR(hs10,1,6) AS hs6, stat_cd, yyyymm,
               SUM(exp_dlr) AS exp, SUM(imp_dlr) AS imp
        FROM fact_trade
        GROUP BY 1,2,3
    """,
    "Q2_YoY_self_join": """
        SELECT a.stat_cd, a.hs10, a.yyyymm,
               a.exp_dlr AS cur, b.exp_dlr AS prev
        FROM fact_trade a
        JOIN fact_trade b
          ON a.stat_cd=b.stat_cd AND a.hs10=b.hs10
         AND a.yyyymm = b.yyyymm + 100
    """,
    "Q3_concordance_조인": """
        SELECT c.past_version, COUNT(*) AS n, SUM(f.exp_dlr) AS exp
        FROM fact_trade f
        JOIN dim_hs6_concordance c
          ON SUBSTR(f.hs10,1,6) = c.hs_past
        GROUP BY 1
    """,
    "Q4_dim_조인_집계": """
        SELECT d.continent_common, dh.sitc_like_name,
               SUM(f.exp_dlr) AS exp
        FROM fact_trade f
        LEFT JOIN dim_country d ON f.stat_cd = d.stat_cd
        LEFT JOIN dim_hs10 dh ON f.hs10 = dh.hs10
        WHERE f.yyyymm BETWEEN 202201 AND 202603
        GROUP BY 1,2
    """,
}

print("=" * 64)
print("쿼리 성능 측정")
print(f"DB: {DB.stat().st_size/1024**2:.0f} MB")
print("주의: .df() 시간은 결과 구체화(파이썬 변환) 포함. 순수 연산은 EXPLAIN ANALYZE 참조.")
print("=" * 64)

def pure_compute_seconds(sql: str) -> float:
    """EXPLAIN ANALYZE의 Total Time(순수 연산, 결과 전송 제외) 추출."""
    plan = con.execute("EXPLAIN ANALYZE " + sql).fetchall()
    for row in plan:
        txt = row[1] if len(row) > 1 else row[0]
        for line in str(txt).split("\n"):
            if "Total Time" in line:
                # 예: "Total Time: 0.912s"
                part = line.split("Total Time:")[-1].strip().rstrip("│").strip()
                try:
                    return float(part.rstrip("s"))
                except ValueError:
                    return -1.0
    return -1.0

for name, sql in QUERIES.items():
    # 결과 전송 포함 시간(.df) 2회
    times = []
    nrows = None
    for i in range(2):
        t0 = time.perf_counter()
        df = con.execute(sql).df()
        times.append(time.perf_counter() - t0)
        nrows = len(df)
    compute = pure_compute_seconds(sql)  # 순수 연산
    print(f"\n[{name}]")
    print(f"  전체(.df 포함) 2회차: {times[1]:.3f}s / 결과 {nrows:,}행")
    print(f"  순수 연산(EXPLAIN ANALYZE): {compute:.3f}s")
    # 판정은 순수 연산 기준. 전송 비용은 인덱스로 해결 불가하므로 별도.
    if compute < 0:
        verdict = "연산시간 추출 실패 — 수동 확인"
    elif compute < 2.0:
        verdict = "연산 빠름 — 인덱스 불필요"
    elif compute < 10.0:
        verdict = "연산 수용 가능 — 인덱스 선택적"
    else:
        verdict = "연산 느림 — 대응 검토"
    print(f"  판정: {verdict}")
    if nrows > 1_000_000 and times[1] - compute > 2.0:
        print(f"  참고: 전체시간의 {times[1]-compute:.1f}s는 결과 {nrows:,}행 파이썬 변환 비용."
              f" 인덱스로 해결 안 됨. 분석 시 DuckDB 내 집계로 결과 축소 권장.")

con.close()
