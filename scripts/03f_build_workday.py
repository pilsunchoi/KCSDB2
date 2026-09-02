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

관세청과 세는 법이 다르다 (2026-08-29 확인)
--------------------------------------------
여기서는 월~금에서 공휴일을 뺀 날만 센다. **관세청 보도자료는 토요일을 0.5일로 센다.**
2026년 8월 1~20일을 관세청은 14.0일, 지난해 같은 기간을 14.5일로 잡는데, 우리 정의로는
둘 다 13일이다(평일 13 + 토요일 2 또는 3의 절반). 보도자료 두 건의 소수점까지 맞아
정의는 확실하다.

그래서 같은 자료를 두고도 일평균 증가율이 관세청 +61.5%, 우리 +56.0%로 갈린다.
**그래도 월~금 정의를 쓴다.** 두 정의로 구간 증분 탄력성을 다시 재면 총수출 R^2가
0.387(월~금) 대 0.372(관세청식)로 월~금이 앞서고, 열한 품목 중 아홉이 같은 방향이다
(승용차와 무선통신기기만 관세청식이 낫다). 토요일 수가 구간마다 0~2개로 흔들리는데
그 절반을 조업으로 세는 것이 실제 수출 흐름과 덜 맞는다는 뜻이다.

대시보드는 이 차이를 화면에 밝힌다. 관세청 발표와 조업일수가 다르게 보이는 것은
버그가 아니다.

자료
----
data/external/KASI_공휴일.csv
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
CAL = os.path.join(ROOT, "data", "external", "KASI_공휴일.csv")

SEGMENTS = [(10, 1, 10, "상순"), (20, 11, 20, "중순"), (99, 21, 31, "하순")]
START = "1995-01-01"   # 2026-09-02에 2007에서 당겼다. 달력은 03h가 뒤로 늘린다.


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
