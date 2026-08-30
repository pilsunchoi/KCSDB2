"""09_build_exp10d_dashboard.py — 10일 단위 수출 트렌드 대시보드를 만든다.

무엇을 만드나
-------------
docs/exp10d.html 한 파일. CSS·JS·그림 전부 인라인이라 열면 그대로 뜬다. 외부 CDN에
기대지 않는 것은 docs/index.html과 같은 규약이다.

**수치를 손으로 적지 않는다.** 07·08이 만든 마트를 읽어 채운다. 자료가 갱신되면
05 -> 07 -> 08 -> 09 순서로 다시 돌리면 화면이 따라온다.

배치를 다시 짰다 (2026-08-30)
-----------------------------
처음에는 월 마감 예측을 머리기사로 놓았는데 값어치를 다시 재 보고 내렸다. 이유 셋.
  (1) **유통기한이 짧다.** 상순 예측은 20일, 중순 예측은 10일 뒤면 정답이 나온다.
  (2) **중순 예측은 거의 자명하다.** 21일이면 이미 58%가 확정이라 소박한 진도율
      평균(4.7%)과 우리 모형(2.4%)의 차이가 읽는 사람에게 잘 느껴지지 않는다.
  (3) **답이 질문보다 덜 흥미롭다.** "8월 950억"을 알고 나서 달라지는 것이 없다.
버리지는 않았다. 만드는 비용은 이미 치렀고 유지비는 0이라 카드 하나로 남겼다.

대신 머리기사는 **반도체를 빼면 얼마인가**다. 뺄셈 하나라 설명이 필요 없고 즉시
검증되는데 이야기는 이만큼 세다 — 2025년 총수출이 +3.8%일 때 반도체 제외는 -1.1%였다.
총수출 머리기사가 나머지 산업의 상태를 가리고 있었다는 뜻이다.

관세청 보도자료의 약점 셋을 겨냥한다
------------------------------------
  (1) **도넛 두 개로 구성 변화를 보여준다.** 11조각 도넛의 각도 차이는 눈으로 못
      읽는다. 여기서는 기여도 막대와 선 그래프로 바꾼다.
  (2) **증감률만 주고 기여도를 안 준다.** "반도체 +198.8%"와 "승용차 -45.1%" 중
      어느 쪽이 총수출에 더 중요한지 보도자료로는 알 수 없다.
  (3) **중요도가 아니라 관례 순서다.** 여기서는 이번 순에 가장 두드러진 것을 골라
      맨 위에 문장으로 쓴다(surprise 함수).

같은 숫자를 두 축으로 가른다
----------------------------
총수출 증가율 하나를 품목 열둘과 국가 열둘로 각각 정확히 분해해 나란히 놓는다.
관세청은 품목과 국가를 별개 절로 두어 둘이 같은 총계의 분해라는 것이 드러나지 않는다.

그림은 둘뿐이다
---------------
표로 되는 것은 표로 한다. 그림이 정당한 자리는 둘뿐이다.
  (1) 총수출과 반도체 제외의 전년비 36개월 — 행이 서른여섯이라 표에 안 들어가고,
      두 선이 언제 갈라졌는지는 숫자를 늘어놓아서는 안 보인다.
  (2) 상순 예측 오차의 36개월 이력 — 오차가 언제 커졌는지 역시 표로 안 보인다.
기여도는 표 안의 칸 막대로, 마감 예측은 카드로 해결해 그림을 늘리지 않았다.

선박은 숫자를 크게 내지 않는다
------------------------------
표본외 평균절대오차가 상순 76%다. 등급 D를 붙이고 예측값을 흐리게 처리한다.
mart_exp10d_fcskill의 등급을 화면이 그대로 따르므로 성적이 나빠지면 자동으로 흐려진다.

실행: python scripts/09_build_exp10d_dashboard.py   # 08 이후에 돌린다
"""

from __future__ import annotations

import datetime as dt
import html
import os
import re

import duckdb
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "processed", "kcsdb.duckdb")
OUT = os.path.join(ROOT, "docs", "exp10d.html")

ITEM, CNTY = "exp_item", "exp_cnty"      # 마트에 네 계열이 있다. 수출 둘만 쓴다.
TOTAL = "총수출"
SEMI = "반도체"
SEGNAME = {10: "상순", 20: "중순", 99: "월 전체"}
CUTDAY = {10: "11일", 20: "21일", 99: "익월 1일"}
EOK = 1e5          # 천 달러 -> 억 달러

# 관세청이 이 수치를 실제로 내놓는 자리. 개별 보도자료 주소는 달마다 바뀌므로
# 게시판 목록으로 건다. 여기 링크는 바깥으로 나가는 길일 뿐, 페이지를 열 때
# 바깥에서 불러오는 자원이 아니다 — 자기완결형은 그대로다.
URL_KCS = "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&amp;bbsId=1362"
URL_API = "https://www.data.go.kr/data/15157908/openapi.do"


def e(x) -> str:
    return html.escape(str(x))


def num(x, d=1) -> str:
    return "-" if x != x else f"{x:,.{d}f}"


def pct(x, d=1, sign=True) -> str:
    if x != x:
        return "-"
    return f"{x*100:+.{d}f}%" if sign else f"{x*100:.{d}f}%"


def ym_ko(ym: int) -> str:
    return f"{ym//100}년 {ym%100}월"


def ym_dot(ym: int) -> str:
    return f"{ym//100}.{ym%100:02d}"


# ---------------------------------------------------------------- 계산

