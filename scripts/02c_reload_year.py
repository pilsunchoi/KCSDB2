"""02c_reload_year.py — 특정 연도의 fact 행만 갈아 끼운다.

왜 02b를 쓰지 않는가
--------------------
`02b_parquet_to_duckdb.py`는 **DB 파일을 지우고 처음부터 만든다.** 새 달을 몇 개 더하려고
그것을 돌리면 dim 테이블 열두 개가 함께 사라져 03~03f를 전부 다시 돌려야 하고, 그중
`03c`(별표 PDF 파싱 + IPF)는 무겁다.

더 중요한 것은 `fact_exp10d`다. 이 표의 vintage 이력은 **원리상 복구할 수 없다.** API는
현재 값만 주므로 과거에 관측한 값은 다시 받을 방법이 없다. DB를 지우면 그 이력이 사라진다.

dim은 fact에 의존하지 않는다. 국가·품목명은 외부 파일에서, HS 연계표는 별표 PDF에서,
조업일수는 KASI 달력에서 온다. 거래 행이 늘어난다고 달라질 것이 없다. 그래서 해당 연도의
fact 행만 지우고 새 parquet에서 다시 넣으면 02b와 같은 결과에 닿는다.

전체 재생성이 필요한 때
-----------------------
파이프라인 자체를 검증하거나 스키마를 바꿀 때는 02b를 쓴다. 그때는 fact_exp10d를 먼저
parquet로 빼 두고 나중에 되돌려야 한다.

앞선 단계
---------
    python scripts/00_probe_update.py                    # 확정 상한 확인
    python scripts/01_fetch_raw.py --year-from 2026 --year-to 2026 \\
           --ym-from 202604 --ym-to 202607                # 미확정월은 받지 않는다
    python scripts/02a_xml_to_parquet.py --year 2026 --force

실행: python scripts/02c_reload_year.py --year 2026
      python scripts/02c_reload_year.py --year 2026 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "processed" / "kcsdb.duckdb"
INTERIM = ROOT / "data" / "interim"
PROGRESS = ROOT / "data" / "raw" / ".progress"


def snapshot(con: duckdb.DuckDBPyConnection, year: int) -> dict:
    lo, hi = year * 100 + 1, year * 100 + 12
    out = {}
    for t in ("fact_trade", "fact_total", "meta_calls"):
        n, mx = con.sql(
            f"SELECT COUNT(*), MAX(yyyymm) FROM {t} WHERE yyyymm BETWEEN {lo} AND {hi}"
        ).fetchone()
        out[t] = (n, mx)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    y, lo, hi = args.year, args.year * 100 + 1, args.year * 100 + 12

    trade_pq = INTERIM / f"fact_trade_{y}.parquet"
    total_pq = INTERIM / f"fact_total_{y}.parquet"
    for p in (trade_pq, total_pq):
        if not p.exists():
            sys.exit(f"parquet이 없다: {p}\n먼저 02a를 --force로 돌린다.")

    con = duckdb.connect(str(DB))
    try:
        before = snapshot(con, y)
        new_trade = con.sql(f"SELECT COUNT(*), MAX(yyyymm) FROM read_parquet('{trade_pq.as_posix()}')").fetchone()
        new_total = con.sql(f"SELECT COUNT(*), MAX(yyyymm) FROM read_parquet('{total_pq.as_posix()}')").fetchone()
        print(f"[{y}년]")
        print(f"  fact_trade  DB {before['fact_trade'][0]:>9,}행(~{before['fact_trade'][1]}) "
              f"-> parquet {new_trade[0]:>9,}행(~{new_trade[1]})")
        print(f"  fact_total  DB {before['fact_total'][0]:>9,}행(~{before['fact_total'][1]}) "
              f"-> parquet {new_total[0]:>9,}행(~{new_total[1]})")
        if args.dry_run:
            print("\n[DRY RUN] 바꾸지 않고 종료.")
            return

        # 품목 명세가 없는 달은 잠정이므로 fact_total에서도 뺀다(02b와 같은 규칙).
        prov = [r[0] for r in con.sql(f"""
            SELECT DISTINCT yyyymm FROM read_parquet('{total_pq.as_posix()}')
            WHERE yyyymm NOT IN (SELECT DISTINCT yyyymm FROM read_parquet('{trade_pq.as_posix()}'))
            ORDER BY 1""").fetchall()]
        if prov:
            print(f"  잠정월 제외(품목명세 없음): {prov}")

        con.execute("BEGIN")
        con.execute(f"DELETE FROM fact_trade WHERE yyyymm BETWEEN {lo} AND {hi}")
        con.execute(f"INSERT INTO fact_trade SELECT * FROM read_parquet('{trade_pq.as_posix()}')")
        con.execute(f"DELETE FROM fact_total WHERE yyyymm BETWEEN {lo} AND {hi}")
        skip = f"AND yyyymm NOT IN ({','.join(map(str, prov))})" if prov else ""
        con.execute(f"INSERT INTO fact_total SELECT * FROM read_parquet('{total_pq.as_posix()}') "
                    f"WHERE 1=1 {skip}")

        # meta_calls는 수집 이력이라 status 인덱스에서 다시 만든다.
        idx = PROGRESS / f"status_{y}.json"
        if idx.exists():
            d = json.loads(idx.read_text(encoding="utf-8"))
            rows = []
            for k, v in d.items():
                ym, cc = k.split("_", 1)
                rows.append((int(ym), cc, v.get("success"), v.get("result_code"),
                             v.get("result_msg"), v.get("item_count"),
                             v.get("response_bytes"), v.get("elapsed_sec"), v.get("timestamp")))
            cols = ["yyyymm", "stat_cd", "success", "result_code", "result_msg",
                    "item_count", "response_bytes", "elapsed_sec", "timestamp"]
            df_meta = pd.DataFrame(rows, columns=cols)
            con.register("df_meta", df_meta)
            con.execute(f"DELETE FROM meta_calls WHERE yyyymm BETWEEN {lo} AND {hi}")
            con.execute("INSERT INTO meta_calls SELECT * FROM df_meta")
            print(f"  meta_calls {len(df_meta):,}행 갱신")
        con.execute("COMMIT")

        after = snapshot(con, y)
        print("\n[적재 후]")
        for t in ("fact_trade", "fact_total", "meta_calls"):
            print(f"  {t:<11} {before[t][0]:>9,} -> {after[t][0]:>9,}행 "
                  f"(최신월 {before[t][1]} -> {after[t][1]})")
        tot = con.sql("SELECT MIN(yyyymm), MAX(yyyymm), COUNT(*) FROM fact_trade").fetchone()
        print(f"\nfact_trade 전체 {tot[0]}~{tot[1]}, {tot[2]:,}행")
    finally:
        con.close()


if __name__ == "__main__":
    main()
