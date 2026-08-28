"""
HSK 별표 전문 PDF 파서
======================

기획재정부 고시 「관세ㆍ통계통합품목분류표」의 별표 전문 PDF에서
(HS10 코드, 국문 품명, 영문 품명, 계층 경로 품명)을 뽑는다.

03c(HS10 개정 연계표)와 03d(HS10 품명 이력)가 함께 쓴다.
받는 경로와 첨부일련번호는 docs/DB_구축_원칙.md §3.4 참조.

학생 교훈
--------
함정 1: 이 PDF에는 표 격자선이 없다. 그러나 열 x좌표는 고정이므로
        낱말의 성격(숫자 / 한글 / 로마자)으로 갈라 각 무리의 최빈 x를
        열 기준으로 삼으면 표를 복원할 수 있다. 영문 열을 찾을 때
        단순 최빈값을 쓰면 국문 열 안쪽 좌표에 걸린다.

함정 2: 판본마다 열 좌표가 몇 pt씩 흔들린다(2015년 판은 10자리 열이
        115.6~119.5에 퍼져 있다). 고정 허용오차로 자르면 3천여 행이
        자릿수를 잃는다. 이웃 열의 중간점을 경계로 삼아야 한다.

함정 3: 앞자리 0. HSK에는 7자리·9자리 중간 표제가 있어 코드 조각이
        '9'(9자리 표제)와 '09'(10자리)로 갈린다. PDF에 찍힌 문자열을
        그대로 이어야 한다. 같은 이유로 XLSX판은 쓸 수 없다(숫자 셀이라
        '00'이 0이 되고 '09'가 9가 된다).

함정 4: 쪽번호가 품명 꼬리에 붙는다. '기타 - 2 -'처럼 나온다. 낱말이
        전부 숫자·하이픈인 줄을 지우면 될 것 같지만, 2022년 판에는
        품명 칸이 빈 데이터 행이 14개 있어 그 행까지 사라진다. 쪽번호가
        찍히는 y좌표를 문서마다 먼저 찾아 그 줄만 버려야 한다.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import fitz
import pandas as pd

PAGENO_RE = re.compile(r"^-$|^\d{1,4}$")


def _is_hangul(s: str) -> bool:
    return any("가" <= ch <= "힣" for ch in s)


def _lines(page) -> dict[float, list[tuple[float, str]]]:
    """페이지의 낱말을 y로 묶어 줄 단위 사전으로 만든다."""
    out: dict[float, list[tuple[float, str]]] = {}
    for x0, y0, _x1, _y1, txt, *_ in page.get_text("words"):
        key = round(y0, 1)
        for k in out:
            if abs(k - key) <= 1.5:
                key = k
                break
        out.setdefault(key, []).append((x0, txt))
    return out


def footer_y(doc, min_share: float = 0.5) -> float | None:
    """쪽번호가 찍히는 y좌표. 그런 줄이 없으면 None.

    낱말이 전부 숫자·하이픈인 줄의 y를 모아, 거의 모든 쪽에 되풀이되는
    좌표를 쪽번호 줄로 본다. 데이터 행이 우연히 숫자만 갖는 경우는
    그 좌표가 되풀이되지 않으므로 걸러진다.
    """
    c: Counter = Counter()
    pages = 0
    for page in doc:
        ls = _lines(page)
        if not ls:
            continue
        pages += 1
        for y, ws in ls.items():
            if ws and all(PAGENO_RE.match(t) for _, t in ws):
                c[round(y)] += 1
    if not c or pages == 0:
        return None
    y, n = c.most_common(1)[0]
    return float(y) if n >= pages * min_share else None


def column_anchors(doc, sample: int = 60) -> list[float]:
    """HS4·소호·8자리·10자리·국문·영문 여섯 열의 시작 x좌표."""
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


def parse_page(page, anch: list[float], fy: float | None = None) -> list[dict]:
    c0, c1, c2, c3, kx, ex = anch
    tol = 3.0
    bnd = [(c0 + c1) / 2, (c1 + c2) / 2, (c2 + c3) / 2, kx - 4]
    recs: list[dict] = []
    cur: dict | None = None
    for y, ws in sorted(_lines(page).items()):
        if fy is not None and abs(y - fy) <= 2:
            continue  # 쪽번호 줄
        ws = sorted(ws)
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


def read(pdf_path: str | Path) -> pd.DataFrame:
    """별표 PDF → HS10마다 (code, leaf, path, name_en).

    leaf는 그 코드 자신의 품명, path는 부모 품명을 뿌리부터 이어 붙인 문자열.
    10자리 품명은 '기타'가 태반이라 leaf만으로는 변별력이 없어 path가 필요하다.
    """
    doc = fitz.open(pdf_path)
    anch = column_anchors(doc)
    fy = footer_y(doc)
    recs: list[dict] = []
    for page in doc:
        recs += parse_page(page, anch, fy)

    stack: dict[int, tuple[str, str]] = {}
    rows = []
    for r in recs:
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
    return pd.DataFrame(rows).drop_duplicates("code")
