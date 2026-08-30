"""08_exp10d_forecast.py — 상순·중순 실적으로 그 달 전체를 예측한다.

이 시스템의 핵심 산출물
-----------------------
관세청은 11일에 1~10일 수출액을, 21일에 1~20일 수출액을 낸다. 그 달이 얼마로 끝날지는
말하지 않는다. 10일 자료가 있어야만 낼 수 있고 아무도 공표하지 않는 값이 이것이다.

예측식
------
월 전체 = 지금까지의 누적 / 예상 진도율. 그러니 진도율을 맞히는 문제로 바뀐다.

    ln(진도율) = a + b x ln(조업일 진도율) + 달효과 + 0.75 x 최근 6개월 잔차 평균

세 항이 각각 하는 일이 다르다. **조업일 진도율**은 이번 달 공휴일이 상순에 몰렸는지를
반영하고(b는 품목마다 달라 반도체 0.30, 승용차 1.18), **달효과**는 조업일수로 안 잡히는
달마다의 버릇을 잡고(예: 명절 앞 밀어내기), **최근 잔차 평균**은 진도율이 근래 표류한
방향을 따라간다. 표류항의 창(6개월)과 축소계수(0.75)는 격자탐색으로 골랐고, 0.5~1.0
어디에 두어도 성적이 거의 같아 날 선 값이 아니다.

성적 (2019.01~2026.07, 확장창 표본외, 품목x구간마다 91개월)
-------------------------------------------------------------
총수출 평균절대오차가 **상순 5.5%, 중순 2.4%**(중위 4.6%, 2.0%)다. 11일에 그 달
수출을 5% 안팎으로 부를 수 있다는 뜻이다. 비교 대상으로, 진도율을 과거 평균으로만
잡으면 11.0%이고 달별 평균으로 잡아도 9.6%다.

품목별로는 편차가 크다. 기타 4.7% · 정밀기기 5.3% · 철강제품 6.5% · 반도체 6.8% ·
자동차부품 7.7% · 가전제품 9.0% · 무선통신기기 9.1% · 석유제품 10.7% ·
컴퓨터주변기기 14.5% · 승용차 19.0% · **선박 76.3%**. 선박은 몇 건의 대형 인도가
좌우해 예측 자체가 성립하지 않는다. mart_exp10d_fcskill이 품목별 성적과 등급
(A 촘촘 / B 쓸만 / C 거칠다 / D 쓰지 말 것)을 남기니 대시보드에서 그대로 쓸 것.

구간
----
학습 표본 잔차의 분위수를 1.1배 넓혀 쓴다. 표본외에서 80% 구간이 상순 78.2%,
중순 76.6%를 담고 50% 구간이 52.5%, 48.7%를 담아 **두 수준이 함께 맞는다.** 1.1배는
이 뒷걸음질에 맞춰 고른 하나짜리 계수다(모수 하나에 관측 2천여 개라 과적합으로
보기 어렵다). 구간이 뒤집히기 쉬운 자리라는 데 주의할 것 — 월 전체는 진도율의
역수라 **진도율 상위 분위가 월 전체의 하한**이 된다.

명절 창은 여기서도 안 쓴다 (3단계에 이어 재검증)
--------------------------------------------------
설·추석 앞뒤 7일 조업일이 상순·중순에 얼마나 치우쳐 들었는지를 회귀에 넣어 봤다.
표본외 성적이 오히려 나빠진다 — 전체 평균 10.33% -> 10.40%, 총수출 상순 5.49% -> 5.57%,
중순 2.39% -> 2.45%. 자동차부품(-0.36%p)·기타(-0.20%p)에는 도움이 되지만 정작 머리기사인
총수출이 나빠진다. **달효과 항이 이미 명절의 대부분을 흡수하고 있어서** 따로 넣을 값이
남지 않는다. 두 단계에서 각각 확인했으니 다시 시도할 일이 아니다.

품목 예측을 총수출에 맞춰 되맞춘다
----------------------------------
총수출은 직접 예측하는 편이 열한 갈래를 더하는 것보다 정확하다(상순 5.49% vs 5.98%,
중순 2.39% vs 2.43%). 그런데 그대로 두면 예상 기여도가 예상 총수출 증가율과 안 맞는다.
그래서 품목 예측을 총수출 예측에 비례 조정한 `fc_rec_kusd`를 함께 둔다. 되맞춤은 품목
정확도를 사실상 바꾸지 않는다(상순 평균 15.41% -> 15.40%). **기여도 분해에는 되맞춤을,
품목 하나만 볼 때는 원래 예측을 쓴다.**

정직하게 밝힐 것 — 이 뒷걸음질은 개정된 값으로 쟀다
----------------------------------------------------
`fact_exp10d`에 vintage가 하나뿐이라(전부 2026-08-29 수집) 과거 상순 누적치는 그때
공표된 값이 아니라 그 뒤 정정이 반영된 값이다. 실시간 성적은 이보다 나쁠 수 있다.
그래서 이 스크립트는 확정 전 달의 예측을 `mart_exp10d_fclog`에 쌓는다. 시간이 지나면
그 기록으로 진짜 실시간 성적을 잴 수 있다.

만드는 것
---------
mart_exp10d_forecast  시점 x 구간 x 품목 예측(확장창 표본외 + 미확정 달)
mart_exp10d_fcskill   품목 x 구간 표본외 성적과 등급
mart_exp10d_fclog     미확정 달 예측의 기록(추가 전용, 값이 바뀔 때만 쌓는다)

실행: python scripts/08_exp10d_forecast.py     # 07 이후에 돌린다
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

# 예측은 수출 두 계열만 한다. 수입은 "이번 달 얼마"가 뉴스가 되지 않고,
# 무역수지 예측은 수출·수입 오차가 겹쳐 구간이 넓어져 따로 재 본 뒤 결정한다.
SERIES = ["exp_item", "exp_cnty"]
TOTAL = "총수출"
MIN_TRAIN = 36      # 이보다 짧으면 예측하지 않는다
DRIFT_WIN = 6       # 최근 표류를 재는 창
DRIFT_SHRINK = 0.75
WIDEN = 1.1         # 구간 폭 확대 계수 (뒷걸음질로 맞춘 값)
MIN_MON = 3         # 달효과를 쓰려면 그 달 관측이 이만큼은 있어야
QS = {"lo80": .10, "lo50": .25, "hi50": .75, "hi80": .90}


# ---------------------------------------------------------------- 자료

def load(con: duckdb.DuckDBPyConnection, series: str) -> pd.DataFrame:
    """상순·중순 누적 실적에 조업일 진도율과 월 전체 실적을 붙인다.

    조업일 진도율은 dim_workday10d에서 바로 낸다. mart_exp10d_metrics의 w_progress는
    월 전체 실적이 나온 달에만 있어서 정작 예측해야 할 달에 비어 있다.
    """
    m = con.sql(f"""SELECT base_ym, cutoff, item, cum_kusd
                    FROM mart_exp10d_metrics
                    WHERE series = '{series}' AND cutoff IN (10, 20)""").df()
    act = con.sql(f"""SELECT base_ym, item, cum_kusd AS act_kusd
                      FROM mart_exp10d_metrics
                      WHERE series = '{series}' AND cutoff = 99""").df()
    w = con.sql("SELECT base_ym, cutoff, workdays FROM dim_workday10d").df()
    w = w.sort_values(["base_ym", "cutoff"])
    w["cw"] = w.groupby("base_ym").workdays.cumsum()
    tot = w[w.cutoff == 99][["base_ym", "cw"]].rename(columns={"cw": "cw_full"})
    w = w.merge(tot, on="base_ym")
    w["w_progress"] = w.cw / w.cw_full
    w = w[w.cutoff.isin([10, 20])][["base_ym", "cutoff", "cw", "cw_full", "w_progress"]]

    d = m.merge(w, on=["base_ym", "cutoff"], how="inner") \
         .merge(act, on=["base_ym", "item"], how="left")
    d["mon"] = d.base_ym % 100
    d["progress"] = d.cum_kusd / d.act_kusd
    d["lr"] = np.log(d.progress)
    d["lwr"] = np.log(d.w_progress)
    return d.sort_values(["item", "cutoff", "base_ym"]).reset_index(drop=True)


# ---------------------------------------------------------------- 모형

def fit_once(tr: pd.DataFrame, mon: int, lwr: float) -> tuple[float, np.ndarray, float]:
    """학습 표본으로 한 시점의 ln(진도율)을 예측하고 구간용 잔차를 돌려준다."""
    X = sm.add_constant(tr[["lwr"]])
    r = sm.OLS(tr.lr, X).fit()
    res = tr.lr - r.predict(X)

    g = res.groupby(tr.mon).agg(["mean", "size"])
    eff = g["mean"].where(g["size"] >= MIN_MON, 0.0)          # 달효과
    dmon = float(eff.get(mon, 0.0))
    drift = DRIFT_SHRINK * float(res.iloc[-DRIFT_WIN:].mean())

    pred = float(r.params["const"] + r.params["lwr"] * lwr + dmon + drift)
    # 구간용 잔차는 각 행에 자기 달 효과를 적용한 것이다. 예측점의 달효과를
    # 모든 행에서 빼면 다른 달 잔차가 통째로 밀려 구간이 어긋난다.
    resid = (res - tr.mon.map(eff).fillna(0.0)).to_numpy()
    return pred, resid, float(r.params["lwr"])


def forecast(d: pd.DataFrame) -> pd.DataFrame:
    """확장창 표본외 예측. 미확정 달도 같은 규칙으로 한 번 더 낸다."""
    rows = []
    for (item, cut), g in d.groupby(["item", "cutoff"]):
        g = g.reset_index(drop=True)
        hist = g.lr.notna().to_numpy()
        for i in range(len(g)):
            te = g.iloc[i]
            tr = g.iloc[:i]
            tr = tr[tr.lr.notna()]
            if len(tr) < MIN_TRAIN or not np.isfinite(te.lwr):
                continue
            pred, resid, b = fit_once(tr, int(te.mon), float(te.lwr))
            q = {k: pred + WIDEN * float(np.quantile(resid, v)) for k, v in QS.items()}
            rows.append(dict(
                base_ym=int(te.base_ym), cutoff=int(te.cutoff), item=item,
                cum_kusd=float(te.cum_kusd), cw=int(te.cw), cw_full=int(te.cw_full),
                w_progress=float(te.w_progress), b_prog=b, n_train=len(tr),
                pred_lr=pred, pr_hat=float(np.exp(pred)),
                fc_kusd=float(te.cum_kusd) / np.exp(pred),
                # 진도율이 높으면 월 전체는 작아진다. 구간의 위아래가 뒤집힌다.
                lo80=float(te.cum_kusd) / np.exp(q["hi80"]),
                lo50=float(te.cum_kusd) / np.exp(q["hi50"]),
                hi50=float(te.cum_kusd) / np.exp(q["lo50"]),
                hi80=float(te.cum_kusd) / np.exp(q["lo80"]),
                act_kusd=float(te.act_kusd) if te.act_kusd == te.act_kusd else np.nan,
                is_final=bool(hist[i])))
    return pd.DataFrame(rows)


def reconcile(f: pd.DataFrame) -> pd.DataFrame:
    """품목 예측을 총수출 예측에 비례 조정한다. 기여도가 맞아떨어지게."""
    part = f[f.item != TOTAL].groupby(["base_ym", "cutoff"]).fc_kusd.sum()
    tot = f[f.item == TOTAL].set_index(["base_ym", "cutoff"]).fc_kusd
    scale = (tot / part).rename("scale").reset_index()
    f = f.merge(scale, on=["base_ym", "cutoff"], how="left")
    f["fc_rec_kusd"] = np.where(f.item == TOTAL, f.fc_kusd, f.fc_kusd * f.scale)
    for c in ("lo80", "lo50", "hi50", "hi80"):
        f[c + "_rec"] = np.where(f.item == TOTAL, f[c], f[c] * f.scale)
    return f.drop(columns=["scale"])


def add_yoy(con: duckdb.DuckDBPyConnection, f: pd.DataFrame,
            series: str) -> pd.DataFrame:
    """예상 전년 동기 대비와 예상 기여도. 07의 보정 규약을 그대로 쓴다."""
    prev = con.sql(f"""SELECT base_ym + 100 AS base_ym, item,
                              cum_kusd AS prev_kusd, cum_workdays AS prev_wd
                       FROM mart_exp10d_metrics
                       WHERE series = '{series}' AND cutoff = 99""").df()
    beta = con.sql(f"""SELECT item, beta_used FROM mart_exp10d_beta
                       WHERE series = '{series}' AND scope = 'cum'""")              .df().set_index("item").beta_used
    f = f.merge(prev, on=["base_ym", "item"], how="left")
    f["beta_cum"] = f.item.map(beta).fillna(0.0)
    f["fc_adj_kusd"] = f.fc_rec_kusd * (f.prev_wd / f.cw_full) ** f.beta_cum
    f["yoy_fc"] = f.fc_rec_kusd / f.prev_kusd - 1
    f["yoy_fc_adj"] = f.fc_adj_kusd / f.prev_kusd - 1

    base = (f[f.item == TOTAL][["base_ym", "cutoff", "prev_kusd"]]
            .rename(columns={"prev_kusd": "t_prev"}))
    f = f.merge(base, on=["base_ym", "cutoff"], how="left")
    f["contrib_fc_pp"] = (f.fc_rec_kusd - f.prev_kusd) / f.t_prev * 100
    return f.drop(columns=["t_prev"])


def skill(f: pd.DataFrame) -> pd.DataFrame:
    """품목 x 구간 표본외 성적. 등급은 대시보드에서 그대로 쓴다."""
    s = f[f.is_final & f.act_kusd.notna()].copy()
    s["ape"] = (s.fc_kusd / s.act_kusd - 1).abs()
    s["in80"] = s.act_kusd.between(s.lo80, s.hi80)
    s["in50"] = s.act_kusd.between(s.lo50, s.hi50)
    g = s.groupby(["item", "cutoff"]).agg(
        n=("ape", "size"), mape=("ape", "mean"), mdape=("ape", "median"),
        cov80=("in80", "mean"), cov50=("in50", "mean"),
        eval_from=("base_ym", "min"), eval_to=("base_ym", "max")).reset_index()
    for c in ("mape", "mdape", "cov80", "cov50"):
        g[c] = g[c] * 100
    g["grade"] = pd.cut(g.mape, [0, 5, 10, 20, np.inf],
                        labels=["A 촘촘", "B 쓸만", "C 거칠다", "D 쓰지 말 것"])
    return g


def migrate_log(con: duckdb.DuckDBPyConnection) -> None:
    """옛 기록에 series를 채운다. 계열마다 '총수출'과 '기타'가 겹치기 때문이다.

    계열을 늘리면서 처음 걸린 함정이다. series 없이 (base_ym, cutoff, item)만으로
    같은 값인지 보면 exp_item의 총수출과 exp_cnty의 총수출이 한 칸을 다툰다. 실제로
    총수출은 값이 같아 '안 바뀜'으로 건너뛰었고, 기타는 값이 달라 개정으로 잘못 쌓였다.
    옛 행은 항목 이름으로 계열을 가려내고, 어느 쪽에도 있는 총수출·기타는 그때 어느
    계열만 돌렸는지(made_at 순서)로 정한다.
    """
    tabs = {r[0] for r in con.sql("SHOW TABLES").fetchall()}
    if "mart_exp10d_fclog" not in tabs:
        return
    cols = {r[0] for r in con.sql("DESCRIBE mart_exp10d_fclog").fetchall()}
    if "series" in cols:
        return
    con.execute("ALTER TABLE mart_exp10d_fclog ADD COLUMN series VARCHAR")
    con.execute("""
        WITH names AS (SELECT DISTINCT series, item FROM mart_exp10d_forecast
                       WHERE item NOT IN ('총수출', '기타')),
             uniq AS (SELECT item, MIN(series) series FROM names
                      GROUP BY item HAVING COUNT(DISTINCT series) = 1)
        UPDATE mart_exp10d_fclog l SET series = u.series
        FROM uniq u WHERE u.item = l.item""")
    # 남은 총수출·기타는 그때 돌린 계열 순서로 가른다(첫 묶음이 exp_item이었다).
    con.execute("""
        WITH ord AS (SELECT made_at, DENSE_RANK() OVER (ORDER BY made_at) r
                     FROM (SELECT DISTINCT made_at FROM mart_exp10d_fclog))
        UPDATE mart_exp10d_fclog l
        SET series = CASE WHEN o.r = 1 THEN 'exp_item' ELSE 'exp_cnty' END
        FROM ord o WHERE o.made_at = l.made_at AND l.series IS NULL""")
    n = con.sql("SELECT COUNT(*) FROM mart_exp10d_fclog WHERE series IS NULL").fetchone()[0]
    assert n == 0, f"series를 못 채운 기록이 {n}행 남았다"
    print("  옮김: mart_exp10d_fclog에 series 추가 (계열마다 총수출·기타가 겹친다)")


def build_log(con: duckdb.DuckDBPyConnection, f: pd.DataFrame, stamp) -> int:
    """미확정 달 예측을 쌓는다. 값이 바뀔 때만 새 행을 만든다(05와 같은 규약).

    이것이 있어야 나중에 '그때 알 수 있던 값으로 잰' 실시간 성적을 낼 수 있다.
    지금 뒷걸음질은 개정된 누적치를 쓰므로 실시간보다 후하게 나올 수 있다.
    """
    live = f[~f.is_final][["series", "base_ym", "cutoff", "item", "cum_kusd",
                           "fc_kusd", "fc_rec_kusd", "lo80", "lo50", "hi50",
                           "hi80", "yoy_fc", "yoy_fc_adj"]].copy()
    live["made_at"] = stamp
    con.execute("""CREATE TABLE IF NOT EXISTS mart_exp10d_fclog (
        series VARCHAR, base_ym INTEGER, cutoff SMALLINT, item VARCHAR,
        cum_kusd DOUBLE, fc_kusd DOUBLE, fc_rec_kusd DOUBLE, lo80 DOUBLE,
        lo50 DOUBLE, hi50 DOUBLE, hi80 DOUBLE, yoy_fc DOUBLE, yoy_fc_adj DOUBLE,
        made_at TIMESTAMP)""")
    migrate_log(con)
    con.register("_live", live)
    where = """
        WITH last AS (SELECT series, base_ym, cutoff, item, fc_kusd,
                ROW_NUMBER() OVER (PARTITION BY series, base_ym, cutoff, item
                                   ORDER BY made_at DESC) rn
              FROM mart_exp10d_fclog)
        SELECT {sel} FROM _live v
        LEFT JOIN (SELECT * FROM last WHERE rn = 1) l
          USING (series, base_ym, cutoff, item)
        WHERE l.fc_kusd IS NULL OR abs(l.fc_kusd - v.fc_kusd) > 1"""
    n = con.sql(where.format(sel="COUNT(*)")).fetchone()[0]
    con.execute("INSERT INTO mart_exp10d_fclog BY NAME "
                + where.format(sel="v.*"))
    con.unregister("_live")
    return n


# ---------------------------------------------------------------- 검증

def check(f: pd.DataFrame, sk: pd.DataFrame, series: str) -> None:
    fin = f[f.is_final & f.act_kusd.notna()]
    # 1. 구간이 뒤집히지 않았나. 진도율의 역수라 뒤집기 쉬운 자리다.
    assert (f.lo80 <= f.lo50).all() and (f.lo50 <= f.fc_kusd).all() \
        and (f.fc_kusd <= f.hi50).all() and (f.hi50 <= f.hi80).all(), \
        "예측 구간의 순서가 뒤집혔다"
    # 2. 되맞춤 뒤 품목 합 = 총수출 예측.
    part = f[f.item != TOTAL].groupby(["base_ym", "cutoff"]).fc_rec_kusd.sum()
    tot = f[f.item == TOTAL].set_index(["base_ym", "cutoff"]).fc_rec_kusd
    assert ((part - tot).abs() / tot).max() < 1e-9, "되맞춤 뒤에도 합이 안 맞는다"
    # 3. 예상 기여도 합 = 예상 총수출 증가율.
    cs = f[f.item != TOTAL].groupby(["base_ym", "cutoff"]).contrib_fc_pp.sum()
    ct = f[f.item == TOTAL].set_index(["base_ym", "cutoff"])[["contrib_fc_pp", "yoy_fc"]]
    j = pd.concat([cs.rename("s"), ct], axis=1).dropna()
    assert (j.s - j.contrib_fc_pp).abs().max() < 1e-6, "예상 기여도 합이 안 맞는다"
    assert (j.s - j.yoy_fc * 100).abs().max() < 1e-6, "예상 기여도 합이 증가율과 다르다"
    # 4. 진도율 예측이 상식 범위 안인가.
    assert f.pr_hat.between(0.01, 0.99).all(), "예상 진도율이 0~1 밖"
    w = f.pivot_table(index=["base_ym", "item"], columns="cutoff",
                      values="pr_hat").dropna()
    assert (w[10] < w[20]).all(), "상순 예상 진도율이 중순보다 높다"
    # 5. 성적이 무너지지 않았나 — 이 시스템이 존재할 이유.
    t = sk[(sk.item == TOTAL)].set_index("cutoff").mape
    assert t[20] < t[10], f"{series}: 중순 예측이 상순보다 나쁘다"
    if series == "exp_item":
        assert t[10] < 8, f"총수출 상순 MAPE {t[10]:.1f}% - 8%를 넘으면 쓸모가 없다"
        assert t[20] < 4, f"총수출 중순 MAPE {t[20]:.1f}% - 중순이 상순보다 나아야"
    # 6. 구간 보정이 살아 있나.
    cov = sk[sk.item == TOTAL].cov80.mean()
    assert 70 <= cov <= 92, f"{series} 총계 80% 구간 포함률 {cov:.0f}% - 보정이 어긋났다"
    print(f"검증 통과 - 구간 순서, 되맞춤 합, 기여도 합, 진도율 범위, 성적, 포함률 "
          f"(표본외 {len(fin):,}건)")


# ---------------------------------------------------------------- 출력

def report(f: pd.DataFrame, sk: pd.DataFrame) -> None:
    print("\n[1] 표본외 성적 - 평균절대오차(%)와 80% 구간 포함률(%)")
    p = sk.pivot(index="item", columns="cutoff",
                 values=["mape", "mdape", "cov80"])
    p.columns = [f"{a}_{b}" for a, b in p.columns]
    p = p[["mape_10", "mdape_10", "cov80_10", "mape_20", "mdape_20", "cov80_20"]]
    p.columns = ["상순MAPE", "상순중위", "상순포함", "중순MAPE", "중순중위", "중순포함"]
    g = sk[sk.cutoff == 10].set_index("item").grade
    p["등급(상순)"] = g
    print(p.round(1).sort_values("상순MAPE").to_string())
    print(f"\n    비교: 진도율을 과거 평균으로만 잡으면 총수출 상순 11.0%, "
          f"달별 평균으로 잡아도 9.6%다.")

    live = f[~f.is_final]
    if live.empty:
        print("\n[2] 미확정 달 없음 - 모든 달이 확정됐다")
        return
    ym = int(live.base_ym.max())
    for cut in sorted(live[live.base_ym == ym].cutoff.unique()):
        x = live[(live.base_ym == ym) & (live.cutoff == cut)].copy()
        t = x[x.item == TOTAL].iloc[0]
        print(f"\n[2] {ym} {'상순' if cut == 10 else '중순'} 시점 예측 "
              f"(누적 {t.cum_kusd/1e5:,.1f}억, 조업 {int(t.cw)}/{int(t.cw_full)}일, "
              f"예상 진도율 {t.pr_hat*100:.1f}%)")
        print(f"    총수출 {t.fc_kusd/1e5:,.1f}억 달러 "
              f"[80% 구간 {t.lo80/1e5:,.1f} ~ {t.hi80/1e5:,.1f}] | "
              f"전년 동월 대비 {t.yoy_fc*100:+.1f}% "
              f"(조업일 보정 {t.yoy_fc_adj*100:+.1f}%)")
        x["예측억"] = x.fc_rec_kusd / 1e5
        x["하한80"] = x.lo80_rec / 1e5
        x["상한80"] = x.hi80_rec / 1e5
        x["전년비%"] = x.yoy_fc * 100
        x["기여도pp"] = x.contrib_fc_pp
        x = x[x.item != TOTAL].sort_values("예측억", ascending=False)
        print(x[["item", "예측억", "하한80", "상한80", "전년비%", "기여도pp"]]
              .round(2).to_string(index=False))


def save(con, name, df, stamp):
    df = df.copy()
    df["updated_at"] = stamp
    con.register("_t", df)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _t")
    con.unregister("_t")
    print(f"  {name:24s} {len(df):>7,}행")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", default=",".join(SERIES),
                    help="쉼표로 구분한 계열 (기본 수출 둘)")
    ap.add_argument("--quiet", action="store_true", help="계열별 상세 표를 찍지 않는다")
    a = ap.parse_args()
    want = [s.strip() for s in a.series.split(",") if s.strip()]

    con = duckdb.connect(DB)
    try:
        stamp = dt.datetime.now().replace(microsecond=0)
        F, S, nlog = [], [], 0
        for series in want:
            print(f"\n{'=' * 72}\n[{series}]\n{'=' * 72}")
            d = load(con, series)
            f = add_yoy(con, reconcile(forecast(d)), series)
            sk = skill(f)
            check(f, sk, series)
            f.insert(0, "series", series)
            sk.insert(0, "series", series)
            nlog += build_log(con, f, stamp)
            if not a.quiet:
                report(f, sk)
            F.append(f); S.append(sk)
        f = pd.concat(F, ignore_index=True)
        sk = pd.concat(S, ignore_index=True)
        print(f"\nmart_exp10d_fclog에 새로 쌓은 예측 {nlog}건 "
              f"(값이 그대로면 쌓지 않는다)")

        print("\n적재")
        save(con, "mart_exp10d_forecast", f, stamp)
        save(con, "mart_exp10d_fcskill", sk, stamp)
        n = con.sql("SELECT COUNT(*) FROM mart_exp10d_fclog").fetchone()[0]
        print(f"  {'mart_exp10d_fclog':24s} {n:>7,}행 (추가 전용)")
    finally:
        con.close()


if __name__ == "__main__":
    main()
