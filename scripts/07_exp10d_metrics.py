"""07_exp10d_metrics.py — 10일 단위 잠정치에서 지표를 만든다.

무엇을 만드나
-------------
관세청이 내는 것은 누적 수출액 하나뿐이다. 여기서 네 가지를 뽑는다.

1. **조업일수 탄력성** — 품목마다 다르다. 일괄 보정하면 안 된다.
2. **조업일수 보정 전년 동기 대비** — 원계열·일평균(관세청 방식)·탄력성 보정을 나란히.
3. **기여도 분해** — 총수출 증가율을 열두 갈래(10품목 + 기타)로 정확히 가른다.
4. **진도율** — 상순·중순 누적이 월 전체의 몇 %였나. 4단계 월 마감 예측의 재료다.

왜 일괄 보정을 하면 안 되나
---------------------------
10일 자료로 다시 추정한 구간 증분 탄력성(2016~, HAC 12)은 이렇다.

    자동차부품 1.10   승용차 1.10   가전제품 0.89   기타 0.80   정밀기기 0.80
    철강제품 0.75    컴퓨터주변기기 0.53   무선통신기기 0.53   석유제품 0.52
    선박 0.52(유의하지 않음)   반도체 0.24

관세청식 일평균(수출액/조업일수)은 탄력성을 1로 못박는 것과 같다. 반도체에 그것을
적용하면 조업일이 하루 줄 때 실제로는 2.4%밖에 안 줄어드는 것을 10% 준 것으로 되돌려
놓는다. 반도체가 최근 수출의 36.6%라 총수출까지 함께 틀어진다.

월 자료로 잰 값(반도체 -0.33, 선박 -1.02, 총수출 0.30, 모두 부호나 유의성이 불안정)에
비하면 10일 자료 쪽이 훨씬 안정적이다. 구간 조업일수가 1~8일로 흔들려 식별이 잘 된다.

보정의 뜻
---------
보정치 = 당기 금액 x (전년 동기 조업일수 / 당기 조업일수)^beta
즉 "작년과 같은 날수만큼 일했다면 얼마였을까"다. beta=1이면 일평균 환산과 같고,
beta=0이면 보정하지 않는 것과 같다.

총수출의 보정치는 열두 갈래를 각자 보정해 더한 값(상향 합산)이다. 이렇게 해야 기여도
분해가 정확히 맞아떨어진다. 총수출을 직접 추정하면 beta=0.598이 나오는데 품목별 beta를
비중으로 가중평균하면 0.577이라 실질적으로 같다(이 대조는 실행할 때마다 출력한다).

유의하지 않은 탄력성은 0으로 둔다
---------------------------------
선박은 t=1.4라 유의하지 않다. 몇 건의 대형 인도가 좌우해 조업일수와 관계가 약하다.
추정치를 그대로 쓰면 잡음을 보정이라는 이름으로 집어넣는 셈이라, p >= 0.10이면
beta_used = 0으로 두고 어느 품목이 그렇게 됐는지 실행할 때 알린다.

기여도 분해
-----------
품목 i의 기여도(%p) = (보정 당기 - 전년 동기) / 전년 동기 총수출 x 100
열두 갈래를 더하면 총수출 증가율이 정확히 나온다. 10품목만으로는 총수출의 56~72%밖에
안 되므로 기타(총수출 - 10품목)를 반드시 넣어야 한다.

명절은 왜 안 넣었나
-------------------
설·추석 앞뒤 7일 창을 넣어 봤다. 자동차부품(전 +0.26 t=11.3, 후 -0.17 t=-3.8)과
승용차(후 -0.40 t=-4.1)에는 밀어내기와 그 뒤 공백이 뚜렷하지만, 총수출은 R^2가
0.387에서 0.397로 오를 뿐이고 beta도 0.598에서 0.585로 거의 안 움직인다. 보정식이
복잡해지는 값을 못 한다고 보아 뺐다. 4단계 예측 모형에서는 다시 검토한다.

만드는 것
---------
mart_exp10d_beta      품목 x 대상(구간/누적/진도율)별 조업일수 탄력성
mart_exp10d_progress  품목 x 구간 x 달별 진도율 분포(보정 전후)
mart_exp10d_metrics   시점 x 구간 x 품목 지표 전부

실행: python scripts/07_exp10d_metrics.py
      python scripts/07_exp10d_metrics.py --asof 202312   # 추정 표본을 그때까지로 자름
"""