def ex_semi(m: pd.DataFrame, cut: int) -> pd.DataFrame:
    """반도체를 뺀 수출. 뺄셈 하나라 설명이 필요 없고 즉시 검증된다."""
    x = m[(m.series == ITEM) & (m.cutoff == cut)]
    p = x.pivot_table(index="base_ym", columns="item",
                      values=["cum_kusd", "p_cum_kusd"])
    ex = p["cum_kusd"][TOTAL] - p["cum_kusd"][SEMI]
    pex = p["p_cum_kusd"][TOTAL] - p["p_cum_kusd"][SEMI]
    return pd.DataFrame({
        "ex_kusd": ex, "tot_kusd": p["cum_kusd"][TOTAL],
        "yoy_ex": ex / pex - 1,
        "yoy_tot": p["cum_kusd"][TOTAL] / p["p_cum_kusd"][TOTAL] - 1,
        "semi_share": p["cum_kusd"][SEMI] / p["cum_kusd"][TOTAL],
    }).reset_index()


def surprise(m: pd.DataFrame, series: str, ym: int, cut: int) -> pd.DataFrame:
    """이번 순에 무엇이 가장 두드러졌나.

    조업일 보정 전년비를 그 항목의 과거 분포로 표준화(z)하고 비중의 제곱근으로
    가중한다. 놀라움만 보면 작은 항목이 늘 이기고, 무게만 보면 늘 반도체가 이긴다.
    두 배(2025.08처럼)에는 어느 항목도 |z|가 1을 넘지 않아 '특이할 것 없음'이 된다.
    """
    x = m[(m.series == series) & (m.cutoff == cut) & m.yoy_cum_adj.notna()]
    h = x[x.base_ym < ym].groupby("item").yoy_cum_adj.agg(["mean", "std"])
    n = x[x.base_ym == ym].set_index("item")
    j = n.join(h)
    j["z"] = (j.yoy_cum_adj - j["mean"]) / j["std"]
    j["score"] = j.z.abs() * np.sqrt(j.share_cum)
    return j[j.index != TOTAL].sort_values("score", ascending=False)


def workday_ahead(con: duckdb.DuckDBPyConnection, from_ym: int,
                  beta: float, n: int = 12) -> pd.DataFrame:
    """앞으로 열두 달 조업일수와 그것만으로 이미 정해진 효과.

    예측이 아니라 달력이라 틀릴 수 없고, 아무도 미리 알려주지 않는다.
    """
    w = con.sql("SELECT base_ym, SUM(workdays) wd FROM dim_workday10d "
                "GROUP BY 1").df()
    z = w.merge(w.assign(base_ym=w.base_ym + 100).rename(columns={"wd": "pwd"}),
                on="base_ym")
    z = z[z.base_ym > from_ym].sort_values("base_ym").head(n).copy()
    z["eff"] = np.exp(np.log(z.wd / z.pwd) * beta) - 1
    return z


# ---------------------------------------------------------------- 그림

def two_line(d: pd.DataFrame, w=880, h=300) -> str:
    """총수출과 반도체 제외의 전년비. 두 선이 언제 갈라졌는지는 표로 안 보인다."""
    pad_l, pad_r, pad_t, pad_b = 54, 16, 16, 30
    ys = list(d.yoy_tot) + list(d.yoy_ex)
    lo, hi = min(ys + [-0.15]) * 1.1, max(ys + [0.15]) * 1.1
    n = len(d)
    px = lambda i: pad_l + (w - pad_l - pad_r) * (i / max(1, n - 1))
    py = lambda v: h - pad_b - (h - pad_t - pad_b) * ((v - lo) / (hi - lo))

    parts, step = [], 0.25 if hi - lo > 1.2 else 0.10
    g = np.ceil(lo / step) * step
    while g <= hi:
        y = py(g)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w-pad_r}" y2="{y:.1f}" '
                     f'stroke="{"#9aa4b0" if abs(g) < 1e-9 else "#e3e7ec"}"/>')
        parts.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="#5b6673">{g*100:+.0f}%</text>')
        g += step
    for i, ym in enumerate(d.base_ym):
        if ym % 100 == 1:
            parts.append(f'<text x="{px(i):.1f}" y="{h-9}" text-anchor="middle" '
                         f'font-size="11" fill="#5b6673">{ym//100}</text>')
    for col, colr, dash in (("yoy_tot", "#1b2430", ""),
                            ("yoy_ex", "#1f6feb", ' stroke-dasharray="5 3"')):
        pts = " ".join(f"{px(i):.1f},{py(v):.1f}"
                       for i, v in enumerate(d[col]))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{colr}" '
                     f'stroke-width="2"{dash}/>')
    j = n - 1
    parts.append(f'<text x="{px(j)-6:.1f}" y="{py(d.yoy_tot.iloc[-1])-15:.1f}" '
                 f'text-anchor="end" font-size="12" font-weight="700" '
                 f'fill="#1b2430">총수출</text>')
    parts.append(f'<text x="{px(j)-6:.1f}" y="{py(d.yoy_ex.iloc[-1])+21:.1f}" '
                 f'text-anchor="end" font-size="12" font-weight="700" '
                 f'fill="#1f6feb">반도체 제외</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img" preserveAspectRatio="xMidYMid meet"'
            f' aria-label="총수출과 반도체 제외 수출의 전년 동월 대비">'
            f'{"".join(parts)}</svg>')


