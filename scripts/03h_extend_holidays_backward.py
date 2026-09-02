"""03h_extend_holidays_backward.py — 공휴일 달력을 1995년까지 뒤로 늘린다.

왜 필요한가
-----------
`fact_temper`(성질별)는 1995년부터인데 `KASI_공휴일.csv`는 2007년부터다. 무역 트렌드
연구가 31년 계열의 조업일수를 쓰려면 열두 해가 비어 있다. KASI 특일정보 API는
2004년부터만 응답한다(2003년 이전은 totalCount 0). 그래서 셋으로 나눠 채운다.

  1995~2003  규칙으로 만든다(아래 RULES). 관보 대조는 하지 못했다 — 선거일·임시공휴일
             목록은 이 파일에 손으로 적었고, 그 외는 당시 「관공서의 공휴일에 관한 규정」의
             조문을 따랐다.
  2004~2006  KASI에서 받는다. 규칙 생성분과 맞대 어긋나는 날을 출력한다(규칙의 검증).
  2007~2011  기존 달력에 **선거일이 빠져 있다**(2007-12-19 대선, 2008-04-09 총선,
             2010-06-02 지방선거). KASI가 선거일을 2012년부터만 담기 때문이다. 셋을 더한다.

규칙에서 알아 둘 것
-------------------
- 신정 연휴는 1998년까지 1월 1~2일, 1999년부터 1일 하루.
- 식목일(4.5)은 2005년까지, 제헌절(7.17)은 2007년까지 공휴일. KASI로 확인했다
  (2005.04 식목일 Y, 2006.04 없음 / 2007.07 제헌절 Y, 2008.07 없음).
- 한글날은 1991~2012년 공휴일이 아니다. 국군의 날도 1991년부터 아니다.
- 설·추석은 각 사흘, 석가탄신일은 음력 4.8. 음력 변환은 korean_lunar_calendar.
- 대체공휴일은 2014년 제도라 이 구간에 없다.
- 선거일은 공직선거법에 따라 공휴일이다. 재보궐선거는 아니다.

기존 달력과의 관계
------------------
기존 행은 그대로 두고 없는 날짜만 더한다. 같은 날에 이름이 둘이면(2006-05-05 어린이날과
석가탄신일) 두 행을 다 둔다 — `03f`는 날짜 집합으로 읽으므로 상관없다.

실행:
    python scripts/03h_extend_holidays_backward.py            # 미리 보기(쓰지 않는다)
    python scripts/03h_extend_holidays_backward.py --write    # CSV에 쓴다
그다음: python scripts/03f_build_workday.py   (START가 1995-01-01이어야 한다)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib
import os
import sys

from korean_lunar_calendar import KoreanLunarCalendar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAL = os.path.join(ROOT, "data", "external", "KASI_공휴일.csv")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
g = importlib.import_module("03g_fetch_holidays")

RULE_YEARS = range(1995, 2004)
KASI_YEARS = range(2004, 2007)

# 선거일 (공휴일). 1995~2010. 2012년부터는 KASI가 담고 있다.
ELECTIONS = {
    "1995-06-27": "제1회 전국동시지방선거",
    "1996-04-11": "제15대 국회의원선거",
    "1997-12-18": "제15대 대통령선거",
    "1998-06-04": "제2회 전국동시지방선거",
    "2000-04-13": "제16대 국회의원선거",
    "2002-06-13": "제3회 전국동시지방선거",
    "2002-12-19": "제16대 대통령선거",
    "2004-04-15": "제17대 국회의원선거",
    "2006-05-31": "제4회 전국동시지방선거",
    "2007-12-19": "제17대 대통령선거",
    "2008-04-09": "제18대 국회의원선거",
    "2010-06-02": "제5회 전국동시지방선거",
}

# 임시공휴일. 1995~2006.
TEMPORARY = {
    "2002-07-01": "임시공휴일(월드컵)",
}


def lunar(y: int, m: int, d: int) -> dt.date:
    c = KoreanLunarCalendar()
    c.setLunarDate(y, m, d, False)
    return dt.date.fromisoformat(c.SolarIsoFormat())


def by_rules(y: int) -> dict[str, str]:
    out: dict[str, str] = {}

    def put(day: dt.date, name: str) -> None:
        out[day.isoformat()] = name

    put(dt.date(y, 1, 1), "신정")
    if y <= 1998:
        put(dt.date(y, 1, 2), "신정")
    put(dt.date(y, 3, 1), "삼일절")
    if y <= 2005:
        put(dt.date(y, 4, 5), "식목일")
    put(dt.date(y, 5, 5), "어린이날")
    put(dt.date(y, 6, 6), "현충일")
    if y <= 2007:
        put(dt.date(y, 7, 17), "제헌절")
    put(dt.date(y, 8, 15), "광복절")
    put(dt.date(y, 10, 3), "개천절")
    put(dt.date(y, 12, 25), "기독탄신일")
    s = lunar(y, 1, 1)
    for k in (-1, 0, 1):
        put(s + dt.timedelta(days=k), "설날")
    put(lunar(y, 4, 8), "석가탄신일")
    c = lunar(y, 8, 15)
    for k in (-1, 0, 1):
        put(c + dt.timedelta(days=k), "추석")
    # 설 전날이 전해 12월 말이면 그 해 달력에 들어간다(설이 1월 말인 해).
    # 위 put은 y년 설만 다루므로 y+1년 설 전날이 y년 12월에 걸리는 경우를 더한다.
    n = lunar(y + 1, 1, 1) - dt.timedelta(days=1)
    if n.year == y:
        put(n, "설날")
    return out


def from_kasi(key: str, y: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in range(1, 13):
        for d, name in g.fetch_month(key, y, m):
            out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = name
    return out


def load() -> list[tuple[str, str]]:
    with open(CAL, encoding="utf-8-sig", newline="") as f:
        return [(r["date"], r["name"]) for r in csv.DictReader(f)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="CSV에 쓴다 (기본은 미리 보기)")
    a = ap.parse_args()

    have = load()
    have_dates = {d for d, _ in have}
    print(f"기존 달력 {len(have)}행, {min(have_dates)} ~ {max(have_dates)}")

    new: dict[str, str] = {}
    for y in RULE_YEARS:
        new.update(by_rules(y))

    key = g.service_key()
    for y in KASI_YEARS:
        k = from_kasi(key, y)
        r = by_rules(y)
        only_k = sorted(set(k) - set(r))
        only_r = sorted(set(r) - set(k))
        print(f"  {y}: KASI {len(k)}일, 규칙 {len(r)}일"
              f" | KASI에만 {[(d, k[d]) for d in only_k]}"
              f" | 규칙에만 {[(d, r[d]) for d in only_r]}")
        new.update(k)

    for d, name in {**ELECTIONS, **TEMPORARY}.items():
        # 이미 있는 날(예: 규칙상 공휴일과 겹침)이면 이름을 덮지 않는다
        new.setdefault(d, name)

    add = {d: n for d, n in new.items() if d not in have_dates}
    print(f"\n더할 공휴일 {len(add)}일")
    for d in sorted(add):
        print(f"  + {d} {add[d]}")

    if not a.write:
        print("\n미리 보기다. 쓰려면 --write.")
        return
    rows = sorted(have + list(add.items()))
    with open(CAL, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "name"])
        w.writerows(rows)
    print(f"썼다: {CAL} ({len(rows)}행, {rows[0][0]} ~ {rows[-1][0]})")


if __name__ == "__main__":
    main()
