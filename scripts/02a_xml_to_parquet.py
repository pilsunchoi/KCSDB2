"""
02a: raw XML → interim parquet 변환 (KCSDB2 재설계판)

설계 원칙 (docs/DB_구축_원칙.md):
- fact 테이블은 관세청 API 응답 필드만 담는다. 파생 컬럼을 만들지 않는다.
- v1 대비 제거: year, month, hs2, hs4, hs6, stat_kor, stat_kor_item
- 관세청은 결측을 0으로 채워 보낸다. NULL이 존재하지 않으므로 int 캐스팅은 손실 없음.
- fact_total.stat_cd만 파일명 유래 파생 (총계 행 응답은 statCd=- 이므로 불가피).

입력: data/raw/{YYYY}/{YYYYMM}_{CC}.xml.gz
출력: data/interim/fact_trade_{YYYY}.parquet
      data/interim/fact_total_{YYYY}.parquet
정책: 연도 단위 idempotent. 이미 있으면 skip. --force 로 재생성.

실행 예:
  python scripts\\02a_xml_to_parquet.py
  python scripts\\02a_xml_to_parquet.py --year 2024
  python scripts\\02a_xml_to_parquet.py --force
"""

from __future__ import annotations
import argparse
import gzip
import logging
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pandas as pd
from tqdm import tqdm


# ──────────────────────────────────────────────
# 경로 설정
# ──────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
INTERIM_DIR = PROJECT_ROOT / "data" / "interim"
LOG_DIR = PROJECT_ROOT / "logs"

INTERIM_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


LOG_PATH = LOG_DIR / f"load_xml_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# 파일명 패턴: {YYYYMM}_{CC}.xml.gz
FNAME_RE = re.compile(r"^(\d{6})_([A-Z]{2})\.xml\.gz$")


# ──────────────────────────────────────────────
# XML 파싱
# ──────────────────────────────────────────────

def _to_int(v) -> int:
    """수치 태그 → int. 관세청은 결측을 0으로 채워 보내므로 NULL 케이스는 없다.
    `or 0`은 태그 자체가 없는 이론적 경우를 위한 방어코드일 뿐 실제로는 도달하지 않는다
    (2026-07-03 표본 3종 전체에서 결측 수치태그 0건 확인)."""
    return int(v or 0)


def parse_one_file(xml_gz_path: Path) -> tuple[list[dict], dict | None]:
    """한 파일 파싱. (거래 행 리스트, 총계 행 dict 또는 None) 반환.

    파일명에서 yyyymm, cnty_cd 추출.
    - yyyymm: 호출 시기 (거래·총계 공통 시간키)
    - cnty_cd: 호출 파라미터. 총계 행의 stat_cd로만 사용 (총계 응답은 statCd=-).
    거래 행의 stat_cd는 응답 statCd를 그대로 쓴다 (raw 보존).
    """
    m = FNAME_RE.match(xml_gz_path.name)
    if not m:
        raise ValueError(f"잘못된 파일명: {xml_gz_path.name}")
    yyyymm_str, cnty_cd = m.group(1), m.group(2)
    yyyymm = int(yyyymm_str)

    with gzip.open(xml_gz_path, "rb") as f:
        tree = ET.parse(f)
    root = tree.getroot()

    # 응답 정상성. resultCode != "00" (예: XK 영구실패)은 거래·총계 모두 없음.
    rc = root.findtext(".//resultCode")
    if rc != "00":
        return [], None

    items = root.findall(".//item")

    # resultCode=00이나 items가 빈 경우: 그 (월,국가) 조합에 거래가 전혀 없었음.
    # fact_total에 0값 행으로 기록해 "거래 0건(정상)"과 "수집 실패(XK 등)"를 구분.
    # 수집 실패는 위 rc != "00"에서 이미 (None) 처리됨.
    if not items:
        empty_total = {
            "yyyymm": yyyymm,
            "stat_cd": cnty_cd,
            "exp_dlr": 0,
            "imp_dlr": 0,
            "exp_wgt": 0,
            "imp_wgt": 0,
            "bal_payments": 0,
        }
        return [], empty_total

    rows = []
    total_row = None

    for it in items:
        d = {child.tag: child.text for child in it}

        # 총계 행: year 필드가 "총계"
        if d.get("year") == "총계":
            total_row = {
                "yyyymm": yyyymm,
                "stat_cd": cnty_cd,  # 파일명 유래 (응답은 statCd=-)
                "exp_dlr": _to_int(d.get("expDlr")),
                "imp_dlr": _to_int(d.get("impDlr")),
                "exp_wgt": _to_int(d.get("expWgt")),
                "imp_wgt": _to_int(d.get("impWgt")),
                "bal_payments": _to_int(d.get("balPayments")),
            }
            continue

        # 거래 행 — 관세청 raw 필드만. 파생/반복문자열 없음.
        rows.append({
            "yyyymm": yyyymm,
            "stat_cd": (d.get("statCd") or cnty_cd).strip(),
            "hs10": (d.get("hsCd") or "").strip(),
            "exp_dlr": _to_int(d.get("expDlr")),
            "imp_dlr": _to_int(d.get("impDlr")),
            "exp_wgt": _to_int(d.get("expWgt")),
            "imp_wgt": _to_int(d.get("impWgt")),
            "bal_payments": _to_int(d.get("balPayments")),
        })

    return rows, total_row


# ──────────────────────────────────────────────
# 자료형 명시 (parquet 스키마 안정성)
# ──────────────────────────────────────────────

TRADE_DTYPES = {
    "yyyymm": "int32",
    "stat_cd": "string",
    "hs10": "string",
    "exp_dlr": "int64",
    "imp_dlr": "int64",
    "exp_wgt": "int64",
    "imp_wgt": "int64",
    "bal_payments": "int64",
}

