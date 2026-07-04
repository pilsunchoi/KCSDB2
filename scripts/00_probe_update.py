"""
00_probe_update.py — 업데이트 시작점 판정 (로컬 실행 필수)

목적:
1. 로컬 data/raw 에 실제로 어느 월까지 수집돼 있는지 실측 (메모리·문서 아닌 파일 기준)
2. 다음 후보 월들을 소수 대형 무역국으로 시험 호출해, 어디까지 관세청 확정치가
   존재하는지 판정. 관세청 확정 무역통계는 익월 중순 이후 공개되므로 당월·전월은
   빈 응답/미확정일 수 있다. 이 경계를 실측해야 미확정 데이터 오염을 막는다.

호출 비용: 후보월 × 프로브국가(기본 3개) 뿐. 한도 거의 소모 안 함.
raw 저장 안 함: 판정 전용. 실제 수집은 01_fetch_raw.py 가 한다.

실행:
  python scripts\\00_probe_update.py
  python scripts\\00_probe_update.py --probe-countries US,CN,JP --look-ahead 4
"""

from __future__ import annotations
import os
import sys
import argparse
import tempfile
import xml.etree.ElementTree as ET
import gzip
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv
from utils.api_client import call_trade_api

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
ENV_PATH = PROJECT_ROOT / "config" / "api_key.env"


def scan_local_raw() -> dict[str, int]:
    """로컬 raw 에 실제 존재하는 (yyyymm별 파일수) 집계."""
    counts: dict[str, int] = {}
    if not RAW_DIR.exists():
        return counts
    for year_dir in sorted(RAW_DIR.glob("[0-9]" * 4)):
        for f in year_dir.glob("*.xml.gz"):
            ym = f.name[:6]
            counts[ym] = counts.get(ym, 0) + 1
    return counts


def next_months(after_yyyymm: str, today: date) -> list[str]:
    """after_yyyymm 다음 달부터 이번 달까지 후보 월 목록."""
    y, mo = int(after_yyyymm[:4]), int(after_yyyymm[4:])
    out = []
    while True:
        mo += 1
        if mo > 12:
            mo = 1; y += 1
        if (y, mo) > (today.year, today.month):
            break
        out.append(f"{y:04d}{mo:02d}")
    return out


def probe_month(api_key: str, yyyymm: str, countries: list[str]) -> dict:
    """한 월을 프로브 국가들로 호출. 확정치 존재 여부 판정.
    total 행의 expDlr/impDlr 가 모두 0이거나 items 자체가 없으면 '미확정/공란'으로 본다."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        summary = {"yyyymm": yyyymm, "per_country": {}, "has_data": False}
        for cc in countries:
            res = call_trade_api(api_key, yyyymm, cc, tmp)
            total_val = 0
            if res.raw_path and res.raw_path.exists():
                with gzip.open(res.raw_path, "rb") as f:
                    root = ET.fromstring(f.read())
                for it in root.findall(".//item"):
                    d = {c.tag: c.text for c in it}
                    if d.get("year") == "총계":
                        total_val = int(d.get("expDlr") or 0) + int(d.get("impDlr") or 0)
                        break
            # 품목 명세(HS10 거래행) 수 = 전체 item - 총계행 1개.
            # 관세청은 월통계를 2단계 공개한다: 먼저 총계, 이후 품목 명세.
            # 총계만 있고 품목이 없으면(item_count<=1) 잠정월이므로 확정으로 보지 않는다.
            detail_count = max(0, (res.item_count or 0) - 1)
            summary["per_country"][cc] = {
                "result_code": res.result_code,
                "item_count": res.item_count,
                "detail_count": detail_count,
                "total_expimp": total_val,
            }
            # 확정 판정: 품목 명세가 있어야 한다 (총계 금액만으로는 잠정월과 구분 불가)
            if detail_count > 0:
                summary["has_data"] = True
        return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-countries", default="US,CN,JP",
                    help="확정치 판정에 쓸 대형 무역국 ISO2 (쉼표 구분)")
    ap.add_argument("--look-ahead", type=int, default=6,
                    help="로컬 최신월 이후 최대 몇 개월까지 프로브할지")
    args = ap.parse_args()

    countries = [c.strip().upper() for c in args.probe_countries.split(",") if c.strip()]

    # 로컬 상태 실측
    local = scan_local_raw()
    if not local:
        print("[경고] 로컬 raw 가 비어 있음. 경로 확인:", RAW_DIR)
        sys.exit(1)
    latest_local = max(local.keys())
    print("=" * 60)
    print("로컬 raw 실측")
    print(f"  최신 수집월: {latest_local} (파일 {local[latest_local]}개)")
    # 최근 6개월 파일수 (누락 점검)
    recent = sorted(local.keys())[-6:]
    print("  최근 6개월 파일수:")
    for ym in recent:
        print(f"    {ym}: {local[ym]}개")

    # 인증키
    load_dotenv(ENV_PATH)
    api_key = os.getenv("DATA_GO_KR_API_KEY")
    if not api_key or api_key.startswith("여기에"):
        print("[오류] 인증키 미설정:", ENV_PATH)
        sys.exit(1)

    # 후보월 프로브
    today = date.today()
    candidates = next_months(latest_local, today)[: args.look_ahead]
    print("=" * 60)
    print(f"확정치 프로브 (국가: {','.join(countries)})")
    if not candidates:
        print("  후보월 없음. 로컬이 이미 최신.")
        sys.exit(0)

    confirmed = []
    for ym in candidates:
        s = probe_month(api_key, ym, countries)
        tag = "확정치 있음" if s["has_data"] else "공란/미확정(품목명세 없음)"
        print(f"  {ym}: {tag}")
        for cc, info in s["per_country"].items():
            print(f"    {cc}: rc={info['result_code']} items={info['item_count']} "
                  f"품목명세={info['detail_count']} 총수출입={info['total_expimp']:,}")
        if s["has_data"]:
            confirmed.append(ym)
        else:
            # 첫 공란월에서 멈춤 (그 이후는 볼 필요 없음)
            break

    print("=" * 60)
    if confirmed:
        print(f"수집 권장 범위: {confirmed[0]} ~ {confirmed[-1]} ({len(confirmed)}개월)")
        print(f"실행: python scripts\\01_fetch_raw.py --year-from {confirmed[0][:4]} --year-to {confirmed[-1][:4]}")
        print("  (01_fetch_raw 는 idempotent: 기존 월은 자동 skip, 신규 월만 수집)")
    else:
        print("확정치가 있는 신규 월 없음. 업데이트 불필요.")


if __name__ == "__main__":
    main()
