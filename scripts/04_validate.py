"""
04_validate.py — 통합 검증 (단일 진입점)

설계 원칙 (docs/DB_구축_원칙.md §5):
- v1 의 검증 분열(04_validate + _v6_additions + diagnose_validate*) 을 하나로 통합.
- 각 검증은 PASS / WARN / FAIL 반환. FAIL 존재 시 종료코드 1 (재현·CI 실패 감지).
- WARN 은 "설계상 예상된 불완전"(폐지코드 미매칭 등). FAIL 은 무결성 위반.

전제: 02b, 03, 03b 실행 완료된 kcsdb.duckdb.
출력: 콘솔 + logs/validate_*.log. 종료코드 0(전부 PASS/WARN) 또는 1(FAIL 존재).

실행:
  python scripts\\04_validate.py
"""

from __future__ import annotations
import logging
import sys
from datetime import datetime
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "kcsdb.duckdb"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_PATH = LOG_DIR / f"validate_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

EXPECTED_TRADE = {"yyyymm", "stat_cd", "hs10", "exp_dlr", "imp_dlr", "exp_wgt", "imp_wgt", "bal_payments"}
EXPECTED_TOTAL = {"yyyymm", "stat_cd", "exp_dlr", "imp_dlr", "exp_wgt", "imp_wgt", "bal_payments"}
FORBIDDEN = {"year", "month", "hs2", "hs4", "hs6", "stat_kor", "stat_kor_item"}

results = []  # (category, name, status, detail)


def record(cat, name, status, detail=""):
    results.append((cat, name, status, detail))
    mark = {"PASS": "✓", "WARN": "△", "FAIL": "✗", "INFO": "·"}[status]
    logger.info(f"  {mark} [{status}] {name}: {detail}")


def check_schema(con):
    logger.info("\n[1] 스키마 무결성")
    for tbl, exp in [("fact_trade", EXPECTED_TRADE), ("fact_total", EXPECTED_TOTAL)]:
        cols = {r[0] for r in con.execute(f"DESCRIBE {tbl}").fetchall()}
        forb = cols & FORBIDDEN
        if forb:
            record("schema", f"{tbl} 파생컬럼 없음", "FAIL", f"금지컬럼 혼입: {forb}")
        elif cols != exp:
            record("schema", f"{tbl} 스키마 일치", "FAIL", f"실제={sorted(cols)}")
        else:
            record("schema", f"{tbl} 스키마", "PASS", f"{len(cols)}컬럼, 파생 없음")


def check_values(con):
    logger.info("\n[2] 값 무결성")
    # bal_payments 항등식
    bad = con.execute("SELECT COUNT(*) FROM fact_trade WHERE bal_payments <> exp_dlr-imp_dlr").fetchone()[0]
    record("values", "bal_payments 항등식(fact_trade)", "PASS" if bad == 0 else "FAIL", f"위반 {bad:,}행")
    bad_t = con.execute("SELECT COUNT(*) FROM fact_total WHERE bal_payments <> exp_dlr-imp_dlr").fetchone()[0]
    record("values", "bal_payments 항등식(fact_total)", "PASS" if bad_t == 0 else "FAIL", f"위반 {bad_t:,}행")
    # 음수 중량 — 관세청 중량 사후정정(반품·수정신고 상계)로 원본에 존재.
    # 원칙(원본 보존)상 FAIL 아님. 정보성 기록으로 남기고, 금액 음수만 이상신호.
    neg_wgt = con.execute("""
        SELECT COUNT(*) FROM fact_trade WHERE exp_wgt<0 OR imp_wgt<0
    """).fetchone()[0]
    neg_dlr = con.execute("""
        SELECT COUNT(*) FROM fact_trade WHERE exp_dlr<0 OR imp_dlr<0
    """).fetchone()[0]
    # 금액 음수는 실제 이상(관세청 금액은 음수 정정을 별도 처리) → FAIL 유지
    record("values", "음수 금액 없음", "PASS" if neg_dlr == 0 else "FAIL", f"금액음수 {neg_dlr:,}행")
    # 중량 음수는 관세청 정정 원본 → INFO
    record("values", "음수 중량(관세청 정정 원본, 보존)", "INFO", f"중량음수 {neg_wgt:,}행")
    # NULL 없음 (관세청은 결측을 0으로 채움 — NULL 이 있으면 파싱 오류)
    nulls = con.execute("""
        SELECT COUNT(*) FROM fact_trade
        WHERE exp_dlr IS NULL OR imp_dlr IS NULL OR exp_wgt IS NULL OR imp_wgt IS NULL
              OR yyyymm IS NULL OR stat_cd IS NULL OR hs10 IS NULL
    """).fetchone()[0]
    record("values", "NULL 없음(raw는 0으로 채워짐)", "PASS" if nulls == 0 else "FAIL", f"NULL {nulls:,}행")