TOTAL_DTYPES = {
    "yyyymm": "int32",
    "stat_cd": "string",
    "exp_dlr": "int64",
    "imp_dlr": "int64",
    "exp_wgt": "int64",
    "imp_wgt": "int64",
    "bal_payments": "int64",
}


# ──────────────────────────────────────────────
# 연도별 처리
# ──────────────────────────────────────────────

def parse_country_names(xml_gz_path: Path) -> dict[str, str]:
    """부산물: 이 파일의 stat_cd → statCdCntnKor1(관세청 국명) 매핑.
    dim_country 빌드용. fact_trade 에서 국명을 제거한 대신 여기서 수집한다."""
    m = FNAME_RE.match(xml_gz_path.name)
    if not m:
        return {}
    cnty_cd = m.group(2)
    with gzip.open(xml_gz_path, "rb") as f:
        root = ET.parse(f).getroot()
    if root.findtext(".//resultCode") != "00":
        return {}
    out = {}
    for it in root.findall(".//item"):
        d = {c.tag: c.text for c in it}
        if d.get("year") == "총계":
            continue
        cd = (d.get("statCd") or cnty_cd).strip()
        nm = (d.get("statCdCntnKor1") or "").strip()
        if cd and nm and cd not in out:
            out[cd] = nm
    return out


def process_year(year: int, force: bool = False) -> dict:
    year_dir = RAW_DIR / str(year)
    if not year_dir.exists():
        logger.warning(f"연도 디렉토리 없음: {year_dir}")
        return {"success": False, "n_files": 0, "errors": ["dir_not_found"]}

    out_trade = INTERIM_DIR / f"fact_trade_{year}.parquet"
    out_total = INTERIM_DIR / f"fact_total_{year}.parquet"

    if not force and out_trade.exists() and out_total.exists():
        logger.info(f"  [skip] {year} 이미 변환됨 ({out_trade.name})")
        return {"success": True, "n_files": 0, "skipped": True}

    files = sorted(year_dir.glob("*.xml.gz"))
    logger.info(f"  {year}: {len(files)}개 파일 처리 시작")

    all_trade_rows = []
    all_total_rows = []
    country_names: dict[str, str] = {}  # stat_cd → 관세청 국명 (부산물)
    errors = []

    for fp in tqdm(files, desc=f"  {year}", unit="file"):
        try:
            trade_rows, total_row = parse_one_file(fp)
            all_trade_rows.extend(trade_rows)
            if total_row is not None:
                all_total_rows.append(total_row)
            for cd, nm in parse_country_names(fp).items():
                country_names.setdefault(cd, nm)
        except Exception as e:
            errors.append(f"{fp.name}: {e}")
            logger.error(f"  파싱 실패: {fp.name}: {e}")

    # 부산물 저장: stat_cd → 관세청 국명 (연도별, 03이 통합)
    if country_names:
        cn_df = pd.DataFrame(
            sorted(country_names.items()), columns=["stat_cd", "name_ko_kcs"]
        )
        cn_df.to_parquet(INTERIM_DIR / f"country_names_{year}.parquet", index=False)

    df_trade = pd.DataFrame(all_trade_rows).astype(TRADE_DTYPES) if all_trade_rows else pd.DataFrame()
    df_total = pd.DataFrame(all_total_rows).astype(TOTAL_DTYPES) if all_total_rows else pd.DataFrame()

    if not df_trade.empty:
        df_trade.to_parquet(out_trade, engine="pyarrow", compression="snappy", index=False)
    if not df_total.empty:
        df_total.to_parquet(out_total, engine="pyarrow", compression="snappy", index=False)

    logger.info(
        f"  {year}: 거래 {len(df_trade):,}행, 총계 {len(df_total):,}행, "
        f"실패 {len(errors)}건 → {out_trade.name}, {out_total.name}"
    )

    return {
        "success": True,
        "year": year,
        "n_files": len(files),
        "n_trade_rows": len(df_trade),
        "n_total_rows": len(df_total),
        "n_errors": len(errors),
        "errors": errors,
    }


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=None,
                        help="특정 연도만 처리 (기본: 전체)")
    parser.add_argument("--year-from", type=int, default=2007)
    parser.add_argument("--year-to", type=int, default=2026)
    parser.add_argument("--force", action="store_true",
                        help="이미 변환된 연도도 재생성")
    args = parser.parse_args()

    years = [args.year] if args.year is not None else list(range(args.year_from, args.year_to + 1))

    logger.info("=" * 60)
    logger.info("02a: XML → parquet 변환 시작 (KCSDB2 재설계판, 파생 컬럼 제거)")
    logger.info(f"대상 연도: {years[0]} ~ {years[-1]} ({len(years)}개)")
    logger.info(f"raw 디렉토리: {RAW_DIR}")
    logger.info(f"출력 디렉토리: {INTERIM_DIR}")
    logger.info("=" * 60)

    results = []
    t_start = datetime.now()
    for y in years:
        results.append(process_year(y, force=args.force))
    t_elapsed = (datetime.now() - t_start).total_seconds()

    logger.info("=" * 60)
    logger.info(f"전체 종료. 총 {t_elapsed:.0f}초 ({t_elapsed/60:.1f}분)")
    total_trade = sum(r.get("n_trade_rows", 0) for r in results)
    total_total = sum(r.get("n_total_rows", 0) for r in results)
    total_errors = sum(r.get("n_errors", 0) for r in results)
    logger.info(f"누적 거래 행: {total_trade:,}")
    logger.info(f"누적 총계 행: {total_total:,}")
    logger.info(f"누적 파싱 실패: {total_errors}")
    logger.info(f"상세 로그: {LOG_PATH}")


if __name__ == "__main__":
    main()
