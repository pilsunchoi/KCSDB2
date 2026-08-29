"""03g_fetch_holidays.py — 공휴일 달력을 KASI 특일정보 API에서 받아 늘린다.

왜 필요한가
-----------
`dim_workday10d`(10일 단위 조업일수)의 원천이 공휴일 달력인데, 달력이 끝나는 지점부터는
조업일수를 계산할 수 없다. 실제로 달력이 2026년 3월에서 끝나 최근 다섯 달을 보정하지
못하는 상태였다. 10일 단위 자료는 조업일수 보정 없이 견줄 수 없으므로(상순 조업일수가
1~8일로 여덟 배까지 벌어진다) 이 달력이 자동 수집의 선행 조건이다.

지금까지는 `analysis/한국 수출입 달력효과/calendar_effects.ipynb` §0이 이 일을 했는데,
`analysis/`는 gitignore라 파이프라인이 저장소에 없는 파일에 기대고 있었다. 스크립트로
꺼내 `data/external/`에 두었다.

자료
----
한국천문연구원 특일정보 API (공공데이터포털 15012690, getRestDeInfo)
  월 단위로 호출한다. 인증키는 `config/api_key.env`의 KASI_SERVICE_KEY(gitignore).
  Encoding 키를 넣어도 자동으로 풀어 쓴다.

**공휴일만 담는다.** `isHoliday='Y'`인 것만 받는다. 특일정보에는 24절기나 잡절 같은
쉬지 않는 날도 들어 있는데 그것을 조업일수에서 빼면 안 된다.

기존 달력과의 관계
------------------
지금 있는 CSV는 workalendar 등으로 만든 뒤 이 API로 전수 대조해 고친 것이다(한글날
2007~2012년 오포함 제거, 부처님오신날 2012·2023년 정정, 대체공휴일 24일 추가).
그 결과를 덮어쓰지 않도록 **기존 날짜는 그대로 두고 없는 기간만 덧붙인다.**
전체를 다시 받으려면 --refetch를 준다.

실행:
    python scripts/03g_fetch_holidays.py                 # 없는 기간만 채운다
    python scripts/03g_fetch_holidays.py --to 2028       # 2028년까지
    python scripts/03g_fetch_holidays.py --refetch       # 전 기간 재수집(대조용)
그다음: python scripts/03f_build_workday.py
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAL = ROOT / "data" / "external" / "KASI_공휴일.csv"
ENV = ROOT / "config" / "api_key.env"
URL = ("https://apis.data.go.kr/B090041/openapi/service/"
       "SpcdeInfoService/getRestDeInfo")


def service_key() -> str:
    if not ENV.exists():
        sys.exit(f"인증키 파일이 없다: {ENV}")
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("KASI_SERVICE_KEY="):
            k = line.split("=", 1)[1].strip()
            return urllib.parse.unquote(k) if "%" in k else k
    sys.exit("KASI_SERVICE_KEY 행이 없다")


def fetch_month(key: str, y: int, m: int, retries: int = 3) -> list[tuple[str, str]]:
    """그 달의 공휴일을 (YYYYMMDD, 이름)으로 준다. 쉬지 않는 날은 뺀다."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    qs = urllib.parse.urlencode({
        "serviceKey": key, "solYear": str(y), "solMonth": f"{m:02d}",
        "numOfRows": "50", "pageNo": "1"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(f"{URL}?{qs}", timeout=30, context=ctx) as r:
                root = ET.fromstring(r.read().decode("utf-8"))
            auth = root.findtext(".//returnAuthMsg")
            if auth:
                sys.exit(f"인증 오류: {auth}")
            out = []
            for it in root.iter("item"):
                if (it.findtext("isHoliday") or "").strip() == "Y":
                    out.append(((it.findtext("locdate") or "").strip(),
                                (it.findtext("dateName") or "").strip()))
            return out
        except SystemExit:
            raise
        except Exception:
            if attempt == retries - 1:
                print(f"  실패 {y}-{m:02d}")
                return []
            time.sleep(2 * (attempt + 1))
    return []


def load_existing() -> dict[str, str]:
    """키를 YYYYMMDD로 맞춘다. CSV는 2007-01-01, API는 20070101이라 그대로 두면
    같은 날이 서로 다른 키가 되어 전부 새 날짜로 들어온다."""
    if not CAL.exists():
        return {}
    out = {}
    with CAL.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            out[row["date"].replace("-", "")] = row["name"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", type=int, default=dt.date.today().year + 1,
                    help="이 연도 12월까지 채운다 (기본: 내년)")
    ap.add_argument("--refetch", action="store_true", help="전 기간 재수집")
    args = ap.parse_args()

    have = {} if args.refetch else load_existing()
    if have:
        last = max(have)
        print(f"기존 달력 {len(have)}건, {min(have)} ~ {last}")
        start_y, start_m = int(last[:4]), int(last[4:6])
    else:
        print("기존 달력 없음 — 2007년부터 받는다")
        start_y, start_m = 2007, 1

    key = service_key()
    added, months = {}, 0
    y, m = start_y, start_m
    while (y, m) <= (args.to, 12):
        for d, name in fetch_month(key, y, m):
            if d not in have:
                added[d] = name
        months += 1
        m += 1
        if m > 12:
            y, m = y + 1, 1
    print(f"{months}개월 조회, 새 공휴일 {len(added)}건")
    for d in sorted(added):
        print(f"  + {d[:4]}-{d[4:6]}-{d[6:]} {added[d]}")

    merged = {**have, **added}
    with CAL.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "name"])
        for d in sorted(merged):
            w.writerow([f"{d[:4]}-{d[4:6]}-{d[6:]}", merged[d]])
    lo, hi = min(merged), max(merged)
    print(f"\n{CAL.name}: {len(merged)}건, "
          f"{lo[:4]}-{lo[4:6]}-{lo[6:]} ~ {hi[:4]}-{hi[4:6]}-{hi[6:]}")
    print("다음: python scripts/03f_build_workday.py")


if __name__ == "__main__":
    main()