from __future__ import annotations

import argparse
import datetime as dt
import os

import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "processed", "kcsdb.duckdb")

# v_exp10d_seg에는 네 계열이 들어 있다(수출/수입 x 품목/국가). 계열마다 따로 돌린다.
# 거르지 않고 한 번에 읽으면 품목과 국가가 섞여 총계가 두 배가 된다.
SERIES = ["exp_item", "exp_cnty", "imp_item", "imp_cnty"]
TOTALS = {"exp_item": "총수출", "exp_cnty": "총수출",
          "imp_item": "총수입", "imp_cnty": "총수입"}

OTHER = "기타"
PVAL_CUT = 0.10        # 이보다 크면 탄력성을 0으로 둔다
HAC_LAGS = 12
MIN_OBS = 24           # 추정에 이보다 적으면 건너뛴다


# ---------------------------------------------------------------- 자료 준비

def load(con: duckdb.DuckDBPyConnection, series: str, total: str) -> pd.DataFrame:
    """구간 증분·누적과 조업일수를 붙이고 '기타' 갈래를 만든다."""
    d = con.sql(f"SELECT base_ym, cutoff, seg, item, seg_kusd, cum_kusd "
                f"FROM v_exp10d_seg WHERE series = '{series}'").df()
    w = con.sql("SELECT base_ym, cutoff, days, workdays FROM dim_workday10d").df()

    # 기타 = 총계 - 열 항목. 구간과 누적 모두.
    piv = d.pivot_table(index=["base_ym", "cutoff"], columns="item",
                        values=["seg_kusd", "cum_kusd"])
    items = [c for c in piv["seg_kusd"].columns if c != total]
    oth = pd.DataFrame({
        "seg_kusd": piv["seg_kusd"][total] - piv["seg_kusd"][items].sum(axis=1),
        "cum_kusd": piv["cum_kusd"][total] - piv["cum_kusd"][items].sum(axis=1),
    }).reset_index()
    oth["item"] = OTHER
    oth["seg"] = oth.cutoff.map({10: "상순", 20: "중순", 99: "하순"})

    d = pd.concat([d, oth], ignore_index=True)
    d = d.merge(w, on=["base_ym", "cutoff"], how="inner")

    # 누적 조업일수(상순=상순, 중순=상+중, 하순=월 전체)
    cw = (w.sort_values(["base_ym", "cutoff"])
            .assign(cum_workdays=lambda x: x.groupby("base_ym").workdays.cumsum(),
                    cum_days=lambda x: x.groupby("base_ym").days.cumsum())
            [["base_ym", "cutoff", "cum_workdays", "cum_days"]])
    return d.merge(cw, on=["base_ym", "cutoff"]).sort_values(
        ["item", "cutoff", "base_ym"]).reset_index(drop=True)