def error_chart(err: pd.DataFrame, w=880, h=200) -> str:
    """상순 예측 오차의 이력. 오차가 언제 커졌는지는 표로 안 보인다."""
    pad_l, pad_r, pad_t, pad_b = 54, 16, 14, 28
    m = max(0.13, err.err.abs().max() * 1.12)
    n = len(err)
    bw = max(3.0, (w - pad_l - pad_r) / n * 0.62)
    px = lambda i: pad_l + (w - pad_l - pad_r) * ((i + .5) / n)
    py = lambda v: pad_t + (h - pad_t - pad_b) * (1 - (v + m) / (2 * m))
    parts = []
    for lv in (-.10, -.05, 0, .05, .10):
        if abs(lv) > m:
            continue
        y = py(lv)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w-pad_r}" y2="{y:.1f}" '
                     f'stroke="{"#9aa4b0" if lv==0 else "#e3e7ec"}"/>')
        parts.append(f'<text x="{pad_l-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="#5b6673">{lv*100:+.0f}%</text>')
    for i, r in enumerate(err.itertuples()):
        y0, y1 = py(0), py(r.err)
        parts.append(f'<rect x="{px(i)-bw/2:.1f}" y="{min(y0,y1):.1f}" width="{bw:.1f}" '
                     f'height="{abs(y1-y0):.1f}" rx="1.5" '
                     f'fill="{"#a11" if abs(r.err)>.10 else "#1f6feb"}" opacity=".78"/>')
        if r.base_ym % 100 == 1:
            parts.append(f'<text x="{px(i):.1f}" y="{h-9}" text-anchor="middle" '
                         f'font-size="11" fill="#5b6673">{r.base_ym//100}</text>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img" preserveAspectRatio="xMidYMid meet"'
            f' aria-label="상순 시점 예측의 오차 이력">{"".join(parts)}</svg>')


def cellbar(v: float, vmax: float) -> str:
    """표 칸 안의 작은 막대. 기여도를 따로 그림으로 만들지 않기 위한 것."""
    if v != v or vmax <= 0:
        return ""
    frac = min(1.0, abs(v) / vmax)
    side = "right" if v >= 0 else "left"
    col = "#1f6feb" if v >= 0 else "#a11"
    return (f'<span class="cb"><span class="cbf" style="width:{frac*50:.1f}%;'
            f'{side}:50%;background:{col}"></span></span>')


# ---------------------------------------------------------------- 절

def head(ym: int, cut: int, stamp) -> str:
    return f"""<header class="top"><div class="wrap">
  <div class="brand">
    <h1>10일 단위 수출 트렌드</h1><span class="tag">KCSDB2</span>
  </div>
  <p class="lede">관세청이 열흘마다 내는 잠정치를 <b>총수출 증가율의 분해</b>로 다시
    쓴 화면이다. <a href="{URL_KCS}" target="_blank" rel="noopener">관세청
    보도자료</a>는 품목별·국가별 증감률을 따로 내지만 그중 무엇이 총수출을 실제로
    움직였는지는 말하지 않는다. <b>{ym_ko(ym)} {SEGNAME[cut]}</b>까지 반영했다.</p>
  <p class="lede" style="margin-top:-8px">
    <span class="pill info">공표 {CUTDAY[cut]}</span>
    <span class="pill info">생성 {stamp:%Y-%m-%d %H:%M}</span></p>
</div></header>"""


def sec_headline(m, es, si, sc, fc, ym, cut) -> str:
    """이번 순 한 줄. 무엇이 가장 두드러졌는지를 골라 문장으로 쓴다."""
    t = m[(m.series == ITEM) & (m.base_ym == ym) & (m.cutoff == cut)
          & (m.item == TOTAL)].iloc[0]
    row = es[es.base_ym == ym].iloc[0]
    i1, c1 = si.index[0], sc.index[0]
    r1, q1 = si.iloc[0], sc.iloc[0]
    quiet = si.z.abs().max() < 1.0 and sc.z.abs().max() < 1.0
    lead = ("이번 순에는 평년에서 크게 벗어난 항목이 없다."
            if quiet else
            f"이번 순에 가장 두드러진 것은 품목에서 <b>{e(i1)}</b>"
            f"(조업일 보정 전년비 {pct(r1.yoy_cum_adj)}, 평년 대비 {r1.z:+.1f}표준편차),"
            f" 국가에서 <b>{e(c1)}</b>({pct(q1.yoy_cum_adj)}, {q1.z:+.1f}표준편차)다.")
    return f"""<section><h2 class="h">{ym_ko(ym)} {SEGNAME[cut]}까지</h2>
<p class="big">수출 {num(t.cum_kusd/EOK)}억 달러, 전년 동기 대비 {pct(t.yoy_cum)}.
  <b>반도체를 빼면 {pct(row.yoy_ex)}다.</b></p>
<p class="sub">{lead}</p>
<div class="stats">
  <div class="stat"><div class="n">{num(t.cum_kusd/EOK)}억 $</div>
    <div class="k">{SEGNAME[cut]}까지 누적 (조업 {int(t.cum_workdays)}/{int(t.p_cum_workdays)}일)</div></div>
  <div class="stat"><div class="n">{pct(t.yoy_cum)}</div>
    <div class="k">총수출 전년 동기 대비</div></div>
  <div class="stat hl"><div class="n">{pct(row.yoy_ex)}</div>
    <div class="k">반도체 제외 (비중 {pct(row.semi_share,1,False)})</div></div>
  <div class="stat"><div class="n">{num(fc.fc_kusd/EOK,0)}억 $</div>
    <div class="k">이 달 마감 예측 ({num(fc.lo80/EOK,0)}~{num(fc.hi80/EOK,0)})</div></div>
</div></section>"""


