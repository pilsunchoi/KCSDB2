"""
03c_build_hs10_concordance.py — HS10(HSK) 개정 연계표 구축

설계 원칙 (docs/DB_구축_원칙.md §3.4):
- HS10 시계열 연결을 DB 계층에서 한 번 해결한다. fact는 건드리지 않는다.
- 공식 HS10 승계표는 존재하지 않는다. 연도별 별표 두 판을 맞대어 추정한다.
- 추정 절차와 그 한계를 weight·method 열로 드러낸다. 규칙을 감추지 않는다.

절차:
  1. 연도별 HSK 별표 전문 PDF에서 (HS10, 국문명, 영문명)과 계층 경로를 뽑는다.
  2. 개정 전후 두 판의 코드 집합을 비교해 존속·소멸·신설을 가른다.
  3. 허용 그래프를 만든다. 공식 HS6 연계표(dim_hs6_concordance)가 잇는 6자리 쌍만
     후보로 두고, 개정 전후에 모두 살아 있는 서로 다른 코드끼리는 이동을 막는다.
  4. 계층 경로 품명과 잎 품명의 유사도를 사전가중으로 삼아 이중비례조정(IPF)한다.
     행합은 개정 직전 6개월 수출액, 열합은 직후 6개월 수출액.
  5. 세 개정을 이어 곱해 과거 체계 → 2022 체계 표를 만들고, 별표가 못 덮는
     옛 코드는 HS6 해상도로 보완한다(method='hs6_fallback').

입력: data/external/HSK_별표/HSK_별표_{2011,2013,2015,2017,2021,2022}.pdf
      data/processed/kcsdb.duckdb 의 dim_hs6_concordance, fact_trade
출력: data/processed/kcsdb.duckdb 의 dim_hs10_concordance, dim_hs10_to_2022

실측 (2026-08-28):
  dim_hs10_concordance 50,899행 (2012 16,428 / 2017 16,709 / 2022 17,762)
  dim_hs10_to_2022     75,754행 (chain 66,997 / hs6_fallback 8,757)
  대각선 보존율(개정 / 위약): 2012 96.84/99.88, 2017 93.00/99.32, 2022 85.67/97.94

실행:
  python scripts\\03c_build_hs10_concordance.py
  python scripts\\03c_build_hs10_concordance.py --dry-run   # DB 미기록, 추출·검증만
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import duckdb
import fitz
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BYEOLPYO_DIR = PROJECT_ROOT / "data" / "external" / "HSK_별표"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

DB_PATH = PROCESSED_DIR / "kcsdb.duckdb"

LOG_PATH = LOG_DIR / f"build_hs10_concordance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# 개정: (개정 전 별표, 개정 후 별표, 시행 연월, HS6 연계표의 과거 판본)
REVISIONS = {
    "2012": ("2011", "2013", 201201, "2007"),
    "2017": ("2015", "2017", 201701, "2012"),
    "2022": ("2021", "2022", 202201, "2017"),
}
# 개정 사이의 소규모 국내 개정 구간. 양쪽에 다 있는 코드만 그대로 넘긴다.
BRIDGES = [("2013", "2015"), ("2017", "2021")]
# 과거 판본별로 그 체계가 쓰인 거래 기간
WINDOWS = {"2007": (200701, 201112), "2012": (201201, 201612), "2017": (201701, 202112)}

GAMMA = 8.0     # 품명 유사도를 사전가중으로 바꿀 때의 지수
W_LEAF = 0.6    # 점수에서 잎 품명이 차지하는 몫
FLOOR = 1e-6    # 허용 쌍의 최소 사전가중
ITERS = 400     # IPF 반복


# ─────────────────────────────────────────────────────────────────────────────
# 1. 별표 PDF 파싱
# ─────────────────────────────────────────────────────────────────────────────

def _is_hangul(s: str) -> bool:
    return any("가" <= ch <= "힣" for ch in s)


def column_anchors(doc, sample: int = 60) -> list[float]:
    """HS4·소호·8자리·10자리·국문·영문 여섯 열의 시작 x좌표.

    낱말의 성격(숫자 / 한글 / 로마자)으로 갈라 각 무리의 최빈 x를 쓴다. 영문 열을
    국문 열 안쪽 좌표와 헷갈리지 않게 하려는 것이다.
    """
    dig: Counter = Counter()
    ko: Counter = Counter()
    en: Counter = Counter()
    step = max(1, len(doc) // sample)
    for i in range(0, len(doc), step):
        for w in doc[i].get_text("words"):
            x, t = round(w[0], 1), w[4]
            if t.isdigit():
                dig[x] += 1
            elif _is_hangul(t):
                ko[x] += 1
            elif t.isascii() and any(ch.isalpha() for ch in t):
                en[x] += 1
    kx = ko.most_common(1)[0][0]
    ex = max((x for x, _ in en.most_common(8) if x > kx), key=lambda x: en[x])
    codes = sorted(x for x, _ in dig.most_common(20) if x < kx - 5)
    anchors: list[float] = []
    for x in codes:
        if not anchors or x - anchors[-1] > 8:
            anchors.append(x)
    if len(anchors) != 4:
        raise RuntimeError(f"코드 열 4개를 못 찾음: {anchors}")
    return anchors + [kx, ex]


def parse_page(page, anch: list[float]) -> list[dict]:
    c0, c1, c2, c3, kx, ex = anch
    tol = 3.0
    lines: dict[float, list[tuple[float, str]]] = {}
    for x0, y0, _x1, _y1, txt, *_ in page.get_text("words"):
        key = round(y0, 1)
        for k in lines:
            if abs(k - key) <= 1.5:
                key = k
                break
        lines.setdefault(key, []).append((x0, txt))

    # 코드 열은 판본마다 몇 pt씩 흔들린다. 고정 허용오차 대신 이웃 열의 중간점을
    # 경계로 삼아 가장 가까운 열에 배정한다.
    bnd = [(c0 + c1) / 2, (c1 + c2) / 2, (c2 + c3) / 2, kx - 4]
    recs: list[dict] = []
    cur: dict | None = None
    for y in sorted(lines):
        ws = sorted(lines[y])
        if any(c0 - 6 < x < bnd[0] for x, _ in ws):
            frag = ["", "", "", ""]
            for x, t in ws:
                if not t.isdigit() or x >= bnd[3] or x < c0 - 6:
                    continue
                frag[next(k for k in range(4) if x < bnd[k])] = t
            cur = {"frag": frag, "ko": [], "en": []}
            recs.append(cur)
        if cur is None:
            continue
        for x, t in ws:
            if kx - tol <= x < ex - tol:
                cur["ko"].append(t)
            elif x >= ex - tol:
                cur["en"].append(t)
    return recs


def read_byeolpyo(year: str) -> pd.DataFrame:
    """별표 PDF → HS10마다 (코드, 잎 품명, 계층 경로 품명, 영문명)."""
    doc = fitz.open(BYEOLPYO_DIR / f"HSK_별표_{year}.pdf")
    anch = column_anchors(doc)
    recs: list[dict] = []
    for page in doc:
        recs += parse_page(page, anch)

    stack: dict[int, tuple[str, str]] = {}
    rows = []
    for r in recs:
        # PDF에 찍힌 자릿수를 그대로 잇는다. HSK에는 7·9자리 중간 계층이 있어
        # 0을 채워 넣으면 '9'(9자리 표제)와 '09'(10자리)가 뒤섞인다.
        parts = []
        for f in r["frag"]:
            if not f:
                break
            parts.append(f)
        code = "".join(parts)
        if not code:
            continue
        ko = " ".join(r["ko"]).strip()
        n = len(code)
        stack = {k: v for k, v in stack.items() if k < n and code.startswith(v[0])}
        stack[n] = (code, ko)
        if n == 10:
            rows.append(
                {
                    "code": code,
                    "leaf": ko,
                    "path": " ".join(stack[k][1] for k in sorted(stack)),
                    "name_en": " ".join(r["en"]).strip(),
                }
            )
    df = pd.DataFrame(rows).drop_duplicates("code")
    logger.info(f"  별표 {year}: HS10 {len(df):,}개")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 2. 허용 그래프와 이중비례조정
# ─────────────────────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum())


def hs6_map(con, past_version: str) -> dict[str, set[str]]:
    df = con.execute(
        "SELECT hs2022, hs_past FROM dim_hs6_concordance WHERE past_version = ? "
        "AND hs2022 IS NOT NULL AND hs_past IS NOT NULL",
        [past_version],
    ).df()
    m: dict[str, set[str]] = {}
    for a, b in zip(df.hs2022, df.hs_past):
        m.setdefault(b, set()).add(a)
    return m


def licensed(A: pd.DataFrame, B: pd.DataFrame, tgt: dict[str, set[str]]) -> pd.DataFrame:
    """허용 쌍과 사전가중.

    개정 전후에 모두 살아 있는 서로 다른 두 코드 사이에는 이동을 허용하지 않는다.
    양쪽에 다 있는 코드는 개정이 건드리지 않은 것이므로 내용이 오갈 이유가 없고,
    이것을 열어 두면 조정 단계가 평상시 변동까지 이동으로 빨아들인다(디램과 플래시
    메모리 사이에 값이 오가는 식이다).
    """
    aset, bset = set(A.code), set(B.code)
    apath, aleaf = dict(zip(A.code, A.path)), dict(zip(A.code, A.leaf))
    bpath, bleaf = dict(zip(B.code, B.path)), dict(zip(B.code, B.leaf))
    b_by6: dict[str, list[str]] = {}
    for c in B.code:
        b_by6.setdefault(c[:6], []).append(c)

    rows: list[tuple[str, str, float]] = []
    orphan: list[str] = []
    for p in A.code:
        h6 = p[:6]
        heads = set(tgt.get(h6, set()))
        if h6 in b_by6:
            heads.add(h6)
        cands = [t for h in sorted(heads) for t in b_by6.get(h, [])]
        if not cands:
            orphan.append(p)
            continue
        sp, sl = _norm(apath.get(p, "")), _norm(aleaf.get(p, ""))
        p_alive = p in bset
        for t in cands:
            if p == t:
                rows.append((p, t, 1.0 + FLOOR))
                continue
            if p_alive and t in aset:
                continue
            # 경로만 보면 '자동차 휘발유'와 '나프타'가 같은 점수를 받는다(둘 다 같은
            # 호 밑이라 앞부분이 길게 겹친다). 잎 품명에 무게를 실어 가른다.
            s = W_LEAF * SequenceMatcher(None, sl, _norm(bleaf.get(t, ""))).ratio() + (
                1 - W_LEAF
            ) * SequenceMatcher(None, sp, _norm(bpath.get(t, ""))).ratio()
            rows.append((p, t, max(s, 0.0) ** GAMMA + FLOOR))
    if orphan:
        logger.info(f"  HS6 후보가 아예 없는 개정 전 코드 {len(orphan):,}개")
    return pd.DataFrame(rows, columns=["hs_from", "hs_to", "w0"])


def shift(ym: int, k: int) -> int:
    y, m = divmod(ym, 100)
    t = y * 12 + (m - 1) + k
    return (t // 12) * 100 + (t % 12) + 1


def exports(con, ym: int, back: int, fwd: int) -> tuple[pd.Series, pd.Series]:
    pre = [shift(ym, -i) for i in range(1, back + 1)]
    post = [shift(ym, i) for i in range(fwd)]

    def q(ms):
        return (
            con.execute(
                f"SELECT hs10, SUM(exp_dlr) v FROM fact_trade "
                f"WHERE yyyymm IN ({','.join(map(str, ms))}) GROUP BY 1"
            )
            .df()
            .set_index("hs10")
            .v.astype(float)
        )

    return q(pre), q(post)


def ipf(E: pd.DataFrame, ra: np.ndarray, cb: np.ndarray, r, c, npre, npost) -> np.ndarray:
    w = np.array(E.w0.to_numpy(dtype=float), copy=True)
    for _ in range(ITERS):
        rs = np.bincount(r, weights=w, minlength=npre)
        w *= np.where(rs[r] > 0, ra[r] / np.maximum(rs[r], 1e-300), 0.0)
        cs = np.bincount(c, weights=w, minlength=npost)
        w *= np.where(cs[c] > 0, cb[c] / np.maximum(cs[c], 1e-300), 0.0)
    return w


def build_revision(con, rev: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    y0, y1, ym, past = REVISIONS[rev]
    A, B = tables[y0], tables[y1]
    dead, born = set(A.code) - set(B.code), set(B.code) - set(A.code)
    logger.info(
        f"[{rev}] {y0}({len(A):,}) → {y1}({len(B):,}): "
        f"존속 {len(set(A.code) & set(B.code)):,} 소멸 {len(dead):,} 신설 {len(born):,}"
    )

    E = licensed(A, B, hs6_map(con, past))
    pre_codes = pd.Index(sorted(set(E.hs_from)))
    post_codes = pd.Index(sorted(set(E.hs_to)))
    r = pd.Series(np.arange(len(pre_codes)), index=pre_codes)[E.hs_from].to_numpy()
    c = pd.Series(np.arange(len(post_codes)), index=post_codes)[E.hs_to].to_numpy()

    va, vb = exports(con, ym, 6, 6)
    ra = va.reindex(pre_codes).fillna(0.0).to_numpy()
    cb = vb.reindex(post_codes).fillna(0.0).to_numpy()
    # 무거래 코드도 계열로는 살아 있어야 하므로 아주 작은 값을 준다.
    eps = max(ra.sum(), 1.0) * 1e-9
    ra, cb = ra + eps, cb + eps
    cb = cb * (ra.sum() / cb.sum())

    w = ipf(E, ra, cb, r, c, len(pre_codes), len(post_codes))
    same = E.hs_from.to_numpy() == E.hs_to.to_numpy()
    logger.info(f"  대각선 보존 {100 * w[same].sum() / w.sum():.2f}%")

    rs = np.bincount(r, weights=w, minlength=len(pre_codes))
    E = E.assign(weight=w / np.maximum(rs[r], 1e-300))
    E["score"] = (E.w0 - FLOOR) ** (1 / GAMMA)
    E = E[E.weight > 1e-4].copy()
    E["weight"] = E.weight / E.groupby("hs_from").weight.transform("sum")
    E["revision"] = rev
    E["relation"] = np.where(E.hs_from == E.hs_to, "identity", "moved")
    logger.info(f"  남긴 연결 {len(E):,}행 (이동 {int((E.relation == 'moved').sum()):,})")
    return E[["hs_from", "hs_to", "revision", "weight", "score", "relation"]]


# ─────────────────────────────────────────────────────────────────────────────
# 3. 연쇄 구성과 HS6 보완
# ─────────────────────────────────────────────────────────────────────────────

def compose(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    m = a.merge(b, left_on="hs_to", right_on="hs_from", suffixes=("_a", "_b"))
    m["weight"] = m.weight_a * m.weight_b
    return (
        m.groupby(["hs_from_a", "hs_to_b"], as_index=False)
        .weight.sum()
        .rename(columns={"hs_from_a": "hs_from", "hs_to_b": "hs_to"})
    )


def chain(parts: dict[str, pd.DataFrame], tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    def bridge(y0: str, y1: str) -> pd.DataFrame:
        keep = sorted(set(tables[y0].code) & set(tables[y1].code))
        logger.info(f"  틈 {y0}→{y1}: {keep and len(keep):,}개 그대로, "
                    f"{len(set(tables[y0].code)) - len(keep):,}개 흘림")
        return pd.DataFrame({"hs_from": keep, "hs_to": keep, "weight": 1.0})

    cols = ["hs_from", "hs_to", "weight"]
    m12, m17, m22 = (parts[k][cols] for k in ("2012", "2017", "2022"))
    b1, b2 = bridge(*BRIDGES[0]), bridge(*BRIDGES[1])
    chains = {
        "2017": m22,
        "2012": compose(compose(m17, b2), m22),
        "2007": compose(compose(compose(compose(m12, b1), m17), b2), m22),
    }
    out = []
    for ver, m in chains.items():
        m = m.copy()
        m["weight"] = m.weight / m.groupby("hs_from").weight.transform("sum")
        m["past_version"] = ver
        m["method"] = "chain"
        out.append(m.rename(columns={"hs_from": "hs_past", "hs_to": "hs2022"}))
    return pd.concat(out, ignore_index=True)[["hs_past", "past_version", "hs2022", "weight", "method"]]


def add_fallback(con, ch: pd.DataFrame) -> pd.DataFrame:
    """별표로 덮이지 않는 옛 코드를 HS6 수준으로 이어 붙인다.

    연쇄의 출발점은 2011년 별표다(국가법령정보센터에 그보다 옛 고시가 없다).
    2009년경 국내 개정으로 사라진 코드들은 2011년 별표에 없어 연쇄에 못 든다.
    이 코드들은 폐지되어 dim_hs10에 품명이 없으므로 품명 매칭을 쓸 수 없다.
    대신 공식 HS6 연계표가 허용하는 현행 6자리로 보내고 2022년 교역액으로 나눈다.
    """
    conc = con.execute(
        "SELECT hs2022, hs_past, past_version FROM dim_hs6_concordance "
        "WHERE hs2022 IS NOT NULL AND hs_past IS NOT NULL"
    ).df()
    # 수입만 있는 코드도 덮어야 하므로 여기서는 수출입 합계를 쓴다.
    val = (
        con.execute(
            "SELECT hs10, SUM(exp_dlr + imp_dlr) v FROM fact_trade "
            "WHERE yyyymm BETWEEN 202201 AND 202212 GROUP BY 1"
        )
        .df()
        .set_index("hs10")
        .v.to_dict()
    )
    by6: dict[str, list[str]] = {}
    for c in sorted(set(ch.hs2022)):
        by6.setdefault(c[:6], []).append(c)
    h6map: dict[tuple[str, str], set[str]] = {}
    for a, b, pv in zip(conc.hs2022, conc.hs_past, conc.past_version):
        h6map.setdefault((pv, b), set()).add(a)

    rows = []
    for pv, (a, b) in WINDOWS.items():
        traded = con.execute(
            f"SELECT hs10 FROM fact_trade WHERE yyyymm BETWEEN {a} AND {b} "
            f"GROUP BY 1 HAVING SUM(exp_dlr + imp_dlr) > 0"
        ).df()
        have = set(ch[ch.past_version == pv].hs_past)
        miss = [c for c in traded.hs10 if c not in have]
        hit = 0
        for c in miss:
            heads = set(h6map.get((pv, c[:6]), set()))
            if c[:6] in by6:
                heads.add(c[:6])
            cands = [t for h in sorted(heads) for t in by6.get(h, [])]
            if not cands:
                continue
            w = pd.Series([max(val.get(t, 0.0), 0.0) for t in cands], index=cands, dtype=float)
            w = pd.Series(1.0, index=cands) if w.sum() <= 0 else w
            w = w / w.sum()
            hit += 1
            for t, wt in w.items():
                rows.append(
                    {"hs_past": c, "past_version": pv, "hs2022": t,
                     "weight": float(wt), "method": "hs6_fallback"}
                )
        logger.info(f"  {pv} 체계: 미포함 {len(miss):,}개 중 {hit:,}개를 HS6로 보완")
    return pd.concat([ch, pd.DataFrame(rows)], ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────

DDL = {
    "dim_hs10_concordance": """
        CREATE TABLE dim_hs10_concordance (
            hs_from   VARCHAR,   -- 개정 전 HS10
            hs_to     VARCHAR,   -- 개정 후 HS10
            revision  VARCHAR,   -- '2012' | '2017' | '2022'
            weight    DOUBLE,    -- hs_from 별 합이 1
            score     DOUBLE,    -- 품명 유사도(0~1). identity는 1
            relation  VARCHAR    -- 'identity' | 'moved'
        )
    """,
    "dim_hs10_to_2022": """
        CREATE TABLE dim_hs10_to_2022 (
            hs_past       VARCHAR,
            past_version  VARCHAR,  -- '2007' | '2012' | '2017'
            hs2022        VARCHAR,
            weight        DOUBLE,
            method        VARCHAR   -- 'chain'(HS10 해상도) | 'hs6_fallback'(HS6 해상도)
        )
    """,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="DB 미기록, 추출·검증만")
    args = ap.parse_args()

    logger.info("=" * 60)
    logger.info("03c: HS10 개정 연계표 구축 시작")
    logger.info("=" * 60)

    years = sorted({y for r in REVISIONS.values() for y in r[:2]})
    for a, b in BRIDGES:
        years = sorted(set(years) | {a, b})
    missing = [y for y in years if not (BYEOLPYO_DIR / f"HSK_별표_{y}.pdf").exists()]
    if missing:
        logger.error(f"별표 PDF 없음: {missing} — {BYEOLPYO_DIR}")
        sys.exit(1)

    logger.info("별표 파싱")
    tables = {y: read_byeolpyo(y) for y in years}

    con = duckdb.connect(str(DB_PATH), read_only=True)
    parts = {rev: build_revision(con, rev, tables) for rev in REVISIONS}
    full = pd.concat(parts.values(), ignore_index=True)

    logger.info("연쇄 구성(과거 체계 → 2022 체계)")
    ch = chain(parts, tables)
    ch = add_fallback(con, ch)
    con.close()

    logger.info("-" * 60)
    logger.info(f"dim_hs10_concordance {len(full):,}행, dim_hs10_to_2022 {len(ch):,}행")
    for name, df, key in [
        ("dim_hs10_concordance", full, ["hs_from", "revision"]),
        ("dim_hs10_to_2022", ch, ["hs_past", "past_version"]),
    ]:
        dev = abs(df.groupby(key).weight.sum() - 1).max()
        logger.info(f"  {name}: 가중치 합 최대 이탈 {dev:.2e}")
    logger.info(
        f"  method: chain {int((ch.method == 'chain').sum()):,}, "
        f"hs6_fallback {int((ch.method == 'hs6_fallback').sum()):,}"
    )

    if args.dry_run:
        logger.info("[dry-run] DB 미기록. 종료.")
        print(full[full.relation == "moved"].sort_values("score", ascending=False).head(10).to_string())
        return

    con = duckdb.connect(str(DB_PATH))
    for name, ddl in DDL.items():
        con.execute(f"DROP TABLE IF EXISTS {name}")
        con.execute(ddl)
    con.register("df_full", full)
    con.register("df_ch", ch)
    con.execute("INSERT INTO dim_hs10_concordance SELECT * FROM df_full")
    con.execute("INSERT INTO dim_hs10_to_2022 SELECT * FROM df_ch")
    for name in DDL:
        n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        logger.info(f"{name} 기록 완료: {n:,}행 → {DB_PATH}")
    con.close()


if __name__ == "__main__":
    main()
