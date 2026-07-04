"""
02b_parquet_to_duckdb.py — interim parquet + status JSON → DuckDB (KCSDB2 재설계판)

설계 원칙 (docs/DB_구축_원칙.md):
- fact_trade / fact_total 은 02a가 만든 raw 8컬럼 parquet를 그대로 적재.
- meta_calls 에서도 파생 컬럼(year, month) 제거. yyyymm 만 보존.
- 인덱스 강제·"압축으로 충분" 단정 없음. 자연 정렬 상태로 적재하고
  성능은 04/분석에서 실측 판단.

입력: data/interim/fact_trade_{YYYY}.parquet
      data/interim/fact_total_{YYYY}.parquet
      data/raw/.progress/status_{YYYY}.json
출력: data/processed/kcsdb.duckdb (fact_trade, fact_total, meta_calls)
      * dim_* 및 dim_hs6_concordance 는 03/03b가 같은 DB에 추가.

실행:
  python scripts\\02b_parquet_to_duckdb.py
  python scripts\\02b_parquet_to_duckdb.py --overwrite
"""

from __future__ import annotations
import argparse
import glob
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
PROGRESS_DIR = PROJECT_ROOT / "data" / "raw" / ".progress"
LOG_DIR = PROJECT_ROOT / "logs"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

DB_PATH = PROCESSED_DIR / "kcsdb.duckdb"

LOG_PATH = LOG_DIR / f"load_duckdb_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── 예상 스키마 (파생 컬럼 혼입 방지용 명시 검증) ──
EXPECTED_TRADE_COLS = {"yyyymm", "stat_cd", "hs10",
                       "exp_dlr", "imp_dlr", "exp_wgt", "imp_wgt", "bal_payments"}
EXPECTED_TOTAL_COLS = {"yyyymm", "stat_cd",
                       "exp_dlr", "imp_dlr", "exp_wgt", "imp_wgt", "bal_payments"}
FORBIDDEN_COLS = {"year", "month", "hs2", "hs4", "hs6", "stat_kor", "stat_kor_item"}


# ── meta_calls: status JSON → DataFrame (year/month 제거) ──
META_DTYPES = {
    "yyyymm": "int32",
    "stat_cd": "string",
    "success": "boolean",
    "result_code": "string",
    "result_msg": "string",
    "item_count": "int32",
    "response_bytes": "int32",
    "elapsed_sec": "float32",
    "timestamp": "datetime64[ns]",
}


def build_meta_calls_df() -> pd.DataFrame:
    records = []
    n_files = 0
    for fp in sorted(PROGRESS_DIR.glob("status_*.json")):
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        for key, v in d.items():
            try:
                yyyymm_str, cnty = key.split("_")
                yyyymm = int(yyyymm_str)
            except (ValueError, IndexError):
                logger.warning(f"  잘못된 status 키: {key} (in {fp.name})")
                continue
            records.append({
                "yyyymm": yyyymm,
                "stat_cd": cnty,
                "success": bool(v.get("success", False)),
                "result_code": v.get("result_code") or "",
                "result_msg": v.get("result_msg") or "",
                "item_count": int(v.get("item_count", 0) or 0),
                "response_bytes": int(v.get("response_bytes", 0) or 0),
                "elapsed_sec": float(v.get("elapsed_sec", 0.0) or 0.0),
                "timestamp": v.get("timestamp"),
            })
        n_files += 1

    logger.info(f"  status JSON 파일: {n_files}개, meta_calls 레코드: {len(records):,}")
    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for col, dtype in META_DTYPES.items():
        if col == "timestamp":
            continue
        df[col] = df[col].astype(dtype)
    return df