def check_loading(con):
    logger.info("\n[3] 적재 무결성")
    # 적재 정합성 — 페어 단위 방향 검증.
    # meta_calls.item_count 는 수집단계에서 총계행을 세지 않아 fact 행수의 대조
    # 기준으로 부적합하다(거래0건 페어에서 item_count=0이나 fact_total엔 총계행 존재).
    # 따라서 총합 비교 대신, "관세청이 데이터가 있다고 응답(item_count>0)한 페어가
    # 실제 적재됐는가"만 검증한다. 이것이 진짜 무결성 질문(수집→적재 누락 감지)이다.
    missing_pairs = con.execute("""
        WITH claimed AS (
            SELECT yyyymm, stat_cd, item_count
            FROM meta_calls
            WHERE success AND item_count > 0
        ),
        loaded AS (
            SELECT DISTINCT yyyymm, stat_cd FROM fact_trade
            UNION
            SELECT DISTINCT yyyymm, stat_cd FROM fact_total
        )
        SELECT COUNT(*) FROM claimed c
        WHERE NOT EXISTS (
            SELECT 1 FROM loaded l
            WHERE l.yyyymm = c.yyyymm AND l.stat_cd = c.stat_cd
        )
    """).fetchone()[0]
    record("loading", "적재 정합성(데이터 있는 페어 누락 없음)",
           "PASS" if missing_pairs == 0 else "FAIL",
           f"item_count>0이나 fact 미적재 페어: {missing_pairs:,}")

    # 시간 연속성 — 빠진 월 없어야 함
    months = con.execute("SELECT DISTINCT yyyymm FROM fact_trade ORDER BY yyyymm").df()["yyyymm"].tolist()
    if months:
        expected = set()
        y, mo = months[0] // 100, months[0] % 100
        end_y, end_mo = months[-1] // 100, months[-1] % 100
        while (y, mo) <= (end_y, end_mo):
            expected.add(y * 100 + mo)
            mo += 1
            if mo > 12:
                mo = 1; y += 1
        missing = sorted(expected - set(months))
        record("loading", "시간 연속성(빠진 월 없음)",
               "PASS" if not missing else "FAIL",
               f"{months[0]}~{months[-1]} {len(months)}개월" + (f", 누락 {missing}" if missing else ""))


def check_dim_coverage(con):
    logger.info("\n[4] dim 커버리지")
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}

    # stat_cd → dim_country
    if "dim_country" in tables:
        total, matched = con.execute("""
            WITH f AS (SELECT DISTINCT stat_cd FROM fact_trade)
            SELECT COUNT(*), COUNT(d.stat_cd)
            FROM f LEFT JOIN dim_country d ON f.stat_cd = d.stat_cd
        """).fetchone()
        # dim_country 는 fact 의 국명 부산물로 만들므로 100% 여야 함
        record("dim", "fact.stat_cd → dim_country",
               "PASS" if matched == total else "FAIL",
               f"{matched}/{total} 매칭")

    # hs10 → dim_hs10 (폐지코드 미매칭은 WARN)
    if "dim_hs10" in tables:
        total, matched = con.execute("""
            WITH f AS (SELECT DISTINCT hs10 FROM fact_trade)
            SELECT COUNT(*), COUNT(d.hs10)
            FROM f LEFT JOIN dim_hs10 d ON f.hs10 = d.hs10
        """).fetchone()
        rate = 100 * matched / total if total else 0
        record("dim", "fact.hs10 → dim_hs10", "WARN",
               f"{matched:,}/{total:,} ({rate:.1f}%), 미매칭 {total-matched:,}는 폐지코드(정직한 NULL)")


def check_concordance(con):
    logger.info("\n[5] concordance 커버리지")
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "dim_hs6_concordance" not in tables:
        record("concordance", "dim_hs6_concordance 존재", "FAIL", "테이블 없음 — 03b 미실행")
        return
    # fact 의 hs6(=SUBSTR(hs10,1,6)) 가 concordance 에 있는지
    for ver in ["2007", "2012", "2017"]:
        total, matched = con.execute(f"""
            WITH f AS (SELECT DISTINCT SUBSTR(hs10,1,6) hs6 FROM fact_trade)
            SELECT COUNT(*),
                   COUNT(DISTINCT CASE WHEN c.hs_past IS NOT NULL THEN f.hs6 END)
            FROM f LEFT JOIN dim_hs6_concordance c
              ON f.hs6 = c.hs_past AND c.past_version = '{ver}'
        """).fetchone()
        rate = 100 * matched / total if total else 0
        # 개정 스타형 사각지대로 100% 미만은 예상됨 → WARN
        record("concordance", f"fact hs6 → concordance({ver})", "WARN",
               f"{matched:,}/{total:,} ({rate:.1f}%)")


def main():
    if not DB_PATH.exists():
        logger.error(f"DB 없음: {DB_PATH}")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("04: 통합 검증")
    logger.info(f"DB: {DB_PATH}")
    logger.info("=" * 60)

    con = duckdb.connect(str(DB_PATH), read_only=True)
    check_schema(con)
    check_values(con)
    check_loading(con)
    check_dim_coverage(con)
    check_concordance(con)
    con.close()

    # 요약
    n_pass = sum(1 for *_, s, _ in [(0,0,r[2],r[3]) for r in results] if s == "PASS")
    n_pass = sum(1 for r in results if r[2] == "PASS")
    n_warn = sum(1 for r in results if r[2] == "WARN")
    n_fail = sum(1 for r in results if r[2] == "FAIL")

    logger.info("\n" + "=" * 60)
    logger.info(f"결과: PASS {n_pass}, WARN {n_warn}, FAIL {n_fail}")
    logger.info("=" * 60)

    if n_fail:
        logger.error("FAIL 항목:")
        for cat, name, st, detail in results:
            if st == "FAIL":
                logger.error(f"  ✗ [{cat}] {name}: {detail}")
        sys.exit(1)
    logger.info("무결성 위반 없음 (WARN 은 설계상 예상된 불완전).")


if __name__ == "__main__":
    main()
