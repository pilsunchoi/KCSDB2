"""
03_build_dims.py — dim_country, dim_hs10 생성

설계 원칙 (docs/DB_구축_원칙.md §3.1, §3.2):
- dim_country: 관세청 stat_cd 를 키로, 외교부 표준명을 참조로 병기. fact 는 raw 유지.
  * name_ko_kcs (관세청 원본) 는 02a 부산물(country_names_*.parquet)에서.
  * 외교부 참조(ISO2/3, 영문명, 한글명, 대륙 3종)는 CSV 조인.
  * 매칭 실패 시 참조 컬럼 NULL ("없으면 없는 대로"). 오타 교정 없음.
- dim_hs10: 관세청 HS부호 파일. 핵심 컬럼만. 폐지코드는 NULL(정직한 NULL).

전제: 02b 가 먼저 실행되어 kcsdb.duckdb 에 fact_trade 존재.
입력: data/interim/country_names_*.parquet  (02a 부산물)
      data/external/외교부_국가표준코드_20251222.csv
      data/external/관세청_HS부호_20260101.xlsx
출력: kcsdb.duckdb 의 dim_country, dim_hs10

실행:
  python scripts\\03_build_dims.py
"""

from __future__ import annotations
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
EXTERNAL_DIR = PROJECT_ROOT / "data" / "external"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

DB_PATH = PROCESSED_DIR / "kcsdb.duckdb"
MOFA_CSV = EXTERNAL_DIR / "외교부_국가표준코드_20251222.csv"
HS_XLSX = EXTERNAL_DIR / "관세청_HS부호_20260101.xlsx"

LOG_PATH = LOG_DIR / f"build_dims_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── dim_country ──

def build_dim_country(con) -> None:
    logger.info("[dim_country] 빌드 시작")

    # 1. 관세청 국명 부산물 통합 (02a country_names_*.parquet)
    cn_files = sorted(INTERIM_DIR.glob("country_names_*.parquet"))
    if not cn_files:
        logger.error("  country_names_*.parquet 없음. 02a 를 --force 로 재실행 필요.")
        sys.exit(1)
    cn = pd.concat([pd.read_parquet(f) for f in cn_files], ignore_index=True)
    # 여러 연도에서 같은 stat_cd 는 최초 1개만 (관세청 국명이 시기별로 미세 변동 가능)
    cn = cn.drop_duplicates(subset=["stat_cd"], keep="first")
    logger.info(f"  관세청 국명: {len(cn)}개 stat_cd ({len(cn_files)}개 연도 파일 통합)")

    # 2. 외교부 CSV — 나미비아 ISO2='NA' 결측 방지 (keep_default_na=False)
    #    'NA' 는 유효 국가코드인데 pandas 기본 na_values 가 NULL 로 오변환.
    mofa = pd.read_csv(
        MOFA_CSV, encoding="utf-8", dtype=str,
        keep_default_na=False, na_values=[""],
    )
    mofa = mofa.rename(columns={
        "국제표준화기구_2자리": "iso2",
        "국제표준화기구_3자리": "iso3",
        "국제표준화기구_숫자": "iso_num",
        "대륙명_공통 대륙코드": "continent_common",
        "대륙명_행정표준코드": "continent_admin",
        "대륙명_외교부 직제": "continent_mofa",
        "영문명": "name_en",
        "한글명": "name_ko_mofa",
    })
    # NA 결측 확인 (나미비아)
    na_missing = mofa["iso2"].isna().sum()
    if na_missing:
        logger.warning(f"  외교부 iso2 결측 {na_missing}건 — keep_default_na 처리 실패 조사 필요")
    else:
        logger.info(f"  외교부 CSV: {len(mofa)}행, iso2 결측 0 (나미비아 NA 정상 보존)")

    # 3. 조인: 관세청 stat_cd(키) ← 외교부 iso2(참조). 매칭 실패 시 참조 NULL.
    con.register("cn_df", cn)
    con.register("mofa_df", mofa)
    con.execute("DROP TABLE IF EXISTS dim_country")
    con.execute("""
        CREATE TABLE dim_country AS
        SELECT
            cn.stat_cd,
            cn.name_ko_kcs,
            m.name_ko_mofa,
            m.name_en,
            m.iso2, m.iso3, m.iso_num,
            m.continent_common,
            m.continent_admin,
            m.continent_mofa
        FROM cn_df cn
        LEFT JOIN mofa_df m ON cn.stat_cd = m.iso2
        ORDER BY cn.stat_cd
    """)

    n = con.execute("SELECT COUNT(*) FROM dim_country").fetchone()[0]
    n_match = con.execute("SELECT COUNT(*) FROM dim_country WHERE iso2 IS NOT NULL").fetchone()[0]
    logger.info(f"  dim_country: {n}행, 외교부 매칭 {n_match}, 미매칭 {n-n_match}")
    if n - n_match:
        unmatched = con.execute(
            "SELECT stat_cd, name_ko_kcs FROM dim_country WHERE iso2 IS NULL ORDER BY stat_cd"
        ).df()
        logger.info(f"  미매칭 stat_cd (참조 NULL, 관세청 고유코드):")
        for _, r in unmatched.iterrows():
            logger.info(f"    {r['stat_cd']}: {r['name_ko_kcs']}")


