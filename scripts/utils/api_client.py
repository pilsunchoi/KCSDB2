"""
관세청 API 호출 래퍼
====================

본격 수집을 위한 안정적인 호출 로직:
- 지수 백오프 재시도
- 일일 한도 추적
- raw 응답 압축 저장
- 표준화된 결과 반환

학생 교훈
--------
- 외부 API 호출에는 항상 timeout 지정
- 일시 오류(네트워크, 5xx)는 재시도, 영구 오류(4xx, resultCode 99)는 즉시 실패 처리
- raw 응답을 압축 저장하면 디버깅·재현 가능성 확보
"""

from __future__ import annotations

import gzip
import time
import logging
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

import requests


logger = logging.getLogger(__name__)


# 본격 수집 상수 — settings.yaml 에서 오버라이드 가능
DEFAULT_BASE_URL = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"
DEFAULT_TIMEOUT = 60          # 초
DEFAULT_RETRY_MAX = 5
DEFAULT_BACKOFF_BASE = 2      # 지수 백오프: base ** attempt
DEFAULT_DELAY_BETWEEN = 0.3   # 호출 사이 기본 대기 (서버 부하 완화)


@dataclass
class CallResult:
    """API 호출 결과를 표준화한 컨테이너."""
    success: bool
    result_code: str | None         # "00", "99", 등
    result_msg: str | None
    item_count: int                 # 총계 행 포함 (총계는 1)
    raw_path: Path | None           # 저장된 raw XML 경로
    error: str | None = None        # 실패 사유
    elapsed_sec: float = 0.0
    response_bytes: int = 0


def call_trade_api(
    api_key: str,
    yyyymm: str,
    cnty_cd: str,
    raw_dir: Path,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
    retry_max: int = DEFAULT_RETRY_MAX,
    backoff_base: int = DEFAULT_BACKOFF_BASE,
) -> CallResult:
    """
    한 (월, 국가) 조합을 호출하고 raw 응답을 압축 저장.

    Parameters
    ----------
    api_key : str
        공공데이터포털 인증키 (Decoding)
    yyyymm : str
        호출 월, 형식 'YYYYMM' (예: '202401')
    cnty_cd : str
        국가코드 ISO2 (예: 'US')
    raw_dir : Path
        raw 저장 루트 (data/raw)
    base_url : str
        API 엔드포인트
    timeout : int
        요청 타임아웃
    retry_max : int
        최대 재시도 횟수
    backoff_base : int
        지수 백오프 베이스 (초)

    Returns
    -------
    CallResult
    """
    # 저장 경로: data/raw/{YYYY}/{YYYYMM}_{CC}.xml.gz
    year = yyyymm[:4]
    sub_dir = raw_dir / year
    sub_dir.mkdir(parents=True, exist_ok=True)
    raw_path = sub_dir / f"{yyyymm}_{cnty_cd}.xml.gz"

    params = {
        "serviceKey": api_key,
        "strtYymm": yyyymm,
        "endYymm": yyyymm,
        "cntyCd": cnty_cd,
        # hsSgn 생략 → HS10 풀 해상도
    }

    last_err = None
    start_time = time.time()

    for attempt in range(1, retry_max + 1):
        try:
            response = requests.get(base_url, params=params, timeout=timeout)
        except requests.exceptions.RequestException as e:
            last_err = f"network error attempt={attempt}: {e}"
            logger.warning(last_err)
            # 지수 백오프
            sleep_sec = backoff_base ** attempt
            time.sleep(sleep_sec)
            continue

        # HTTP 5xx 는 재시도, 4xx 는 즉시 실패
        if response.status_code >= 500:
            last_err = f"HTTP {response.status_code} attempt={attempt}"
            logger.warning(last_err)
            time.sleep(backoff_base ** attempt)
            continue

        if response.status_code >= 400:
            elapsed = time.time() - start_time
            return CallResult(
                success=False,
                result_code=None,
                result_msg=None,
                item_count=0,
                raw_path=None,
                error=f"HTTP {response.status_code}",
                elapsed_sec=elapsed,
                response_bytes=len(response.content),
            )

        # 200 OK
        elapsed = time.time() - start_time

        # XML 파싱하여 resultCode 확인
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            last_err = f"XML parse error: {e}"
            logger.warning(last_err)
            # 응답이 깨진 경우도 재시도 가치 있음 (서버 일시 오류 가능성)
            time.sleep(backoff_base ** attempt)
            continue

        result_code = root.findtext(".//resultCode", default="")
        result_msg = root.findtext(".//resultMsg", default="")
        items = root.findall(".//item")

        # raw 응답 저장 (resultCode 무관)
        with gzip.open(raw_path, "wb") as f:
            f.write(response.content)

        # resultCode 99 는 영구 오류 (재시도해도 의미 없음)
        # 다만 일시적 시스템 오류일 수 있으니 첫 1회만 재시도
        if result_code != "00":
            if attempt == 1 and "시스템" in result_msg:
                logger.warning(f"resultCode {result_code}, retrying once: {result_msg}")
                time.sleep(backoff_base ** attempt)
                continue
            # 그 외 99 는 영구 오류 (잘못된 파라미터 등)
            return CallResult(
                success=False,
                result_code=result_code,
                result_msg=result_msg,
                item_count=len(items),
                raw_path=raw_path,
                error=f"resultCode {result_code}: {result_msg}",
                elapsed_sec=elapsed,
                response_bytes=len(response.content),
            )

        return CallResult(
            success=True,
            result_code=result_code,
            result_msg=result_msg,
            item_count=len(items),
            raw_path=raw_path,
            elapsed_sec=elapsed,
            response_bytes=len(response.content),
        )

    # 모든 재시도 실패
    elapsed = time.time() - start_time
    return CallResult(
        success=False,
        result_code=None,
        result_msg=None,
        item_count=0,
        raw_path=None,
        error=last_err or "max retries exceeded",
        elapsed_sec=elapsed,
        response_bytes=0,
    )
