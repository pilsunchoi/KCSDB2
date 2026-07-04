"""
관세청 무역통계 본격 수집
========================

(연도, 월, 국가) 조합을 모두 순회하며 raw XML 을 압축 저장한다.

설계 원칙
--------
1. 재시작 가능 (idempotent)
   - 이미 받은 (월, 국가) 페어는 자동 skip
   - 중간에 중단해도 재실행하면 이어서 수집

2. 일일 한도 관리
   - 개발계정 10,000회/일
   - 한도 도달 시 graceful exit + 다음날 재실행 가능

3. 장애 복원
   - 호출 실패도 별도 로그에 기록
   - 부분 실패는 다음 실행에서 자동 재시도

4. 학생 가시성
   - tqdm 진행률 표시
   - 핵심 통계 콘솔 출력
   - 일일 호출 추적 파일

실행
----
    python scripts/01_fetch_raw.py [--year-from 2010] [--year-to 2026]
                                   [--max-calls 10000] [--dry-run]

기본값: 2010~2026 전체. 일일 한도 9500회 (안전 마진 500).
"""

from __future__ import annotations

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict

import yaml
from tqdm import tqdm
from dotenv import load_dotenv

# 자기 자신의 utils 임포트 가능하게 path 추가
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils.country_codes import get_iso2_list
from utils.api_client import call_trade_api, DEFAULT_DELAY_BETWEEN
import time


# ============================================================
# 1. 경로 / 설정
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

ENV_PATH = PROJECT_ROOT / "config" / "api_key.env"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
COUNTRY_CSV = PROJECT_ROOT / "data" / "external" / "country_codes_mofa_20251222.csv"

RAW_DIR = PROJECT_ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# 호출 추적 파일 (재시작 가능성용)
PROGRESS_DIR = PROJECT_ROOT / "data" / "raw" / ".progress"
PROGRESS_DIR.mkdir(parents=True, exist_ok=True)

# 로그 설정
LOG_PATH = LOG_DIR / f"fetch_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ============================================================
# 2. 일일 호출 카운터 (한도 관리)
# ============================================================

def daily_call_counter_path(today: date) -> Path:
    return PROGRESS_DIR / f"daily_calls_{today.isoformat()}.txt"


def get_today_call_count(today: date) -> int:
    p = daily_call_counter_path(today)
    if not p.exists():
        return 0
    return int(p.read_text().strip() or "0")


def increment_today_call_count(today: date, n: int = 1) -> int:
    p = daily_call_counter_path(today)
    current = get_today_call_count(today)
    new = current + n
    p.write_text(str(new))
    return new


# ============================================================
# 3. 진행 상황 추적 (idempotency)
# ============================================================

def is_pair_done(yyyymm: str, cnty_cd: str) -> bool:
    """이미 raw 응답이 저장되어 있고 이전에 success 였는지 확인."""
    year = yyyymm[:4]
    raw_path = RAW_DIR / year / f"{yyyymm}_{cnty_cd}.xml.gz"
    if not raw_path.exists():
        return False

    # 같은 페어의 status 가 success 였는지 별도 인덱스 확인
    status_idx = PROGRESS_DIR / f"status_{year}.json"
    if not status_idx.exists():
        return True  # raw 만 있으면 일단 done 으로 간주

    try:
        idx = json.loads(status_idx.read_text(encoding="utf-8"))
        key = f"{yyyymm}_{cnty_cd}"
        record = idx.get(key, {})
        # success 였거나, resultCode 99 (영구 오류) 였으면 다시 호출 안 함
        if record.get("success") is True:
            return True
        if record.get("result_code") == "99":
            # 영구 실패도 skip (재호출해도 같은 결과)
            return True
        return False
    except Exception:
        return True