def _assert_schema(con, table: str, expected: set) -> None:
    cols = {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
    forbidden = cols & FORBIDDEN_COLS
    if forbidden:
        logger.error(f"  ✗ {table}에 금지된 파생 컬럼 존재: {forbidden}")
        sys.exit(1)
    if cols != expected:
        logger.error(f"  ✗ {table} 스키마 불일치. 실제={sorted(cols)}, 기대={sorted(expected)}")
        sys.exit(1)
    logger.info(f"  ✓ {table} 스키마 검증 통과 ({len(cols)}컬럼, 파생 컬럼 없음)")


def build_duckdb(overwrite: bool) -> None:
    if DB_PATH.exists():
        if not overwrite:
            logger.error(f"이미 존재: {DB_PATH} (덮어쓰려면 --overwrite)")
            sys.exit(1)
        logger.warning(f"  기존 DB 삭제: {DB_PATH}")
        DB_PATH.unlink()

    logger.info(f"DB 생성: {DB_PATH}")
    con = duckdb.connect(str(DB_PATH))

    # 1. fact_trade — yyyymm, stat_cd, hs10 정렬 적재
    trade_glob = str(INTERIM_DIR / "fact_trade_*.parquet").replace(chr(92), "/")
    logger.info(f"\n[1/3] fact_trade 적재 ({len(glob.glob(trade_glob))}개 parquet)")
    t0 = datetime.now()
    con.execute(f"""
        CREATE TABLE fact_trade AS
        SELECT * FROM read_parquet('{trade_glob}')
        ORDER BY yyyymm, stat_cd, hs10
    """)
    n_trade = con.execute("SELECT COUNT(*) FROM fact_trade").fetchone()[0]
    logger.info(f"  완료: {n_trade:,}행 / {(datetime.now()-t0).total_seconds():.1f}초")
    _assert_schema(con, "fact_trade", EXPECTED_TRADE_COLS)

    # 2. fact_total
    total_glob = str(INTERIM_DIR / "fact_total_*.parquet").replace(chr(92), "/")
    logger.info(f"\n[2/3] fact_total 적재 ({len(glob.glob(total_glob))}개 parquet)")
    t0 = datetime.now()
    con.execute(f"""
        CREATE TABLE fact_total AS
        SELECT * FROM read_parquet('{total_glob}')
        ORDER BY yyyymm, stat_cd
    """)
    n_total_raw = con.execute("SELECT COUNT(*) FROM fact_total").fetchone()[0]

    # 잠정월 제거: 관세청은 월통계를 2단계 공개한다(먼저 국가별 총계, 이후 HS10
    # 품목 명세). 품목 명세(fact_trade)가 없는 yyyymm은 잠정 총계뿐이므로 이 DB의
    # 단위(HS10 거래)를 충족하지 못한다. fact_trade에 거래행이 0인 월의 fact_total을
    # 제거해 확정월만 남긴다.
    prov = con.execute("""
        SELECT DISTINCT yyyymm FROM fact_total
        WHERE yyyymm NOT IN (SELECT DISTINCT yyyymm FROM fact_trade)
        ORDER BY yyyymm
    """).df()["yyyymm"].tolist()
    if prov:
        con.execute(f"""
            DELETE FROM fact_total
            WHERE yyyymm IN ({','.join(str(m) for m in prov)})
        """)
        logger.info(f"  잠정월 제거(품목명세 없음): {prov} → fact_total에서 제외")
    n_total = con.execute("SELECT COUNT(*) FROM fact_total").fetchone()[0]
    logger.info(f"  완료: {n_total:,}행 (원본 {n_total_raw:,} - 잠정 {n_total_raw-n_total}) / {(datetime.now()-t0).total_seconds():.1f}초")
    _assert_schema(con, "fact_total", EXPECTED_TOTAL_COLS)

    # 3. meta_calls
    logger.info(f"\n[3/3] meta_calls 적재 (status JSON)")
    t0 = datetime.now()
    df_meta = build_meta_calls_df()
    con.execute("CREATE TABLE meta_calls AS SELECT * FROM df_meta")
    n_meta = con.execute("SELECT COUNT(*) FROM meta_calls").fetchone()[0]
    logger.info(f"  완료: {n_meta:,}행 / {(datetime.now()-t0).total_seconds():.1f}초")

    # ── 적재 무결성 검증 ──
    logger.info("\n" + "=" * 60)
    logger.info("적재 검증")
    logger.info("=" * 60)
    logger.info(f"  fact_trade: {n_trade:>12,}행")
    logger.info(f"  fact_total: {n_total:>12,}행")
    logger.info(f"  meta_calls: {n_meta:>12,}행")

    # 검증: meta 성공 페어의 item_count 합 = fact_trade행 + 거래있는 fact_total행
    # (item_count는 총계행 포함. 총계행 1개가 fact_total 1행에 대응)
    sql = """
        WITH meta_sum AS (SELECT SUM(item_count) s FROM meta_calls WHERE success),
             tt AS (SELECT COUNT(*) c FROM fact_total
                    WHERE NOT (exp_dlr=0 AND imp_dlr=0 AND exp_wgt=0 AND imp_wgt=0)),
             tc AS (SELECT COUNT(*) c FROM fact_trade)
        SELECT meta_sum.s, tc.c, tt.c, meta_sum.s - tc.c - tt.c AS diff
        FROM meta_sum, tc, tt
    """
    s, t, tt, diff = con.execute(sql).fetchone()
    logger.info(f"\n  적재 무결성:")
    logger.info(f"    meta item_count 합    = {s:>12,}")
    logger.info(f"    fact_trade 행          = {t:>12,}")
    logger.info(f"    fact_total 거래있는 행 = {tt:>12,}")
    logger.info(f"    차이(0이면 무결)       = {diff:>12,}")
    logger.info("    ✓ 통과" if diff == 0 else "    ✗ 실패 — 조사 필요")

    # bal_payments 항등식 (전 행)
    bad_bal = con.execute("""
        SELECT COUNT(*) FROM fact_trade WHERE bal_payments <> exp_dlr - imp_dlr
    """).fetchone()[0]
    logger.info(f"\n  bal_payments = exp_dlr-imp_dlr 위반 행: {bad_bal:,} " +
                ("✓" if bad_bal == 0 else "✗"))

    # 거래 0건 페어 / 실패 페어
    n_zero = con.execute("""
        SELECT COUNT(*) FROM fact_total
        WHERE exp_dlr=0 AND imp_dlr=0 AND exp_wgt=0 AND imp_wgt=0
    """).fetchone()[0]
    n_fail = con.execute("SELECT COUNT(*) FROM meta_calls WHERE NOT success").fetchone()[0]
    logger.info(f"  거래 0건 페어(fact_total 0값): {n_zero:,}")
    logger.info(f"  수집 실패 페어(meta success=false): {n_fail:,}")

    # 범위·기수
    rng = con.execute("SELECT MIN(yyyymm), MAX(yyyymm) FROM fact_trade").fetchone()
    n_ctry = con.execute("SELECT COUNT(DISTINCT stat_cd) FROM fact_trade").fetchone()[0]
    n_hs = con.execute("SELECT COUNT(DISTINCT hs10) FROM fact_trade").fetchone()[0]
    logger.info(f"\n  fact_trade 범위: {rng[0]} ~ {rng[1]}")
    logger.info(f"  국가(stat_cd) 고유: {n_ctry}")
    logger.info(f"  hs10 고유: {n_hs:,}")

    db_mb = DB_PATH.stat().st_size / 1024**2
    logger.info(f"  DB 파일 크기: {db_mb:.1f} MB")

    con.close()
    logger.info(f"\n✓ DuckDB 빌드 완료: {DB_PATH}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true", help="기존 DB 덮어쓰기")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("02b: parquet → DuckDB 빌드 (KCSDB2 재설계판)")
    logger.info(f"interim: {INTERIM_DIR}")
    logger.info(f"DB:      {DB_PATH}")
    logger.info("=" * 60)

    t_start = datetime.now()
    build_duckdb(overwrite=args.overwrite)
    logger.info(f"\n총 시간: {(datetime.now()-t_start).total_seconds():.1f}초")
    logger.info(f"상세 로그: {LOG_PATH}")


if __name__ == "__main__":
    main()
