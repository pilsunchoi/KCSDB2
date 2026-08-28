"""
03d_build_hs10_name_hist.py — HS10 품명 이력 (dim_hs10_name_hist)

설계 원칙 (docs/DB_구축_원칙.md §3.5):
- dim_hs10은 건드리지 않는다. 관세청 HS부호 파일이 준 값과 그 NULL을 그대로 둔다.
- 품명은 판본에 따라 변하므로 (hs10) 하나를 키로 하는 dim_hs10에 열로 넣을 수 없다.
  15,255종 중 5,779종(37.9%)이 판본별로 다른 품명을 갖는다. 열 하나에 담으면
  여러 참값 가운데 하나를 임의로 고르고 나머지를 버리는 것이 된다.
- 따라서 (hs10, 별표연도)를 키로 하는 별도 테이블에 원본 그대로 쌓는다.

이 테이블이 푸는 문제: dim_hs10의 품명 커버리지는 72.8%다. 관세청 HS부호 파일이
2026년 유효 코드만 담아, 2007~2021년에만 존재하다 폐지된 4,194종의 품명이 NULL이다.
기획재정부 고시 별표에는 그해 유효했던 코드 전부의 품명이 있으므로 그중 3,738종
(89.1%, 폐지코드 거래액의 94.1%)을 되찾을 수 있다.

입력: data/external/HSK_별표/HSK_별표_{2011,2013,2015,2017,2021,2022}.pdf
출력: data/processed/kcsdb.duckdb 의 dim_hs10_name_hist

실측 (2026-08-28):
  72,129행 / 고유 HS10 15,255종
  판본별로 품명이 달라지는 코드 5,779종(37.9%)
  관세청 dim_hs10과 2022년 별표가 겹치는 11,267종에서 품명 98.5% 일치

실행:
  python scripts\\03d_build_hs10_name_hist.py
  python scripts\\03d_build_hs10_name_hist.py --dry-run   # DB 미기록, 추출·검증만
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BYEOLPYO_DIR = PROJECT_ROOT / "data" / "external" / "HSK_별표"
DB_PATH = PROJECT_ROOT / "data" / "processed" / "kcsdb.duckdb"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils import byeolpyo  # noqa: E402

LOG_PATH = LOG_DIR / f"build_hs10_name_hist_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# 전문(全文) 별표가 있는 해. 2012·2014년 판은 신구대비표라 전문이 아니므로 제외한다.
YEARS = ["2011", "2013", "2015", "2017", "2021", "2022"]

DDL = """
    CREATE TABLE dim_hs10_name_hist (
        hs10           VARCHAR,   -- HS10 코드
        byeolpyo_year  VARCHAR,   -- 그 품명이 실린 별표의 연도
        name_ko        VARCHAR,   -- 국문 품명 (기획재정부 고시 원문)
        name_en        VARCHAR    -- 영문 품명
    )
"""

# 쪽번호가 품명 꼬리에 붙었는지 점검하는 패턴. '폴리아미드 -6'(나일론 6)은
# 진짜 품명이므로 걸리는 것이 정상이다. 그 밖에 걸리면 파서를 의심할 것.
PAGENO_TAIL = re.compile(r"\s-\s*\d{1,3}\s*-?\s*$")


def collect() -> pd.DataFrame:
    frames = []
    for y in YEARS:
        pdf = BYEOLPYO_DIR / f"HSK_별표_{y}.pdf"
        if not pdf.exists():
            logger.error(f"별표 PDF 없음: {pdf}")
            sys.exit(1)
        df = byeolpyo.read(pdf)[["code", "leaf", "name_en"]].copy()
        df.columns = ["hs10", "name_ko", "name_en"]
        df["byeolpyo_year"] = y
        logger.info(f"  별표 {y}: HS10 {len(df):,}개")
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    return out[["hs10", "byeolpyo_year", "name_ko", "name_en"]]


def verify(df: pd.DataFrame) -> None:
    logger.info("-" * 60)
    logger.info(f"총 {len(df):,}행, 고유 HS10 {df.hs10.nunique():,}종")

    dup = df.duplicated(["hs10", "byeolpyo_year"]).sum()
    logger.info(f"(hs10, 별표연도) 중복: {dup}")

    norm = df.name_ko.map(lambda s: re.sub(r"\s+", "", str(s)))
    nuniq = df.assign(n=norm).groupby("hs10").n.nunique()
    logger.info(
        f"판본에 따라 품명이 달라지는 코드: {int((nuniq > 1).sum()):,}종 "
        f"({(nuniq > 1).mean():.1%})"
    )

    tail = df.name_ko.str.contains(PAGENO_TAIL, na=False) | df.name_en.str.contains(
        PAGENO_TAIL, na=False
    )
    bad = sorted(set(df[tail].hs10))
    logger.info(f"쪽번호 의심 행 {int(tail.sum())}건, 코드 {bad} (3908101000=폴리아미드 -6은 정상)")

    empty = int((df.name_ko.str.strip() == "").sum())
    logger.info(f"빈 국문 품명: {empty}행")

    if not DB_PATH.exists():
        return
    con = duckdb.connect(str(DB_PATH), read_only=True)
    k = con.execute("SELECT hs10, name_ko FROM dim_hs10").df()
    miss = con.execute(
        "SELECT f.hs10, SUM(f.exp_dlr + f.imp_dlr) v FROM fact_trade f "
        "LEFT JOIN dim_hs10 d ON d.hs10 = f.hs10 WHERE d.hs10 IS NULL GROUP BY 1"
    ).df()
    con.close()

    cur = df[df.byeolpyo_year == "2022"][["hs10", "name_ko"]]
    m = k.merge(cur, on="hs10", suffixes=("_kcs", "_mof"))
    agree = (
        m.name_ko_kcs.map(lambda s: re.sub(r"\s+", "", str(s)))
        == m.name_ko_mof.map(lambda s: re.sub(r"\s+", "", str(s)))
    ).mean()
    logger.info(f"출처 교차 확인 — dim_hs10 ∩ 2022년 별표 {len(m):,}종, 품명 일치 {agree:.1%}")

    have = set(df.hs10)
    hit = miss.hs10.isin(have)
    logger.info(
        f"폐지코드 {len(miss):,}종 중 {int(hit.sum()):,}종({hit.mean():.1%}) 품명 확보, "
        f"거래액 기준 {miss[hit].v.sum() / miss.v.sum():.2%}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB 미기록, 추출·검증만")
    args = ap.parse_args()

    logger.info("=" * 60)
    logger.info("03d: HS10 품명 이력 구축 시작")
    logger.info("=" * 60)

    logger.info("별표 파싱")
    df = collect()
    verify(df)

    if args.dry_run:
        logger.info("[dry-run] DB 미기록. 종료.")
        print(df[df.hs10 == "0102901000"].to_string(index=False))
        return

    con = duckdb.connect(str(DB_PATH))
    con.execute("DROP TABLE IF EXISTS dim_hs10_name_hist")
    con.execute(DDL)
    con.register("df_name", df)
    con.execute("INSERT INTO dim_hs10_name_hist SELECT * FROM df_name")
    n = con.execute("SELECT COUNT(*) FROM dim_hs10_name_hist").fetchone()[0]
    con.close()
    logger.info(f"dim_hs10_name_hist 기록 완료: {n:,}행 → {DB_PATH}")


if __name__ == "__main__":
    main()