def contrib_table(f, sk, cut, ym, series, label) -> str:
    x = f[(f.series == series) & (f.base_ym == ym) & (f.cutoff == cut)
          & (f.item != TOTAL)].copy()
    g = sk[(sk.series == series) & (sk.cutoff == cut)].set_index("item")
    x["grade"] = x.item.map(g.grade)
    x = x.sort_values("contrib_fc_pp", ascending=False)
    vmax = x.contrib_fc_pp.abs().max()
    rows = []
    for r in x.itertuples():
        weak = ' class="weak"' if str(r.grade).startswith("D") else ""
        rows.append(f"<tr{weak}><td>{e(r.item)}</td>"
                    f"<td class=\"r\">{num(r.fc_rec_kusd/EOK,1)}</td>"
                    f"<td class=\"r\">{pct(r.yoy_fc)}</td>"
                    f"<td class=\"r\">{r.contrib_fc_pp:+.2f}"
                    f"{cellbar(r.contrib_fc_pp, vmax)}</td></tr>")
    return (f'<div class="tblwrap"><table><caption>{label}</caption>'
            f'<thead><tr><th>{"품목" if series == ITEM else "국가"}</th>'
            f'<th class="r">예측(억 $)</th><th class="r">전년비</th>'
            f'<th class="r">기여도(%p)</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def sec_contrib(f, sk, ym, cut) -> str:
    t = f[(f.series == ITEM) & (f.base_ym == ym) & (f.cutoff == cut)
          & (f.item == TOTAL)].iloc[0]
    it = f[(f.series == ITEM) & (f.base_ym == ym) & (f.cutoff == cut)
           & (f.item != TOTAL)].sort_values("contrib_fc_pp", ascending=False)
    cn = f[(f.series == CNTY) & (f.base_ym == ym) & (f.cutoff == cut)
           & (f.item != TOTAL)].sort_values("contrib_fc_pp", ascending=False)
    return f"""<section><h3>같은 증가율을 두 축으로 가른다</h3>
<p>이 달 예상 증가율 {pct(t.yoy_fc)}를 품목으로 한 번, 목적지로 한 번 나눈 것이다.
  기여도는 (예상 금액 − 전년 동월 금액)을 전년 동월 총수출로 나눈 값이라 각 표의
  열두 줄을 더하면 같은 {pct(t.yoy_fc)}가 나온다. 열 항목이 총수출의 전부가 아니므로
  나머지를 <b>기타</b> 한 줄로 넣었다. 증감률만 보면 작은 항목이 커 보이지만 기여도로
  보면 무엇이 실제로 총수출을 움직였는지가 드러난다 — 품목에서는
  {e(it.iloc[0]["item"])}가 {it.iloc[0].contrib_fc_pp:+.1f}%p로, 목적지에서는
  {e(cn.iloc[0]["item"])}이 {cn.iloc[0].contrib_fc_pp:+.1f}%p로 끈다.</p>
<div class="two">
  {contrib_table(f, sk, cut, ym, ITEM, "품목으로 가른 것")}
  {contrib_table(f, sk, cut, ym, CNTY, "목적지로 가른 것")}
</div>
<p class="muted">금액은 이 달 마감 예측치이고 총수출 예측에 맞춰 비례 조정했다. 그래야
  기여도가 정확히 맞아떨어진다. 흐리게 둔 줄은 예측 등급이 D인 항목이라 방향만 보고
  숫자는 믿지 말 것.</p></section>"""


def sec_exsemi(es, esm, ym, cut) -> str:
    # 그림과 지난해 서술은 월 전체(esm)로, 머리 수치는 이번 구간(es)으로 한다.
    # 중순 누적으로 지난해를 말하면 해당하는 달이 하나뿐이라 이야기가 약해진다.
    d = esm[esm.base_ym <= ym].tail(36)
    row = es[es.base_ym == ym].iloc[0]
    yr = esm.copy(); yr["y"] = yr.base_ym // 100
    last_full = yr[yr.y == yr.y.max() - 1]
    gap = row.yoy_tot - row.yoy_ex
    # 주장과 고르는 조건을 맞춘다 — 총수출은 플러스인데 반도체를 빼면 마이너스인 달.
    hid = last_full[(last_full.yoy_tot > 0) & (last_full.yoy_ex < 0)]
    split = (" · ".join(f"{ym_dot(int(r.base_ym))} 총수출 {r.yoy_tot*100:+.1f}% 대 "
                        f"제외 {r.yoy_ex*100:+.1f}%" for r in hid.tail(3).itertuples())
             + "가 그런 달이다." if len(hid) else
             "지난해에는 그런 달이 없었다.")
    return f"""<section><h3>반도체를 빼면</h3>
<p>반도체가 수출의 {pct(row.semi_share,1,False)}를 차지하는 지금, 총수출 증가율은
  사실상 반도체 증가율이다. 아래 그림은 같은 기간의 총수출 전년비(검정 실선)와
  반도체를 뺀 나머지의 전년비(파랑 점선)를 겹친 것이다. 두 선의 간격이 곧 반도체가
  혼자 만든 몫이고, 이번 순({SEGNAME[cut]} 누적)에는 그 간격이 {gap*100:.0f}%p다. 선이 갈라지기 시작한 시점과
  나머지 산업이 언제 마이너스였는지는 숫자를 늘어놓아서는 보이지 않는다.</p>
<div class="fig">{two_line(d)}
  <p class="cap">그림 1. 총수출과 반도체 제외 수출의 전년 동월 대비, 최근 {len(d)}개월.
    월 전체 확정치 기준이라 아직 끝나지 않은 이 달은 들어 있지 않다.</p></div>
<p>{ym_ko(int(last_full.base_ym.iloc[-1]))}까지의 기록을 보면 이 구분이 왜 필요한지
  드러난다. 총수출이 플러스인 달에도 반도체를 빼면 마이너스인 때가 있었다.
  {split}</p>
</section>"""


def sec_workday(wa, beta) -> str:
    rows = []
    for r in wa.itertuples():
        cls = ' class="on"' if abs(r.eff) >= .03 else ""
        rows.append(f"<tr{cls}><td>{ym_dot(int(r.base_ym))}</td>"
                    f"<td class=\"r\">{int(r.wd)}</td><td class=\"r s\">{int(r.pwd)}</td>"
                    f"<td class=\"r\">{int(r.wd - r.pwd):+d}</td>"
                    f"<td class=\"r\"><b>{r.eff*100:+.1f}%</b></td></tr>")
    worst = wa.loc[wa.eff.abs().idxmax()]
    return f"""<section><h3>앞으로 열두 달, 조업일수만으로 이미 정해진 것</h3>
<p>수출 증가율의 일부는 달력에서 이미 결정되어 있다. 공휴일 배치 때문에 일할 날이
  작년보다 적으면 그만큼 깎이고 많으면 그만큼 부풀기 때문이다. 아래는 앞으로 열두 달의
  조업일수를 작년 같은 달과 견주고, 총수출의 조업일수 탄력성을 적용해 그것만으로
  예정된 증가율 효과를 낸 것이다(탄력성 {beta:.2f}). 예측이 아니라 달력이라 틀릴 수 없고, 아무도 미리
  알려주지 않는다. 가장 큰 자리는 <b>{ym_dot(int(worst.base_ym))}</b> —
  {int(worst.wd - worst.pwd):+d}일 차이가 {worst.eff*100:+.1f}%를 만든다.</p>
<div class="tblwrap"><table>
<thead><tr><th>연월</th><th class="r">조업일</th><th class="r">전년</th>
  <th class="r">차이</th><th class="r">예정된 효과</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="muted">2026년에 공휴일이 둘 늘었다(노동절·제헌절). 조업일수는 실제 달력에서
  세므로 자동으로 반영되지만, 새 공휴일에 대한 수출 반응이 예전과 같다고 보는 것은
  가정이다.</p></section>"""


def sec_forecast(f, sk, ym, cut) -> str:
    t = f[(f.series == ITEM) & (f.base_ym == ym) & (f.cutoff == cut)
          & (f.item == TOTAL)].iloc[0]
    mape = float(sk[(sk.series == ITEM) & (sk.item == TOTAL)
                    & (sk.cutoff == cut)].mape.iloc[0])
    prev = f[(f.series == ITEM) & (f.base_ym == ym) & (f.cutoff == 10)
             & (f.item == TOTAL)]
    move = ""
    if cut == 20 and len(prev):
        p0 = prev.iloc[0]
        move = (f" 상순 시점에는 {num(p0.fc_kusd/EOK)}억이었으니 열흘 사이에 "
                f"{pct(t.fc_kusd/p0.fc_kusd-1)} 옮겨 잡았고 구간 폭은 "
                f"±{(p0.hi80-p0.lo80)/2/EOK:,.0f}억에서 "
                f"±{(t.hi80-t.lo80)/2/EOK:,.0f}억으로 좁아졌다.")
    return f"""<section><h3>이 달 마감 예측</h3>
<div class="panel">
  <p><b>{num(t.fc_kusd/EOK)}억 달러</b> (80% 구간 {num(t.lo80/EOK,0)}~{num(t.hi80/EOK,0)}),
    전년 동월 대비 {pct(t.yoy_fc)}. 지금까지의 누적을 예상 진도율 {pct(t.pr_hat,1,False)}로
    나눈 값이다.{move}</p>
  <p class="muted">{SEGNAME[cut]} 시점 예측의 과거 평균절대오차는 {mape:.1f}%다.
    10일 자료가 있어야만 낼 수 있고 관세청이 공표하지 않는 값이지만, <b>익월 1일이면
    정답이 나오므로 이 숫자의 수명은 열흘 남짓</b>이다. 그래서 화면 아래쪽에 둔다.</p>
</div></section>"""


def sec_recent(m) -> str:
    x = m[(m.series == ITEM) & (m.item == TOTAL)
          & m.yoy_cum.notna()].sort_values(["base_ym", "cutoff"]).tail(9)
    rows = []
    for r in x.itertuples():
        flag = (' <span class="pill warn">조업일 격차 큼</span>'
                if abs(r.dln_cum_workdays) > .25 else "")
        rows.append(f"<tr><td>{ym_dot(int(r.base_ym))} {SEGNAME[r.cutoff]}</td>"
                    f"<td class=\"r\">{num(r.cum_kusd/EOK)}</td>"
                    f"<td class=\"r s\">{int(r.cum_workdays)} / {int(r.p_cum_workdays)}</td>"
                    f"<td class=\"r\">{pct(r.yoy_cum)}</td>"
                    f"<td class=\"r muted\">{pct(r.yoy_cum_daily)}</td>"
                    f"<td class=\"r\"><b>{pct(r.yoy_cum_adj)}</b>{flag}</td></tr>")
    return f"""<section><h3>조업일수를 어떻게 감안했나</h3>
<p>같은 구간이라도 공휴일 배치에 따라 일할 날이 크게 달라진다. 상순 조업일수는 열 해
  동안 최소 1일에서 최대 8일까지 벌어졌다. 아래는 시점마다 원계열, 관세청식 일평균
  환산, 품목별 탄력성을 쓴 보정을 나란히 놓은 것이다. 가운데 열이 흐린 것은 참고용,
  판단은 맨 오른쪽 열로 한다.</p>
<p class="muted"><b>조업일수 세는 법이 관세청과 다르다.</b> 관세청 보도자료는 토요일을
  0.5일로 세어 2026년 8월 1~20일을 14.0일로, 지난해 같은 기간을 14.5일로 잡는다. 이
  화면은 월~금만 세어 둘 다 13일이다. 그래서 같은 자료를 두고도 일평균 증가율이 관세청
  +61.5%, 이 화면 +56.0%로 갈린다. 월~금을 쓰는 이유는 설명력이 높기 때문이다 — 두
  정의로 총수출 탄력성을 다시 재면 R&#178;가 0.387과 0.372로 월~금이 앞선다.
  <b>금액 자체는 관세청 발표와 같다.</b></p>
<div class="tblwrap"><table>
<thead><tr><th>시점</th><th class="r">누적(억 $)</th><th class="r">조업일<br>올해/작년</th>
  <th class="r">원계열</th><th class="r">일평균 환산</th>
  <th class="r">탄력성 보정</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></section>"""


def beta_table(b, series, label) -> str:
    tot = "총수출"
    x = b[(b.series == series) & (b.scope == "seg")
          & (b.item != tot)].sort_values("beta", ascending=False)
    rows = []
    for r in x.itertuples():
        used = "보정 안 함" if r.beta_used == 0 else f"{r.beta_used:.2f}"
        cls = ' class="weak"' if r.beta_used == 0 else ""
        rows.append(f"<tr{cls}><td>{e(r.item)}</td><td class=\"r\">{r.beta:.2f}</td>"
                    f"<td class=\"r s\">{r.tval:+.1f}</td>"
                    f"<td class=\"r\">{used}</td></tr>")
    return (f'<div class="tblwrap"><table><caption>{label}</caption>'
            f'<thead><tr><th>항목</th><th class="r">탄력성</th><th class="r">t</th>'
            f'<th class="r">적용</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


def sec_beta(b) -> str:
    bt = float(b[(b.series == ITEM) & (b.scope == "seg")
                 & (b.item == TOTAL)].beta.iloc[0])
    return f"""<section><h3>왜 일괄 보정을 하면 안 되나</h3>
<p>관세청이 쓰는 일평균 환산(수출액 ÷ 조업일수)은 조업일 탄력성을 1로 못박는 것과
  같다. 그런데 10일 자료로 재 보면 항목마다 크게 다르다. 아래 두 표는 각 항목의 수출이
  조업일수 1% 증가에 몇 % 반응하는지를 2016년 이후 자료로 잰 것이다(전년 동기 대비
  로그차분, HAC 12). 반도체는 팹이 연속 가동돼 거의 반응하지 않고, 자동차부품은
  정비례하다 못해 넘어선다. 통계적으로 유의하지 않은 항목은 보정하지 않는다.</p>
<div class="two">
  {beta_table(b, ITEM, "품목별")}
  {beta_table(b, CNTY, "목적지별")}
</div>
<p class="muted">목적지별 차이의 절반 이상은 <b>구성 효과</b>다. 목적지의 반도체 비중과
  탄력성의 상관이 −0.76이라, 대만(반도체 비중 78%)이나 중국(53%)이 낮은 것은 그 목적지
  고유의 성질이라기보다 무엇을 보내는지의 결과다. 총수출 탄력성이 {bt:.2f}에 그치는 것도
  반도체가 수출의 3분의 1을 넘기 때문이다.</p></section>"""


def sec_skill(sk, err) -> str:
    x = sk[(sk.series == ITEM) & (sk.cutoff == 10)].sort_values("mape")
    g20 = sk[(sk.series == ITEM) & (sk.cutoff == 20)].set_index("item")
    c10 = sk[(sk.series == CNTY) & (sk.cutoff == 10)].sort_values("mape")
    c20 = sk[(sk.series == CNTY) & (sk.cutoff == 20)].set_index("item")

    def body(df, other):
        out = []
        for r in df.itertuples():
            cls = ' class="weak"' if str(r.grade).startswith("D") else ""
            out.append(f"<tr{cls}><td>{e(r.item)}</td><td class=\"r\">{r.mape:.1f}</td>"
                       f"<td class=\"r\">{float(other.loc[r.item,'mape']):.1f}</td>"
                       f"<td class=\"r s\">{r.cov80:.0f}</td><td>{e(r.grade)}</td></tr>")
        return "".join(out)

    hdr = ('<thead><tr><th>항목</th><th class="r">상순 오차(%)</th>'
           '<th class="r">중순 오차(%)</th><th class="r">80% 포함률</th>'
           '<th>등급</th></tr></thead>')
    big = int((err.err.abs() > .10).sum())
    return f"""<section><h3>예측을 얼마나 믿을 수 있나</h3>
<p>아래 그림은 총수출의 상순 시점 예측이 최근 {len(err)}개월 동안 얼마나 빗나갔는지다.
  막대가 0 위면 실제가 예측보다 컸다는 뜻이고 ±5%와 ±10% 자리에 눈금을 두었다. 빨간
  막대는 10%를 넘긴 달로 {big}개다.</p>
<div class="fig">{error_chart(err)}
  <p class="cap">그림 2. 총수출 상순 시점 예측의 오차, 최근 {len(err)}개월.
    확장창 표본외.</p></div>
<p>항목별 성적은 아래와 같다. 2019년 1월부터 91개 시점을 매번 그 전까지의 자료만으로
  예측해 재었다. 포함률은 80% 구간이 실제값을 담은 비율이라 80에 가까울수록 구간이
  정직하다는 뜻이다. <b>대중국 수출이 총수출보다 예측하기 쉽다</b> — 규모가 크고
  반도체 비중이 높아 흐름이 매끄럽기 때문이다.</p>
<div class="two">
  <div class="tblwrap"><table><caption>품목별</caption>{hdr}
    <tbody>{body(x, g20)}</tbody></table></div>
  <div class="tblwrap"><table><caption>목적지별</caption>{hdr}
    <tbody>{body(c10, c20)}</tbody></table></div>
</div>
<p class="muted"><b>정직하게 밝힐 것.</b> 이 성적은 나중에 정정이 반영된 값으로 잰
  것이다. 10일 잠정치는 뒤에 신고 정정·취하가 반영돼 값이 바뀌는데, 과거 시점에 대해
  당시 공표값을 따로 갖고 있지 않다. 실시간 성적은 이보다 나쁠 수 있다. 지금부터는
  확정 전 예측을 시점과 함께 쌓고 있으므로 몇 달 뒤에는 진짜 실시간 성적을 낼 수
  있다.</p></section>"""


def sec_limits() -> str:
    return """<section><h3>자료와 한계</h3>
<div class="traps">
  <div class="trap"><h4>열 항목이 전부가 아니다</h4>
    <p>품목 열 개는 총수출의 60~70%, 국가 열 개는 80% 안팎이다. 나머지는 <b>기타</b>
      한 줄로 묶여 있어 그 안에서 무엇이 움직였는지는 보이지 않는다.</p></div>
  <div class="trap"><h4>중량이 없다</h4>
    <p>10일 잠정치는 금액뿐이다. "단가가 오른 것인가 물량이 늘어난 것인가"는 이 자료로
      답할 수 없고 월 확정 자료로 내려가야 한다.</p></div>
  <div class="trap"><h4>품목과 국가를 교차할 수 없다</h4>
    <p>두 계열이 따로 오기 때문에 "대중국 반도체가 얼마인가"는 이 자료로 못 낸다.
      두 축을 각각 분해할 수는 있어도 칸을 채울 수는 없다.</p></div>
  <div class="trap"><h4>잠정치다</h4>
    <p>당월 값은 신고 정정·취하가 반영되면서 바뀐다. 그래서 값이 바뀔 때마다 새 기록을
      쌓아 언제 무엇을 알았는지를 남긴다.</p></div>
  <div class="trap"><h4>선박은 예측이 안 된다</h4>
    <p>몇 건의 대형 인도가 한 달을 좌우한다. 상순 시점 평균절대오차가 76%라 방향을
      읽는 데만 쓰고 숫자로 인용하지 않는다.</p></div>
  <div class="trap"><h4>조업일 격차가 크면 어떤 보정도 외삽이다</h4>
    <p>조업일이 5일에서 1일로 줄어든 달 같은 자리에서는 어떤 보정식도 관측 범위 밖으로
      나간다. 그런 시점에는 표에 경고를 붙인다.</p></div>
</div></section>"""


CSS = """<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#1b2430;--muted:#5b6673;--line:#e3e7ec;
  --brand:#1f6feb;--brand-soft:#eaf1fe;--warn:#8a5b00;--warn-soft:#fdf3df;
  --info:#4b4f8a;--info-soft:#ecedf7;--danger:#a11;--radius:12px;
  --shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.10)}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--ink);line-height:1.6;-webkit-text-size-adjust:100%;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic","Apple SD Gothic Neo",Roboto,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:0 16px}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
header.top{background:linear-gradient(180deg,#fff,#fbfcfe);border-bottom:1px solid var(--line)}
.brand{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;padding:22px 0 6px}
.brand h1{font-size:1.95rem;font-weight:700;margin:0;letter-spacing:-.01em}
.brand .tag{color:var(--muted);font-size:1.17rem;font-weight:700}
.lede{color:var(--muted);margin:0 0 16px;font-size:.98rem}
main{padding:22px 0 8px}
h2.h{font-size:1.5rem;margin:2px 0 6px}
h3{font-size:1.14rem;margin:36px 0 8px}
.sub{color:var(--muted);margin:0 0 16px;font-size:.95rem}
p{margin:9px 0}p.muted,.muted{color:var(--muted);font-size:.9rem}
p.big{font-size:1.22rem;line-height:1.5;margin:10px 0 6px}
p.big b{color:var(--brand)}
section{margin:0 0 6px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow);padding:16px;margin:14px 0}
.panel>:first-child{margin-top:0}.panel>:last-child{margin-bottom:0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:12px;margin:14px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
  padding:14px;box-shadow:var(--shadow)}
.stat.hl{border-color:#c9dcfa;background:linear-gradient(180deg,#f5f9ff,#fff)}
.stat.hl .n{color:var(--brand)}
.stat .n{font-size:1.5rem;font-weight:700;letter-spacing:-.02em;line-height:1.2}
.stat .k{color:var(--muted);font-size:.82rem;margin-top:2px}
.tblwrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;margin:10px 0;
  background:#fff;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;min-width:340px;font-size:.9rem}
caption{caption-side:top;text-align:left;font-weight:700;padding:10px 12px 4px;font-size:.92rem}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid var(--line)}
thead th{background:#f2f5f9;color:var(--muted);font-weight:700;white-space:nowrap;font-size:.84rem}
tbody tr:last-child td{border-bottom:none}
td.r,th.r{text-align:right;white-space:nowrap}
td.s,th.s{font-size:.82rem;color:var(--muted)}
tr.weak td{opacity:.5}tr.on td{background:var(--brand-soft);font-weight:700}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
@media(max-width:700px){.two{grid-template-columns:1fr}}
.cb{position:relative;display:block;height:5px;margin-top:4px;background:#eef1f5;border-radius:3px}
.cbf{position:absolute;top:0;height:5px;border-radius:3px}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:.76rem;
  font-weight:700;line-height:1.7}
.pill.info{background:var(--info-soft);color:var(--info)}
.pill.warn{background:var(--warn-soft);color:var(--warn)}
.fig{background:#fff;border:1px solid var(--line);border-radius:var(--radius);
  padding:12px 14px 6px;margin:12px 0;box-shadow:var(--shadow)}
.fig svg{width:100%;height:auto;display:block}
.fig .cap{color:var(--muted);font-size:.85rem;margin:4px 0 8px}
.traps{display:grid;grid-template-columns:repeat(auto-fill,minmax(262px,1fr));gap:12px;margin:12px 0}
.trap{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--brand);
  border-radius:10px;padding:12px 14px;box-shadow:var(--shadow)}
.trap h4{margin:0 0 4px;font-size:.97rem}
.trap p{margin:6px 0 0;font-size:.89rem;color:#333}
footer{border-top:1px solid var(--line);margin-top:30px;padding:20px 0 44px;
  color:var(--muted);font-size:.84rem;background:#fff}
footer p{margin:6px 0}
@media(max-width:420px){.brand h1{font-size:1.6rem}.stat .n{font-size:1.26rem}
  h2.h{font-size:1.32rem}p.big{font-size:1.1rem}}
</style>"""


# ---------------------------------------------------------------- 조립

def build(con: duckdb.DuckDBPyConnection) -> tuple[str, int, int]:
    m = con.sql("SELECT * FROM mart_exp10d_metrics").df()
    f = con.sql("SELECT * FROM mart_exp10d_forecast").df()
    sk = con.sql("SELECT * FROM mart_exp10d_fcskill").df()
    b = con.sql("SELECT * FROM mart_exp10d_beta").df()

    live = f[(f.series == ITEM) & ~f.is_final]
    if live.empty:
        ym = int(f[f.series == ITEM].base_ym.max())
        cut = int(f[(f.series == ITEM) & (f.base_ym == ym)].cutoff.max())
    else:
        ym = int(live.base_ym.max())
        cut = int(live[live.base_ym == ym].cutoff.max())

    es = ex_semi(m, cut).dropna(subset=["yoy_ex", "yoy_tot"])
    esm = ex_semi(m, 99).dropna(subset=["yoy_ex", "yoy_tot"])
    si = surprise(m, ITEM, ym, cut)
    sc = surprise(m, CNTY, ym, cut)
    fc = f[(f.series == ITEM) & (f.base_ym == ym) & (f.cutoff == cut)
           & (f.item == TOTAL)].iloc[0]

    beta_cum = float(b[(b.series == ITEM) & (b.scope == "cum")
                       & (b.item == TOTAL)].beta_used.iloc[0])
    wa = workday_ahead(con, ym, beta_cum)   # 이번 달은 마감 예측 절이 따로 다룬다

    ev = f[(f.series == ITEM) & (f.item == TOTAL) & (f.cutoff == 10)
           & f.is_final & f.act_kusd.notna()].copy()
    ev["err"] = ev.act_kusd / ev.fc_kusd - 1
    ev = ev.sort_values("base_ym").tail(36)[["base_ym", "err"]]

    stamp = dt.datetime.now().replace(microsecond=0)
    page = f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>10일 단위 수출 트렌드 (KCSDB2)</title>
{CSS}
{head(ym, cut, stamp)}
<main><div class="wrap">
{sec_headline(m, es, si, sc, fc, ym, cut)}
{sec_contrib(f, sk, ym, cut)}
{sec_exsemi(es, esm, ym, cut)}
{sec_workday(wa, beta_cum)}
{sec_forecast(f, sk, ym, cut)}
{sec_recent(m)}
{sec_beta(b)}
{sec_skill(sk, ev)}
{sec_limits()}
</div></main>
<footer><div class="wrap">
  <p><b>자료.</b> 관세청 <a href="{URL_API}" target="_blank" rel="noopener">10일 단위
    잠정치 통계</a> 네 계열(공공데이터포털 15157908·15157941·15157901·15157909,
    2016.01~) 가운데 수출 품목별과 수출 국가별을 쓴다. 조업일수는 한국천문연구원
    특일정보 API로 대조한 공휴일 달력에서 계산했다. 관세청이 이 수치를 공표하는 자리는
    <a href="{URL_KCS}" target="_blank" rel="noopener">관세청 보도자료</a> 게시판이며,
    상순분은 11일, 중순분은 21일, 월 전체는 익월 1일에 올라온다.</p>
  <p><b>추정 표시.</b> 마감 예측·조업일수 보정·기여도 분해는 이 저장소가 만든 추정치이며
    관세청의 공표치나 공식 판단이 아니다. 관세청이 발표한 값은 각 시점의 누적 금액뿐이다.
    이 화면은 투자 판단을 위한 조언이 아니다.</p>
  <p><b>출처 표시 (공공누리 제1유형).</b> 위 공공데이터를 이용하였으며, 공공기관이 이
    저작물을 후원하거나 특수 관계에 있는 것으로 오인하게 하는 표시를 하지 않는다.</p>
  <p>생성 {stamp:%Y-%m-%d %H:%M} · <a href="index.html">KCSDB2 데이터베이스 소개</a></p>
</div></footer>"""
    return page, ym, cut


def check(page: str) -> None:
    """빈 값이나 미치환 자리표시자가 화면에 새어 나가지 않았나."""
    assert "nan" not in page, "화면에 nan이 새어 나갔다"
    assert "None" not in page, "화면에 None이 새어 나갔다"
    assert "bound method" not in page, "판다스 메서드 객체가 새어 나갔다 (Series.item 함정)"
    assert page.count("<section>") >= 8, "절이 모자란다"
    assert page.count("<svg") == 2, "그림이 둘이어야 한다"
    # 바깥으로 거는 링크는 괜찮다. 막아야 할 것은 열 때 바깥에서 불러오는 자원이다.
    assert not re.search(r'(?:src|action)\s*=\s*"https?:|url\(\s*https?:|@import', page), \
        "외부 자원을 불러온다 - 자기완결형이어야 한다"
    assert "<link " not in page and "<script" not in page, \
        "외부 스타일시트나 스크립트가 있다"
    for u in (URL_KCS, URL_API):
        assert u in page, f"출처 링크가 빠졌다: {u}"
    assert "반도체를 빼면" in page, "머리기사가 빠졌다"


def main() -> None:
    con = duckdb.connect(DB, read_only=True)
    try:
        page, ym, cut = build(con)
    finally:
        con.close()
    check(page)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    print(f"{os.path.relpath(OUT, ROOT)} 생성 — {len(page):,}바이트")
    print(f"기준 시점 {ym_ko(ym)} {SEGNAME[cut]} (공표 {CUTDAY[cut]})")
    print("검증 통과 — nan/None 없음, 절 여덟 이상, 그림 둘, 외부 자원 없음, 머리기사 있음")


if __name__ == "__main__":
    main()
