"""
03b_build_hs6_concordance.py — HS6 개정 연계표 추출 (PDF → dim_hs6_concordance)

설계 원칙 (docs/DB_구축_원칙.md §3.3):
- HS 개정 간 시계열 연결을 DB 계층에서 한 번 해결한다.
- 단위: HS6 (관세청 공식 연계표가 HS6까지 공개).
- 출처: 관세청 FTA 포털 HS 연계표 3종 PDF (HS2022 허브 스타형).
- 다대다 매핑을 그대로 보존한다. 연결 규칙은 분석 계층에서 부과한다.

입력: data/external/HS연계표_2022to{2007,2012,2017}.pdf
출력: data/processed/kcsdb.duckdb 의 dim_hs6_concordance 테이블

테이블 스키마:
  hs2022        VARCHAR   현행(HS2022) 6자리 코드. 폐지코드 케이스는 NULL.
  hs_past       VARCHAR   과거 버전 6자리 코드.
  past_version  VARCHAR   '2007' | '2012' | '2017'
  relation      VARCHAR   'mapped'  : 정상 대응 (hs2022 ↔ hs_past)
                          'identity': hs2022 == hs_past (개정에도 코드 불변)
                          'deleted' : 과거 코드가 HS2022에서 폐지됨 (PDF에 '삭제' 표기)

실측 (2026-07-03):
  HS2022→2007: 6,592행 (헤더 HS2022(6)/HS2007(6))
  HS2022→2012: 6,415행
  HS2022→2017: 5,937행 (그중 1건 '삭제' 행: 2017코드 300219가 2022에서 폐지)

실행:
  python scripts\\03b_build_hs6_concordance.py
  python scripts\\03b_build_hs6_concordance.py --dry-run   # DB 미기록, 추출·검증만
"""

from __future__ import annotations
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pdfplumber
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXTERNAL_DIR = PROJECT_ROOT / "data" / "external"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
LOG_DIR = PROJECT_ROOT / "logs"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

DB_PATH = PROCESSED_DIR / "kcsdb.duckdb"

LOG_PATH = LOG_DIR / f"build_concordance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# past_version → PDF 파일명
SOURCES = {
    "2007": "HS연계표_2022to2007.pdf",
    "2012": "HS연계표_2022to2012.pdf",
    "2017": "HS연계표_2022to2017.pdf",
}

DELETE_TOKENS = {"삭제", "폐지"}  # PDF가 폐지코드를 표기하는 문자열


def extract_one(pdf_path: Path, past_version: str) -> pd.DataFrame:
    """한 PDF에서 (hs2022, hs_past, past_version, relation) 행 추출."""
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages):
            for table in (page.extract_tables() or []):
                for row in table:
                    if not row or len(row) < 2:
                        continue
                    a = (row[0] or "").strip()
                    b = (row[1] or "").strip()
                    # 헤더 skip
                    if "HS" in a or "HS" in b:
                        continue
                    if not a and not b:
                        continue

                    a_del = a in DELETE_TOKENS
                    b_del = b in DELETE_TOKENS

                    if a_del:
                        # 과거 코드(b)가 HS2022에서 폐지됨
                        if b.isdigit() and len(b) == 6:
                            rows.append((None, b, past_version, "deleted"))
                        else:
                            logger.warning(f"  [{past_version} p{pi}] 삭제행이나 과거코드 이상: {row}")
                        continue
                    if b_del:
                        # 드문 역방향 (2022코드 신설, 과거 대응 없음)
                        if a.isdigit() and len(a) == 6:
                            rows.append((a, None, past_version, "new"))
                        else:
                            logger.warning(f"  [{past_version} p{pi}] 삭제행이나 현행코드 이상: {row}")
                        continue

                    if not (a.isdigit() and len(a) == 6 and b.isdigit() and len(b) == 6):
                        logger.warning(f"  [{past_version} p{pi}] 형식 이상행 skip: {row}")
                        continue

                    relation = "identity" if a == b else "mapped"
                    rows.append((a, b, past_version, relation))

    df = pd.DataFrame(rows, columns=["hs2022", "hs_past", "past_version", "relation"])
    logger.info(
        f"  {past_version}: {len(df):,}행 "
        f"(identity={int((df.relation=='identity').sum()):,}, "
        f"mapped={int((df.relation=='mapped').sum()):,}, "
        f"deleted={int((df.relation=='deleted').sum())}, "
        f"new={int((df.relation=='new').sum())})"
    )
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB 미기록, 추출·검증만")
    args = ap.parse_args()

    logger.info("=" * 60)
    logger.info("03b: HS6 concordance 추출 시작")
    logger.info("=" * 60)

    frames = []
    for ver, fname in SOURCES.items():
        p = EXTERNAL_DIR / fname
        if not p.exists():
            logger.error(f"연계표 없음: {p}")
            sys.exit(1)
        logger.info(f"추출: {fname}")
        frames.append(extract_one(p, ver))

    concordance = pd.concat(frames, ignore_index=True)

    # 무결성 점검
    logger.info("-" * 60)
    logger.info(f"총 {len(concordance):,}행")
    # 중복 (hs2022, hs_past, past_version) 은 데이터 오류 신호
    dup = concordance.dropna(subset=["hs2022", "hs_past"]).duplicated(
        subset=["hs2022", "hs_past", "past_version"]
    ).sum()
    logger.info(f"완전중복 행: {dup}")
    # 다대다 규모 (past_version별)
    for ver in SOURCES:
        sub = concordance[(concordance.past_version == ver) & (concordance.relation == "mapped")]
        split = sub.groupby("hs2022").size()
        merge = sub.groupby("hs_past").size()
        logger.info(
            f"  {ver}: 분할(2022 1→과거 다수)={int((split>1).sum())}, "
            f"통합(과거 다수→2022 1)={int((merge>1).sum())}"
        )

    if args.dry_run:
        logger.info("[dry-run] DB 미기록. 종료.")
        # 미리보기
        print(concordance[concordance.relation != "identity"].head(15).to_string())
        return

    # DuckDB 기록 (쓰기 연결)
    con = duckdb.connect(str(DB_PATH))
    con.execute("DROP TABLE IF EXISTS dim_hs6_concordance")
    con.execute("""
        CREATE TABLE dim_hs6_concordance (
            hs2022        VARCHAR,
            hs_past       VARCHAR,
            past_version  VARCHAR,
            relation      VARCHAR
        )
    """)
    con.register("df_conc", concordance)
    con.execute("INSERT INTO dim_hs6_concordance SELECT * FROM df_conc")
    n = con.execute("SELECT COUNT(*) FROM dim_hs6_concordance").fetchone()[0]
    con.close()
    logger.info(f"dim_hs6_concordance 기록 완료: {n:,}행 → {DB_PATH}")


if __name__ == "__main__":
    main()