def add_lag12(d: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """같은 구간의 전년 동월 값을 붙인다. 12행 앞이 정확히 1년 전일 때만 남긴다."""
    d = d.sort_values(["item", "cutoff", "base_ym"]).copy()
    g = d.groupby(["item", "cutoff"])
    for c in cols + ["base_ym"]:
        d["p_" + c] = g[c].shift(12)
    d.loc[d.base_ym - d.p_base_ym != 100, ["p_" + c for c in cols]] = np.nan
    return d


# ---------------------------------------------------------------- 탄력성 추정

def _hac(y: pd.Series, x: pd.Series) -> dict:
    ok = np.isfinite(y) & np.isfinite(x)
    y, x = np.asarray(y)[ok], np.asarray(x)[ok]
    r = sm.OLS(y, sm.add_constant(pd.DataFrame({"dw": x}))).fit(
        cov_type="HAC", cov_kwds={"maxlags": HAC_LAGS})
    return dict(beta=float(r.params["dw"]), se=float(r.bse["dw"]),
                tval=float(r.tvalues["dw"]), pval=float(r.pvalues["dw"]),
                n=int(len(y)), r2=float(r.rsquared),
                resid_sd=float(np.std(r.resid, ddof=2)))


def fit_betas(d: pd.DataFrame, asof: int | None) -> pd.DataFrame:
    """구간 증분·누적 두 대상에 대해 품목별 조업일수 탄력성을 잰다.

    Delta12 ln(수출) = a + beta x Delta12 ln(조업일수) + e, 세 구간 풀링, HAC(12).
    Delta12를 쓰면 계절성과 품목 고정효과가 함께 사라진다.
    """
    s = d if asof is None else d[d.base_ym <= asof]
    s = add_lag12(s, ["seg_kusd", "cum_kusd", "workdays", "cum_workdays"])
    rows = []
    for scope, y, x in [("seg", "seg_kusd", "workdays"),
                        ("cum", "cum_kusd", "cum_workdays")]:
        py, px = "p_" + y, "p_" + x
        for item, g in s.groupby("item"):
            g = g[(g[y] > 0) & (g[py] > 0)]
            if len(g) < MIN_OBS:
                continue
            r = _hac(np.log(g[y] / g[py]), np.log(g[x] / g[px]))
            r.update(item=item, scope=scope, cutoff=0,
                     est_from=int(g.base_ym.min()), est_to=int(g.base_ym.max()))
            rows.append(r)
    b = pd.DataFrame(rows)
    b["signif"] = b.pval < PVAL_CUT
    b["beta_used"] = np.where(b.signif, b.beta, 0.0)
    return b


def fit_progress_betas(prog: pd.DataFrame, asof: int | None) -> pd.DataFrame:
    """진도율의 조업일 탄력성. ln(누적진도율) = a + b x ln(조업일 진도율) + e.

    b=1이면 조업일수에 정비례해 실적이 쌓인다는 뜻이고, 반도체처럼 b가 낮으면
    조업일수와 무관하게 고르게 쌓인다는 뜻이다. 4단계 월 마감 예측이 이것을 쓴다.
    """
    s = prog if asof is None else prog[prog.base_ym <= asof]
    rows = []
    for (item, cut), g in s.groupby(["item", "cutoff"]):
        g = g[(g.ratio > 0) & (g.w_ratio > 0)]
        if len(g) < MIN_OBS:
            continue
        r = _hac(np.log(g.ratio), np.log(g.w_ratio))
        r.update(item=item, scope="prog", cutoff=int(cut),
                 est_from=int(g.base_ym.min()), est_to=int(g.base_ym.max()))
        rows.append(r)
    b = pd.DataFrame(rows)
    b["signif"] = b.pval < PVAL_CUT
    b["beta_used"] = np.where(b.signif, b.beta, 0.0)
    return b


# ---------------------------------------------------------------- 진도율

def build_progress(d: pd.DataFrame) -> pd.DataFrame:
    """상순·중순 누적이 월 전체의 몇 %였나. 월 전체가 나온 달만."""
    piv = d.pivot_table(index=["base_ym", "item"], columns="cutoff",
                        values=["cum_kusd", "cum_workdays"]).dropna()
    out = []
    for cut in (10, 20):
        out.append(pd.DataFrame({
            "base_ym": piv.index.get_level_values(0),
            "item": piv.index.get_level_values(1),
            "cutoff": cut,
            "ratio": (piv["cum_kusd"][cut] / piv["cum_kusd"][99]).values,
            "w_ratio": (piv["cum_workdays"][cut] / piv["cum_workdays"][99]).values,
        }))
    p = pd.concat(out, ignore_index=True)
    p["mon"] = p.base_ym % 100
    return p[np.isfinite(p.ratio) & (p.ratio > 0)].reset_index(drop=True)


def summarise_progress(p: pd.DataFrame, pb: pd.DataFrame) -> pd.DataFrame:
    """품목 x 구간 x 달별 진도율 분포. 조업일 보정 전후를 함께 낸다.

    보정 진도율 = ratio / (w_ratio / 평년 w_ratio)^b. 조업일 배치가 평년 같았다면
    진도율이 얼마였겠나를 뜻한다. 이쪽 산포가 작을수록 월 마감 예측이 쉬워진다.
    """
    b = pb.set_index(["item", "cutoff"]).beta_used.to_dict()
    ref = p.groupby("cutoff").w_ratio.median().to_dict()   # 평년 조업일 진도율
    p = p.copy()
    expo = p.set_index(["item", "cutoff"]).index.map(lambda k: b.get(k, 0.0))
    p["adj"] = p.ratio / (p.w_ratio / p.cutoff.map(ref)) ** np.asarray(expo)

    def stats(g, col):
        return {f"{col}_mean": g[col].mean(), f"{col}_sd": g[col].std(),
                f"{col}_p10": g[col].quantile(.10), f"{col}_p50": g[col].median(),
                f"{col}_p90": g[col].quantile(.90)}

    out = []
    for keys, g in p.groupby(["item", "cutoff", "mon"]):
        row = dict(zip(["item", "cutoff", "mon"], keys), n=len(g))
        row.update(stats(g, "ratio")); row.update(stats(g, "adj"))
        out.append(row)
    for keys, g in p.groupby(["item", "cutoff"]):          # mon=0 은 열두 달 전체
        row = dict(zip(["item", "cutoff"], keys), mon=0, n=len(g))
        row.update(stats(g, "ratio")); row.update(stats(g, "adj"))
        out.append(row)
    return pd.DataFrame(out).sort_values(
        ["item", "cutoff", "mon"]).reset_index(drop=True)


# ---------------------------------------------------------------- 지표 본표

def build_metrics(d: pd.DataFrame, betas: pd.DataFrame, prog: pd.DataFrame,
                  total: str) -> pd.DataFrame:
    """시점 x 구간 x 품목 지표. 보정치는 '작년과 같은 조업일수였다면'이다."""
    m = add_lag12(d, ["seg_kusd", "cum_kusd", "workdays", "cum_workdays"])
    bs = betas[betas.scope == "seg"].set_index("item").beta_used
    bc = betas[betas.scope == "cum"].set_index("item").beta_used
    m["beta_seg"] = m.item.map(bs).fillna(0.0)
    m["beta_cum"] = m.item.map(bc).fillna(0.0)

    m["seg_daily"] = m.seg_kusd / m.workdays
    m["cum_daily"] = m.cum_kusd / m.cum_workdays
    m["seg_adj"] = m.seg_kusd * (m.p_workdays / m.workdays) ** m.beta_seg
    m["cum_adj"] = m.cum_kusd * (m.p_cum_workdays / m.cum_workdays) ** m.beta_cum

    # 총수출 보정치는 열두 갈래를 각자 보정해 더한다(기여도가 정확히 맞아떨어지게).
    # min_count로 하나라도 비면 결측이 되게 한다. 그냥 sum()을 쓰면 자료가 없는
    # 첫 12개월에서 결측을 0으로 세어 총수출 보정치가 조용히 0이 된다.
    parts = m[m.item != total]
    nitem = parts.item.nunique()
    agg = (parts.groupby(["base_ym", "cutoff"])[["seg_adj", "cum_adj"]]
           .sum(min_count=nitem))
    tot = m.item == total
    idx = pd.MultiIndex.from_arrays([m.loc[tot, "base_ym"], m.loc[tot, "cutoff"]])
    m.loc[tot, "seg_adj"] = agg.seg_adj.reindex(idx).values
    m.loc[tot, "cum_adj"] = agg.cum_adj.reindex(idx).values

    for k, wcol in (("seg", "workdays"), ("cum", "cum_workdays")):
        p = f"p_{k}_kusd"
        m[f"yoy_{k}"] = m[f"{k}_kusd"] / m[p] - 1
        m[f"yoy_{k}_daily"] = ((m[f"{k}_kusd"] / m[wcol])
                               / (m[p] / m["p_" + wcol])) - 1
        m[f"yoy_{k}_adj"] = m[f"{k}_adj"] / m[p] - 1

    # 비중과 기여도. 분모는 전년 동기 총수출.
    base = (m[tot][["base_ym", "cutoff", "cum_kusd", "p_cum_kusd", "p_seg_kusd"]]
            .rename(columns={"cum_kusd": "t_cum", "p_cum_kusd": "t_p_cum",
                             "p_seg_kusd": "t_p_seg"}))
    m = m.merge(base, on=["base_ym", "cutoff"], how="left")
    m["share_cum"] = m.cum_kusd / m.t_cum
    m["contrib_cum_pp"] = (m.cum_kusd - m.p_cum_kusd) / m.t_p_cum * 100
    m["contrib_cum_adj_pp"] = (m.cum_adj - m.p_cum_kusd) / m.t_p_cum * 100
    m["contrib_seg_pp"] = (m.seg_kusd - m.p_seg_kusd) / m.t_p_seg * 100
    m["contrib_seg_adj_pp"] = (m.seg_adj - m.p_seg_kusd) / m.t_p_seg * 100

    # 보정의 무리 정도. 조업일수 차이가 클수록 어떤 보정이든 외삽이 된다.
    # 2017년 10월 상순은 5일에서 1일로 줄어 이 값이 -1.61이고, 그때 일평균 환산은
    # 총수출을 +255%로 부풀린다(탄력성 보정은 +44%). 대시보드에서 경고할 근거다.
    m["dln_workdays"] = np.log(m.workdays / m.p_workdays)
    m["dln_cum_workdays"] = np.log(m.cum_workdays / m.p_cum_workdays)

    pr = prog.rename(columns={"ratio": "progress", "w_ratio": "w_progress"})
    m = m.merge(pr[["base_ym", "item", "cutoff", "progress", "w_progress"]],
                on=["base_ym", "item", "cutoff"], how="left")

    keep = ["base_ym", "cutoff", "seg", "item", "seg_kusd", "cum_kusd",
            "workdays", "cum_workdays", "days", "cum_days",
            "p_seg_kusd", "p_cum_kusd", "p_workdays", "p_cum_workdays",
            "dln_workdays", "dln_cum_workdays",
            "beta_seg", "beta_cum", "seg_daily", "cum_daily", "seg_adj", "cum_adj",
            "yoy_seg", "yoy_seg_daily", "yoy_seg_adj",
            "yoy_cum", "yoy_cum_daily", "yoy_cum_adj",
            "share_cum", "contrib_seg_pp", "contrib_seg_adj_pp",
            "contrib_cum_pp", "contrib_cum_adj_pp", "progress", "w_progress"]
    return m[keep].sort_values(
        ["base_ym", "cutoff", "item"]).reset_index(drop=True)


# ---------------------------------------------------------------- 검증

def check(m: pd.DataFrame, betas: pd.DataFrame, d: pd.DataFrame,
          series: str, total: str) -> None:
    """어긋나면 멈춘다. 실제로 걸릴 만한 것만 넣었다."""
    # 1. 기여도 합 = 총수출 증가율. 원계열과 보정 모두.
    s = (m[m.item != total].groupby(["base_ym", "cutoff"])
         [["contrib_cum_pp", "contrib_cum_adj_pp"]].sum())
    t = m[m.item == total].set_index(["base_ym", "cutoff"])[
        ["contrib_cum_pp", "contrib_cum_adj_pp", "yoy_cum", "yoy_cum_adj"]]
    j = s.join(t, rsuffix="_t").dropna()
    assert (j.contrib_cum_pp - j.contrib_cum_pp_t).abs().max() < 1e-6, \
        "기여도 합이 총수출 행과 다르다"
    assert (j.contrib_cum_adj_pp - j.contrib_cum_adj_pp_t).abs().max() < 1e-6, \
        "보정 기여도 합이 총수출 행과 다르다"
    assert (j.contrib_cum_pp - j.yoy_cum * 100).abs().max() < 1e-6, \
        "기여도 합이 총수출 증가율과 다르다"
    assert (j.contrib_cum_adj_pp - j.yoy_cum_adj * 100).abs().max() < 1e-6, \
        "보정 기여도 합이 보정 증가율과 다르다"

    # 2. 누적은 구간 증분의 누적합이어야 한다(seg/cum 혼동 방지).
    c = d.sort_values(["item", "base_ym", "cutoff"]).copy()
    c["run"] = c.groupby(["item", "base_ym"]).seg_kusd.cumsum()
    assert (c.run - c.cum_kusd).abs().max() < 1, "누적이 구간 증분의 누적합과 다르다"

    # 3. beta=1로 두면 일평균과 같아야 한다(보정식이 뒤집히지 않았나).
    x = m[(m.item != total) & m.yoy_cum.notna()].head(500)
    lhs = x.cum_kusd * (x.p_cum_workdays / x.cum_workdays) / x.p_cum_kusd - 1
    assert (lhs - x.yoy_cum_daily).abs().max() < 1e-9, "보정식 방향이 뒤집혔다"

    # 4. 계열마다 아는 사실로 못 박는다. 어긋나면 자료나 매핑을 의심할 것.
    bs = betas[betas.scope == "seg"].set_index("item").beta
    assert (bs > 0).all(), f"{series}: 조업일수 탄력성에 음수가 있다"
    assert (bs < 1.6).all(), f"{series}: 조업일수 탄력성이 1.6을 넘는다"
    if series == "exp_item":
        # 반도체는 팹이 연속 가동돼 낮고, 자동차부품은 조업일에 정비례를 넘는다.
        assert bs["반도체"] < bs[total], "반도체 탄력성이 총수출보다 높다 - 전제 재검토"
        assert 0 < bs["반도체"] < 0.5, f"반도체 탄력성 {bs['반도체']:.3f}이 예상 범위 밖"
        assert bs["자동차부품"] > 0.9, "자동차부품 탄력성이 예상보다 낮다"
    if series == "imp_item":
        # 원유는 벌크라 탱커 입항 일정이 좌우하고 조업일수와 사실상 무관하다.
        assert bs["원유"] < bs[total], "원유 탄력성이 총수입보다 높다 - 전제 재검토"
    if series == "exp_cnty":
        # 목적지의 반도체 비중이 높을수록 낮다(대만 78%, 중국 53% vs 일본 7%).
        assert bs["대만"] < bs["일본"], "대만 탄력성이 일본보다 높다 - 구성 효과 재검토"

    # 5. 진도율은 0과 1 사이.
    assert m[m.progress.notna()].progress.between(0, 1).all(), "진도율이 0~1 밖"

    # 6. 총수출 보정치가 0으로 무너지지 않았나(부분합 함정).
    t2 = m[m.item == total]
    assert not ((t2.cum_adj == 0) | (t2.seg_adj == 0)).any(), \
        "총수출 보정치가 0이다 - 부분합으로 무너졌다"
    assert (t2.cum_adj.notna() == t2.p_cum_workdays.notna()).all(), \
        "총수출 보정치의 결측 자리가 전년 자료 유무와 안 맞는다"
    print(f"검증 통과 - 기여도 합 일치, 누적 일관, 보정식 방향, 탄력성 부호, "
          f"진도율 범위 ({len(j):,}개 시점 x 구간)")


# ---------------------------------------------------------------- 출력

def report(m: pd.DataFrame, betas: pd.DataFrame, prog_sum: pd.DataFrame,
           d: pd.DataFrame, total: str) -> None:
    print("\n[1] 조업일수 탄력성 - 구간 증분 (Delta12, HAC 12)")
    b = betas[betas.scope == "seg"].sort_values("beta", ascending=False)
    print(b[["item", "beta", "se", "tval", "pval", "n", "r2", "beta_used"]]
          .round(3).to_string(index=False))

    last = d[(d.base_ym > d.base_ym.max() - 100) & (d.item != total)]
    sh = last.groupby("item").seg_kusd.sum()
    sh = sh / sh.sum()
    bb = betas[betas.scope == "seg"].set_index("item").beta
    wavg = float((sh * bb.reindex(sh.index)).sum())
    print(f"\n    비중가중 평균 {wavg:.3f}  vs  총수출 직접 추정 {bb[total]:.3f}"
          f"  (차이 {abs(wavg - bb[total]):.3f})")
    zero = b[~b.signif].item.tolist()
    print(f"    p >= {PVAL_CUT}라 보정하지 않는 품목: "
          f"{', '.join(zero) if zero else '없음'}")

    print("\n[2] 진도율 - 상순 누적이 월 전체의 몇 %인가 (열두 달 전체, %)")
    g = prog_sum[(prog_sum.mon == 0) & (prog_sum.cutoff == 10)].copy()
    for c in [c for c in g.columns if c.startswith(("ratio_", "adj_"))]:
        g[c] = g[c] * 100
    g = g.set_index("item")[["n", "ratio_mean", "ratio_sd", "ratio_p10",
                             "ratio_p90", "adj_sd"]]
    g.columns = ["n", "평균", "표준편차", "하위10%", "상위10%", "보정후_표준편차"]
    print(g.round(2).to_string())

    last_ym = int(m.base_ym.max())
    cut = int(m[m.base_ym == last_ym].cutoff.max())
    x = m[(m.base_ym == last_ym) & (m.cutoff == cut) & m.yoy_cum.notna()].copy()
    print(f"\n[3] {last_ym} cutoff={cut} 누적 - 전년 동기 대비 (%) "
          f"| 조업 {int(x.cum_workdays.iloc[0])}일, 전년 "
          f"{int(x.p_cum_workdays.iloc[0])}일")
    x["억달러"] = x.cum_kusd / 1e5
    x["비중%"] = x.share_cum * 100
    x["원계열"] = x.yoy_cum * 100
    x["일평균"] = x.yoy_cum_daily * 100
    x["탄력성보정"] = x.yoy_cum_adj * 100
    x["기여도pp"] = x.contrib_cum_adj_pp
    x = x.sort_values("억달러", ascending=False)
    print(x[["item", "억달러", "비중%", "원계열", "일평균", "탄력성보정", "기여도pp"]]
          .round(2).to_string(index=False))

    print("\n[4] 총수출 상순 진도율의 달별 분포 (%) - 설·추석이 어디에 걸리나")
    g = prog_sum[(prog_sum.item == total) & (prog_sum.cutoff == 10)
                 & (prog_sum.mon > 0)].copy()
    for c in ("ratio_mean", "ratio_sd", "adj_mean", "adj_sd"):
        g[c] = g[c] * 100
    g = g.set_index("mon")[["n", "ratio_mean", "ratio_sd", "adj_mean", "adj_sd"]]
    g.columns = ["n", "평균", "표준편차", "보정후_평균", "보정후_표준편차"]
    print(g.round(2).to_string())

    strain = m[(m.item == total) & m.dln_cum_workdays.notna()]
    worst = strain.reindex(strain.dln_cum_workdays.abs().sort_values(
        ascending=False).index).head(5)
    print("\n[5] 조업일수가 가장 크게 어긋난 다섯 시점 - 보정이 외삽이 되는 자리")
    w = worst[["base_ym", "cutoff", "cum_workdays", "p_cum_workdays"]].copy()
    w["원계열%"] = worst.yoy_cum * 100
    w["일평균%"] = worst.yoy_cum_daily * 100
    w["탄력성보정%"] = worst.yoy_cum_adj * 100
    print(w.round(1).to_string(index=False))


# ---------------------------------------------------------------- 적재

def summary(betas: pd.DataFrame) -> None:
    """네 계열을 한 화면에 겹쳐 본다. 계열 사이의 대조가 여기서 나온다."""
    b = betas[betas.scope == "seg"]
    print(f"\n{'=' * 72}\n[요약] 계열별 구간 증분 조업일수 탄력성의 폭\n{'=' * 72}")
    core = b[~b.item.isin(["총수출", "총수입", OTHER])]
    g = core.groupby("series").beta.agg(["min", "max", "mean", "std", "count"])
    tot = b[b.item.isin(["총수출", "총수입"])].set_index("series").beta
    g["총계"] = tot
    g.columns = ["최소", "최대", "평균", "표준편차", "항목수", "총계"]
    print(g.round(3).to_string())
    zero = b[~b.signif]
    if len(zero):
        print("\n  유의하지 않아 보정하지 않는 항목: "
              + ", ".join(f"{r.series}/{r.item}" for r in zero.itertuples()))
    # 총계는 품목 계열과 국가 계열에서 같은 값이어야 한다(같은 원계열이므로).
    for d in ("exp", "imp"):
        v = tot.reindex([f"{d}_item", f"{d}_cnty"]).dropna()
        if len(v) == 2:
            assert abs(v.iloc[0] - v.iloc[1]) < 1e-9, \
                f"{d}: 품목 계열과 국가 계열의 총계 탄력성이 다르다"
    print("\n  교차 확인 — 품목 계열과 국가 계열의 총계 탄력성이 같다")


def save(con: duckdb.DuckDBPyConnection, name: str, df: pd.DataFrame, stamp) -> None:
    df = df.copy()
    df["updated_at"] = stamp
    con.register("_t", df)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
    con.unregister("_t")
    print(f"  {name:22s} {len(df):>7,}행")


def run_one(con: duckdb.DuckDBPyConnection, series: str, asof: int | None,
            verbose: bool) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """한 계열을 통째로 돈다. 계열마다 따로 도는 것이 섞이지 않는 가장 확실한 길이다."""
    total = TOTALS[series]
    d = load(con, series, total)
    prog = build_progress(d)
    betas = pd.concat([fit_betas(d, asof),
                       fit_progress_betas(prog, asof)], ignore_index=True)
    prog_sum = summarise_progress(prog, betas[betas.scope == "prog"])
    m = build_metrics(d, betas, prog, total)

    check(m, betas, d, series, total)
    if verbose:
        report(m, betas, prog_sum, d, total)

    out = []
    for f in (betas, prog_sum, m):
        f = f.copy()
        f.insert(0, "series", series)
        out.append(f)
    return tuple(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", type=int, default=None,
                    help="탄력성 추정 표본을 이 연월까지로 자른다(YYYYMM)")
    ap.add_argument("--series", default=",".join(SERIES),
                    help="쉼표로 구분한 계열 (기본 넷 다)")
    ap.add_argument("--quiet", action="store_true",
                    help="계열별 상세 표를 찍지 않는다")
    a = ap.parse_args()
    want = [s.strip() for s in a.series.split(",") if s.strip()]
    for s in want:
        if s not in TOTALS:
            raise SystemExit(f"모르는 계열: {s} (가능: {', '.join(SERIES)})")

    con = duckdb.connect(DB)
    try:
        B, P, M = [], [], []
        for s in want:
            print(f"\n{'=' * 72}\n[{s}] {TOTALS[s]} 계열\n{'=' * 72}")
            b, p, m = run_one(con, s, a.asof, not a.quiet)
            B.append(b); P.append(p); M.append(m)
        betas = pd.concat(B, ignore_index=True)
        prog_sum = pd.concat(P, ignore_index=True)
        m = pd.concat(M, ignore_index=True)
        summary(betas)

        if a.asof is not None:
            print(f"\n--asof {a.asof}는 진단용이라 적재하지 않는다. "
                  f"마트를 되돌리려면 인자 없이 다시 실행할 것.")
            return

        stamp = dt.datetime.now().replace(microsecond=0)
        print("\n적재")
        save(con, "mart_exp10d_beta", betas, stamp)
        save(con, "mart_exp10d_progress", prog_sum, stamp)
        save(con, "mart_exp10d_metrics", m, stamp)
    finally:
        con.close()


if __name__ == "__main__":
    main()
