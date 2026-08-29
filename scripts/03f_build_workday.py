"""03f_build_workday.py — 10일 단위 조업일수 차원을 만든다.

왜 필요한가
-----------
관세청 10일 단위 잠정치는 1~10일, 1~20일, 1~말일의 누적 수출액이다. 이 계열을 그대로
견주면 안 된다. 같은 구간인데 조업일수가 크게 다르기 때문이다. 2016~2026년에서 상순
(1~10일)의 조업일수는 최소 1일, 최대 8일로 여덟 배까지 벌어진다.

가장 극단은 2017년 10월 상순이다. 추석 연휴에 임시공휴일과 한글날이 겹쳐 조업일이
10일 하루뿐이었다. 평상시가 8일이니 보정 없이 전년 대비를 재면 -80%가 나온다. 실제
수출이 줄어서가 아니라 일할 날이 없어서다.

하순은 길이 자체가 8일(2월)에서 11일(31일 달)까지 달라진다는 문제도 있다.

자료
----
data/external/KASI_공휴일_2007_2026.csv
  한국천문연구원 특일정보 API로 전수 대조한 공휴일이다. workalendar 등으로 생성한 뒤
  한글날 2007~2012년 오포함 제거, 부처님오신날 2012·2023년 정정, 대체공휴일 24일
  추가를 거쳤다. 만드는 과정은 `analysis/한국 수출입 달력효과/calendar_effects.ipynb` §0.

  **달력이 2026년 3월에서 끝난다.** 그 뒤 기간은 계산할 수 없으므로, 자동 수집을 돌리기
  전에 KASI API로 달력을 늘려야 한다(키는 `config/api_key.env`의 KASI_SERVICE_KEY).

만드는 것
---------
dim_workday10d(base_ym, cutoff, seg, days, workdays, holidays)
  cutoff 10 = 상순(1~10일), 20 = 중순(11~20일), 99 = 하순(21일~말일). seg는 그 이름이다.
  **증분 구간**이지 누적이 아니다. 누적 조업일수가 필요하면 cutoff 오름차순 누적합을 낸다.
  원자료가 누적치인 것과 헷갈리기 쉬우니 주의한다.

실행: python scripts/03f_build_workday.py
"""

from __future__ import annotations

import os
import sys

import duckdb
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "processed", "kcsdb.duckdb")
CAL = os.path.join(ROOT, "data", "external", "KASI_공휴일_2007_2026.csv")

SEGMENTS = [(10, 1, 10, "상순"), (20, 11, 20, "중순"), (99, 21, 31, "하순")]
START = "2007-01-01"


def load_holidays() -> tuple[set, pd.Timestamp]:
    h = pd.read_csv(CAL, dtype=str)
    h.columns = [c.strip().lstrip("﻿") for c in h.columns]
    d = pd.to_datetime(h["date"])
    return set(d.dt.date), d.max()


def build_frame(hol: set, last: pd.Timestamp) -> pd.DataFrame:
    # 달력이 끝나는 달은 통째로 덮지 못하므로 그 전 달까지만 만든다.
    end = (last.replace(day=1) - pd.Timedelta(days=1)).replace(day=1)
    rows = []
    for ym in pd.date_range(START, end, freq="MS"):
        days = pd.date_range(ym, ym + pd.offsets.MonthEnd(0), freq="D")
        for cut, lo, hi, nm in SEGMENTS:
            seg = [d for d in days if lo <= d.day <= hi]
            work = [d for d in seg if d.weekday() < 5 and d.date() not in hol]
            rows.append((int(ym.strftime("%Y%m")), cut, nm, len(seg), len(work),
                         len(seg) - len(work)))
    return pd.DataFrame(rows, columns=["base_ym", "cutoff", "seg", "days",
                                       "workdays", "holidays"])


def check(w: pd.DataFrame) -> None:
    """알려진 사례로 검증한다. 어긋나면 달력이 잘못됐다는 뜻이다."""
    def wd(ym, cut):
        return int(w[(w.base_ym == ym) & (w.cutoff == cut)].workdays.iloc[0])

    # 2017년 10월 상순: 추석 연휴 + 임시공휴일 + 한글날이 겹쳐 조업 1일
    assert wd(201710, 10) == 1, f"2017년 10월 상순 조업 {wd(201710, 10)}일, 1일이어야"
    # 2월 하순은 21~28(29)일이라 길이가 8~9일
    feb = w[(w.base_ym % 100 == 2) & (w.cutoff == 99)]
    assert feb.days.between(8, 9).all(), "2월 하순 길이가 8~9일이 아니다"
    assert w.workdays.between(0, 10).all(), "조업일수가 범위를 벗어난다"
    print("검증 통과 — 2017년 10월 상순 조업 1일, 2월 하순 길이 8~9일")


def main() -> None:
    if not os.path.exists(CAL):
        sys.exit(f"달력이 없다: {CAL}")
    hol, last = load_holidays()
    w = build_frame(hol, last)
    check(w)
    con = duckdb.connect(DB)
    try:
        con.register("w", w)
        con.execute("CREATE OR REPLACE TABLE dim_workday10d AS SELECT * FROM w")
        print(f"dim_workday10d {len(w):,}행 "
              f"({w.base_ym.min()} ~ {w.base_ym.max()}), 달력 종점 {last.date()}")
        print()
        print(con.sql("""
            SELECT seg AS 구간, MIN(workdays) AS 최소, MAX(workdays) AS 최대,
                   ROUND(AVG(workdays), 2) AS 평균, ROUND(STDDEV(workdays), 2) AS 표준편차
            FROM dim_workday10d WHERE base_ym >= 201601
            GROUP BY 1, cutoff ORDER BY cutoff""").df().to_string(index=False))
    finally:
        con.close()


if __name__ == "__main__":
    main()