# ── dim_hs10 ──

HS_COL_MAP = {
    "HS부호": "hs10",
    "적용시작일자": "valid_from",
    "한글품목명": "name_ko",
    "영문품목명": "name_en",
    "수량단위코드": "unit_qty",
    "중량단위코드": "unit_wgt",
    "성질통합분류코드": "sitc_like_code",
    "성질통합분류코드명": "sitc_like_name",
}


def build_dim_hs10(con) -> None:
    logger.info("[dim_hs10] 빌드 시작")

    raw = pd.read_excel(HS_XLSX, sheet_name="Sheet 1", dtype=str)
    hs = raw[list(HS_COL_MAP)].rename(columns=HS_COL_MAP)
    # HS10(10자리)만
    before = len(hs)
    hs = hs[hs["hs10"].str.len() == 10].copy()
    logger.info(f"  HS부호 파일 {before}행 → HS10 {len(hs)}행 (7~9자리 상위분류 제외)")

    # valid_from: 'YYYY-MM-DD 00:00:00' → DATE
    hs["valid_from"] = pd.to_datetime(hs["valid_from"], errors="coerce").dt.date

    con.register("hs_df", hs)
    con.execute("DROP TABLE IF EXISTS dim_hs10")
    con.execute("CREATE TABLE dim_hs10 AS SELECT * FROM hs_df ORDER BY hs10")

    n = con.execute("SELECT COUNT(*) FROM dim_hs10").fetchone()[0]
    logger.info(f"  dim_hs10: {n}행")

    # fact_trade 대비 커버리지 (폐지코드로 인한 미매칭 규모)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "fact_trade" in tables:
        cov = con.execute("""
            WITH fact_hs AS (SELECT DISTINCT hs10 FROM fact_trade)
            SELECT
                COUNT(*) AS fact_hs_total,
                COUNT(d.hs10) AS matched
            FROM fact_hs f
            LEFT JOIN dim_hs10 d ON f.hs10 = d.hs10
        """).fetchone()
        total, matched = cov
        logger.info(f"  fact_trade hs10 커버리지: {matched:,}/{total:,} "
                    f"({100*matched/total:.1f}%), 미매칭 {total-matched:,} "
                    f"(2026 파일에 없는 폐지코드 — 정직한 NULL)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-country", action="store_true")
    ap.add_argument("--skip-hs10", action="store_true")
    args = ap.parse_args()

    if not DB_PATH.exists():
        logger.error(f"DB 없음: {DB_PATH}. 02b 를 먼저 실행.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("03: dim_country, dim_hs10 빌드")
    logger.info("=" * 60)

    con = duckdb.connect(str(DB_PATH))
    if not args.skip_country:
        build_dim_country(con)
    if not args.skip_hs10:
        build_dim_hs10(con)
    con.close()
    logger.info(f"\n✓ 완료. 로그: {LOG_PATH}")


if __name__ == "__main__":
    main()