def update_status_index(yyyymm: str, cnty_cd: str, result) -> None:
    """호출 결과를 status_{YYYY}.json 에 누적 기록."""
    year = yyyymm[:4]
    status_idx = PROGRESS_DIR / f"status_{year}.json"

    if status_idx.exists():
        try:
            idx = json.loads(status_idx.read_text(encoding="utf-8"))
        except Exception:
            idx = {}
    else:
        idx = {}

    key = f"{yyyymm}_{cnty_cd}"
    idx[key] = {
        "success": result.success,
        "result_code": result.result_code,
        "result_msg": result.result_msg,
        "item_count": result.item_count,
        "response_bytes": result.response_bytes,
        "elapsed_sec": round(result.elapsed_sec, 2),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    status_idx.write_text(
        json.dumps(idx, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# 4. 호출 페어 생성기
# ============================================================

def generate_pairs(
    year_from: int,
    year_to: int,
    iso2_list: list[str],
) -> list[tuple[str, str]]:
    """(YYYYMM, ISO2) 페어 전체 생성."""
    pairs = []
    today = date.today()
    for year in range(year_from, year_to + 1):
        for month in range(1, 13):
            # 미래 월은 건너뜀
            if (year, month) > (today.year, today.month):
                continue
            yyyymm = f"{year:04d}{month:02d}"
            for cc in iso2_list:
                pairs.append((yyyymm, cc))
    return pairs


# ============================================================
# 5. 메인
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="관세청 무역통계 본격 수집")
    parser.add_argument("--year-from", type=int, default=2010)
    parser.add_argument("--year-to", type=int, default=2026)
    parser.add_argument(
        "--max-calls", type=int, default=9500,
        help="이 실행에서 허용할 최대 호출 수 (일일 한도 보호)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="호출 안 하고 페어 생성·skip 판정만 수행",
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY_BETWEEN,
        help="호출 사이 대기 (초)",
    )
    args = parser.parse_args()

    # 인증키 로드
    if not ENV_PATH.exists():
        logger.error(f"{ENV_PATH} 없음")
        sys.exit(1)
    load_dotenv(ENV_PATH)
    api_key = os.getenv("DATA_GO_KR_API_KEY")
    if not api_key or api_key.startswith("여기에"):
        logger.error("인증키 미설정")
        sys.exit(1)
    logger.info(f"인증키 로드: {api_key[:4]}...{api_key[-4:]}")

    # 국가 리스트
    if not COUNTRY_CSV.exists():
        logger.error(f"국가코드 파일 없음: {COUNTRY_CSV}")
        sys.exit(1)
    iso2_list = get_iso2_list(COUNTRY_CSV)
    logger.info(f"국가 코드 {len(iso2_list)}개 로드")

    # 페어 생성
    pairs = generate_pairs(args.year_from, args.year_to, iso2_list)
    logger.info(f"전체 페어: {len(pairs):,} (= {args.year_from}~{args.year_to})")

    # 진행 상황 분류: skip vs todo
    todo_pairs = [p for p in pairs if not is_pair_done(*p)]
    skipped = len(pairs) - len(todo_pairs)
    logger.info(f"이미 완료(skip): {skipped:,}")
    logger.info(f"수집 대상: {len(todo_pairs):,}")

    # 일일 한도
    today = date.today()
    today_count = get_today_call_count(today)
    remaining = max(0, args.max_calls - today_count)
    logger.info(f"오늘 이미 호출: {today_count}, 이번 실행 최대: {remaining}")

    if remaining == 0:
        logger.warning("일일 한도 도달. 내일 다시 실행하세요.")
        return

    if args.dry_run:
        logger.info("[DRY RUN] 실제 호출 없이 종료.")
        return

    # 호출 루프
    n_calls = 0
    n_success = 0
    n_fail = 0
    fail_examples = []

    progress = tqdm(todo_pairs[:remaining], desc="Fetching", unit="call")
    for yyyymm, cc in progress:
        # 각 호출마다 한도 재확인 (다른 프로세스가 동시 호출했을 수도)
        if get_today_call_count(today) >= args.max_calls:
            logger.warning("실행 중 일일 한도 도달. 종료.")
            break

        result = call_trade_api(
            api_key=api_key,
            yyyymm=yyyymm,
            cnty_cd=cc,
            raw_dir=RAW_DIR,
        )

        n_calls += 1
        increment_today_call_count(today, 1)
        update_status_index(yyyymm, cc, result)

        if result.success:
            n_success += 1
            progress.set_postfix(
                ok=n_success, fail=n_fail, items=result.item_count, refresh=False
            )
        else:
            n_fail += 1
            if len(fail_examples) < 10:
                fail_examples.append(f"{yyyymm}/{cc}: {result.error}")
            progress.set_postfix(ok=n_success, fail=n_fail, refresh=False)

        # 호출 간 대기 (서버 부하 완화)
        time.sleep(args.delay)

    # 요약
    logger.info("=" * 60)
    logger.info(f"수집 종료: 호출 {n_calls}, 성공 {n_success}, 실패 {n_fail}")
    logger.info(f"오늘 누적 호출: {get_today_call_count(today)}")
    if fail_examples:
        logger.info("실패 샘플 (최대 10개):")
        for ex in fail_examples:
            logger.info(f"  {ex}")
    logger.info(f"raw 저장 위치: {RAW_DIR}")
    logger.info(f"상세 로그: {LOG_PATH}")


if __name__ == "__main__":
    main()
