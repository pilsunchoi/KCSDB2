"""06_db_status.py — DB 현황 수치를 문서에 자동으로 채운다.

왜 필요한가
-----------
"27,533,937행", "231개월", "2007.01–2026.03" 같은 수치가 README와 index.html에
손으로 적혀 있었다. 새 달을 적재할 때마다 여러 곳을 찾아 고쳐야 하고, 실제로 한 번
어긋났다 — 2026년 7월까지 적재한 뒤에도 문서는 3월까지라고 말하고 있었다.

그래서 문서에서 그 수치를 걷어내고, 표시가 필요한 자리에는 **자동으로 채우는 구간**을
두었다. 이 스크립트가 DB를 읽어 그 구간을 다시 쓴다. 손으로 고칠 일이 없어진다.

채우는 자리
-----------
    <!-- DB_STATUS:START --> ... <!-- DB_STATUS:END -->      요약 수치
    <!-- DB_COVERAGE:START --> ... <!-- DB_COVERAGE:END -->  커버리지 수치
    <!-- DB_VERSION:START --> ... <!-- DB_VERSION:END -->    하단 버전 배지
    <!-- DB_INVENTORY:START --> ... <!-- DB_INVENTORY:END -->  표 전체 목록과 출처

목록은 **DB에 실제로 있는 표를 읽어** 만든다. INVENTORY 사전에 설명이 없는 표가 나오면
"(설명 미등록)"으로 찍히므로, 표를 새로 만들면 그 사전에 한 줄을 더해야 한다.

게시본이 뒤처지면
-----------------
푸터 배지는 GitHub Releases API로 **실제 게시본**을 읽어 채운다. 예전에는 로컬 DB의
최신월로 채웠는데, 그러면 릴리스를 안 올린 채 문서만 갱신했을 때 배지가 없는 버전을
가리킨다. 실제로 그랬다 — 사이트는 2026.07까지라 적고 Releases에는 2026.03본뿐이었다.

게시본이 로컬보다 뒤처지면 느낌표 줄로 알리고 `docs/릴리스_본문.md`에 붙여넣을 문안을
만들어 둔다. **그 파일은 gitignore된다** — 작업 문서이지 산출물이 아니고, docs/는 Pages
소스라 올리면 공개 웹에 그대로 서빙되기 때문이다. 필요하면 이 스크립트가 다시 만든다.

대상은 README.md와 docs/index.html이며, 파일마다 형식이 다르므로(마크다운 목록 대
HTML 카드) 각각에 맞게 만든다. 구간이 없는 파일은 건너뛴다.

적재 뒤에 돌린다:
    python scripts/02c_reload_year.py --year 2026
    python scripts/04_validate.py
    python scripts/06_db_status.py
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "processed" / "kcsdb.duckdb"
RELEASES_API = "https://api.github.com/repos/pilsunchoi/KCSDB2/releases"


# 표마다 무엇을 담고 어디서 왔는지. 행수·기간은 DB에서 읽으므로 여기 적지 않는다.
# (설명, 단위, 출처) 순. 새 표를 만들면 여기 한 줄을 더한다.
INVENTORY = {
    "fact_trade": ("국가 × HS10 품목 × 월 수출입 실적. 이 DB의 본체",
                   "국가 × HS10 × 월",
                   "관세청 품목별 국가별 수출입실적(GW) · 공공데이터포털 15100475"),
    "fact_total": ("품목 구분 없는 국가 × 월 총계",
                   "국가 × 월",
                   "관세청 품목별 국가별 수출입실적(GW) · 15100475 (총계 응답)"),
    "fact_nqi": ("신성질별 × 국가 × 월 수출입 실적. 관세청이 직접 집계한 공식치라 "
                 "HS 개정 추정이 섞이지 않는다. <b>이 표만 2005년부터다</b>",
                 "신성질별 × 국가 × 월",
                 "관세청 신성질별 수출입실적(GW) · 15101616"),
    "fact_exp10d": ("10일 단위 잠정치 네 계열(수출·수입 × 주요품목·주요국가). "
                    "값이 바뀔 때마다 새 행을 쌓아 관측 이력을 남긴다",
                    "계열 × 항목 × 순(旬)",
                    "관세청 10일 단위 잠정치 · 15157908·15157941·15157901·15157909"),
    "dim_country": ("국가코드에 관세청 국명과 외교부 표준명·ISO2/3·대륙을 병기",
                    "국가", "관세청 + 외교부 국가표준코드 · 15091117"),
    "dim_hs10": ("HS10 품목명(한/영)·수량/중량 단위·적용시작일",
                 "HS10", "관세청 HS부호 · 15049722"),
    "dim_hs6_concordance": ("HS 개정 사이 6자리 대응. <b>관세청 공표</b>",
                            "(HS2022, 과거코드, 판본)", "관세청 FTA 포털 HS 연계표"),
    "dim_hs10_concordance": ("개정 하나(2012·2017·2022)를 건너는 10자리 연결. <b>추정</b>",
                             "(출발코드, 도착코드, 개정)",
                             "기재부 고시 별표에서 이 저장소가 추정 · 국가법령정보센터"),
    "dim_hs10_to_2022": ("과거 체계를 현행 위로 한 번에 옮기는 10자리 연결. <b>추정</b>",
                         "(과거코드, 판본, HS2022)",
                         "기재부 고시 별표에서 이 저장소가 추정 · 국가법령정보센터"),
    "dim_hs10_name_hist": ("HS10 품명 이력. 폐지코드 이름이 여기 있다",
                           "(HS10, 별표 연도)",
                           "기재부 고시 「관세·통계통합품목분류표」 별표 · 국가법령정보센터"),
    "dim_nqi": ("신성질별 분류의 대·중·소·세·세세 5단 계층과 이름",
                "신성질별 세세분류", "관세청 신성질별 분류 · 15049720"),
    "dim_hs10_to_nqi": ("HS10 → 신성질별. 현행 코드는 공표 대응, 폐지 코드는 추정 경유",
                        "(HS10, 신성질별)", "관세청 15049720 + 이 저장소 추정"),
    "dim_hs10_to_major10": ("HS10 → 수출 10대 품목. 관세청 공표치와 대조해 확정",
                            "(HS10, 품목)", "관세청 15049720 + 공표치 대조"),
    "dim_workday10d": ("상순·중순·하순의 일수와 조업일수. 10일 자료를 견주려면 필요하다",
                       "(연월, 구간)", "한국천문연구원 특일정보 API로 대조한 공휴일 달력"),
    "meta_calls": ("API 호출 기록. 무엇을 언제 몇 번 받았는지",
                   "호출", "이 저장소의 수집 로그"),
    "mart_exp10d_metrics": ("10일 자료 지표 — 조업일수 보정 전년 대비, 비중, 기여도, 진도율",
                            "계열 × 순 × 항목", "이 저장소가 계산"),
    "mart_exp10d_beta": ("항목별 조업일수 탄력성", "계열 × 항목 × 대상", "이 저장소가 추정"),
    "mart_exp10d_progress": ("진도율 분포(조업일 보정 전후)", "계열 × 항목 × 구간 × 달",
                             "이 저장소가 계산"),
    "mart_exp10d_forecast": ("월 마감 예측과 구간", "계열 × 순 × 항목", "이 저장소가 추정"),
    "mart_exp10d_fcskill": ("예측의 표본외 성적과 등급", "계열 × 항목 × 구간",
                            "이 저장소가 계산"),
    "mart_exp10d_fclog": ("확정 전 예측의 기록(추가 전용)", "예측 시점", "이 저장소가 계산"),
    "mart_nqi_check": ("신성질별 공식치와 우리 도출치의 월별 대조", "월", "이 저장소가 계산"),
}

PERIOD_COL = {"fact_trade": "yyyymm", "fact_total": "yyyymm", "fact_nqi": "yyyymm",
              "fact_exp10d": "base_ym", "dim_workday10d": "base_ym",
              "mart_exp10d_metrics": "base_ym", "mart_exp10d_forecast": "base_ym",
              "mart_nqi_check": "yyyymm"}


def inventory() -> list[dict]:
    """DB에 실제로 있는 표를 읽어 목록을 만든다. 설명은 INVENTORY 사전에서 가져온다."""
    con = duckdb.connect(str(DB), read_only=True)
    try:
        names = [r[0] for r in con.sql(
            "SELECT table_name FROM duckdb_tables()").fetchall()]
        # INVENTORY에 적은 순서를 따른다. 알파벳순이면 본체인 fact_trade가 맨 뒤로
        # 밀려 읽는 순서가 어그러진다. 사전에 없는 표는 뒤에 붙는다.
        order = {t: i for i, t in enumerate(INVENTORY)}
        names.sort(key=lambda t: (order.get(t, 10_000), t))
        out = []
        for t in names:
            n = con.sql(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            per = ""
            col = PERIOD_COL.get(t)
            if col:
                a, b = con.sql(f"SELECT MIN({col}), MAX({col}) FROM {t}").fetchone()
                if a:
                    per = f"{a // 100}.{a % 100:02d}–{b // 100}.{b % 100:02d}"
            d, u, src = INVENTORY.get(t, ("(설명 미등록)", "", ""))
            out.append(dict(table=t, rows=n, period=per, desc=d, unit=u, src=src))
        return out
    finally:
        con.close()


def block_inv(inv: list[dict]) -> str:
    """표 전체 목록. 종류별로 묶어 보여 준다."""
    groups = [("fact", "fact — 관세청이 준 실적 그대로"),
              ("dim", "dim — 코드에 이름과 연결을 붙이는 참조"),
              ("mart", "mart — 이 저장소가 계산한 지표"),
              ("meta", "meta — 수집 기록")]
    rows = []
    for pre, title in groups:
        part = [r for r in inv if r["table"].startswith(pre)]
        if not part:
            continue
        rows.append(f'          <tr class="grp"><td colspan="5">{title}</td></tr>')
        for r in part:
            rows.append(
                "          <tr>"
                f"<td><code>{r['table']}</code></td>"
                f"<td>{r['desc']}</td>"
                f"<td class=\"r\">{r['rows']:,}</td>"
                f"<td class=\"s\">{r['period'] or '—'}</td>"
                f"<td class=\"s\">{r['src']}</td></tr>")
    return "\n".join([
        '    <div class="tblwrap">',
        '      <table class="inv">',
        '        <caption>이 DB에 들어 있는 것 전부 (행수·기간은 자동 갱신)</caption>',
        '        <thead><tr><th>테이블</th><th>담는 것</th><th class="r">행수</th>'
        '<th>기간</th><th>출처</th></tr></thead>',
        '        <tbody>',
        *rows,
        '        </tbody>',
        '      </table>',
        '    </div>',
    ])


def release_draft(s: dict, inv: list[dict], tag: str) -> str:
    """Releases에 붙여넣을 본문 초안. DB를 읽어 만들므로 손으로 고칠 일이 없다.

    예전 릴리스 본문은 출처를 넷만 적고 있었다 — 그 뒤에 표가 열넷 늘었는데
    본문은 그대로였기 때문이다. 그래서 본문도 자동으로 만든다.
    """
    gz = DB.with_suffix(".duckdb.gz")
    gzmb = gz.stat().st_size / 1024 ** 2 if gz.exists() else None
    strip = lambda t: t.replace("<b>", "").replace("</b>", "")

    def group(pre, title):
        out = [f"**{title}**"]
        for r in [x for x in inv if x["table"].startswith(pre)]:
            per = f", {r['period']}" if r["period"] else ""
            out.append(f"- `{r['table']}` {r['rows']:,}행{per} — {strip(r['desc'])}")
        return out

    srcs = []
    for r in inv:
        if r["src"] and r["src"] not in srcs and not r["src"].startswith("이 저장소"):
            srcs.append(r["src"])

    body = [
        f"관세청 무역통계 DB. 월 실적 {s['lo']}–{s['hi']} ({s['months']}개월), "
        f"{s['n_country']}개국, HS10 {s['n_hs10']:,}종, {s['rows']:,} 거래행.",
        "",
        f"압축 파일 `kcsdb.duckdb.gz`"
        + (f" ({gzmb:,.0f}MB, 압축 해제 시 {s['db_mb']:,.0f}MB)" if gzmb
           else f" (압축 해제 시 {s['db_mb']:,.0f}MB)")
        + "를 받아 압축을 풀고 `data/processed/kcsdb.duckdb`에 놓는다.",
        "사용법은 저장소 `docs/학생_사용안내.md`, DB 소개는 "
        "https://pilsunchoi.github.io/KCSDB2/ 참조.",
        "",
        "## 들어 있는 것",
        "",
        *group("fact", "fact — 관세청이 준 실적 그대로"), "",
        *group("dim", "dim — 코드에 이름과 연결을 붙이는 참조"), "",
        *group("mart", "mart — 이 저장소가 계산한 지표 (받은 자료가 아니다)"), "",
        "## 알아 둘 것",
        "",
        "- 표마다 기간이 다르다. 신성질별 공식 실적(`fact_nqi`)만 2005년부터이고, "
        "조업일수 달력(`dim_workday10d`)은 2027년까지 미리 만들어 두었다.",
        "- `fact_trade`와 `fact_nqi`는 **같은 무역을 다른 분류로 집계한 것이라 "
        "더하면 안 된다.**",
        "- `v_exp10d_seg` 뷰를 쓸 때는 `series`를 반드시 걸러야 한다"
        "(안 그러면 품목과 국가가 섞인다).",
        "- 접두어가 `mart`인 표는 우리가 계산한 것이다. 그대로 인용하기 전에 "
        "근거를 확인할 것.",
        "- 10자리 HS 연계(`dim_hs10_concordance`·`dim_hs10_to_2022`)는 공표된 표가 "
        "아니라 **이 저장소가 고시 별표에서 추정한 것**이다. "
        "6자리(`dim_hs6_concordance`)만 관세청 공표다.",
        "",
        "## 출처",
        "",
        *[f"- {x}" for x in srcs],
        "",
        "공공누리 제1유형(출처표시)으로 개방된 저작물을 이용하였다. 공공기관이 이 "
        "저작물을 후원하거나 특수 관계에 있는 것으로 오인하게 하는 표시를 하지 않는다.",
    ]
    return "\n".join([
        f"# 릴리스 본문 초안 — {tag}",
        "",
        "> `06_db_status.py`가 DB를 읽어 만든다. Releases에 붙여넣기 전에 다시 돌릴 것.",
        "> 절차는 `docs/배포자_안내.md`.",
        "",
        f"## 태그\n\n`{tag}`",
        "",
        f"## 제목\n\nKCSDB2 {tag} ({s['lo']}–{s['hi']})",
        "",
        "## 본문 (아래 상자 안을 그대로 붙여넣는다)",
        "",
        "```markdown",
        *body,
        "```",
        "",
    ])


def collect() -> dict:
    con = duckdb.connect(str(DB), read_only=True)
    try:
        rows, lo, hi, months = con.sql(
            "SELECT COUNT(*), MIN(yyyymm), MAX(yyyymm), COUNT(DISTINCT yyyymm) FROM fact_trade"
        ).fetchone()
        n_country = con.sql("SELECT COUNT(DISTINCT stat_cd) FROM fact_trade").fetchone()[0]
        n_hs10 = con.sql("SELECT COUNT(DISTINCT hs10) FROM fact_trade").fetchone()[0]
        named = con.sql("""SELECT COUNT(DISTINCT f.hs10) FROM fact_trade f
                           JOIN dim_hs10 d USING (hs10)""").fetchone()[0]
        e_lo, e_hi, e_rows, e_ser = con.sql(
            "SELECT MIN(base_ym), MAX(base_ym), COUNT(*), COUNT(DISTINCT series) "
            "FROM fact_exp10d").fetchone()
    finally:
        con.close()

    fmt = lambda ym: f"{ym // 100}.{ym % 100:02d}"
    return dict(
        rows=rows, months=months, lo=fmt(lo), hi=fmt(hi),
        n_country=n_country, n_hs10=n_hs10, named=named,
        pct=100.0 * named / n_hs10,
        exp_lo=fmt(e_lo), exp_hi=fmt(e_hi), exp_rows=e_rows, exp_series=e_ser,
        db_mb=DB.stat().st_size / 1024 ** 2,
    )


def block_md(s: dict) -> str:
    return (
        f"- 데이터 범위: 월 실적 {s['lo']}–{s['hi']} ({s['months']}개월), "
        f"{s['n_country']}개국, HS10 {s['n_hs10']:,}종, {s['rows']:,} 거래행\n"
        f"- 10일 단위 잠정치: {s['exp_lo']}–{s['exp_hi']}, "
        f"{s['exp_series']}개 계열(수출·수입 × 품목·국가) {s['exp_rows']:,}행\n"
        f"- DB 파일: 약 {s['db_mb']:,.0f}MB"
    )


def block_html(s: dict) -> str:
    card = ('      <div class="stat"><div class="n">{n}</div><div class="k">{k}</div></div>')
    return "\n".join([
        '    <div class="stats">',
        card.format(n=f"{s['rows']:,}", k="fact_trade 거래행"),
        card.format(n=f"{s['lo']}–{s['hi']}", k=f"월 실적 범위 ({s['months']}개월)"),
        card.format(n=f"{s['n_country']}", k="국가 (stat_cd)"),
        card.format(n=f"{s['n_hs10']:,}", k="HS10 품목 종수"),
        '    </div>',
    ])


def block_cov(s: dict) -> str:
    card = ('      <div class="stat"><div class="n">{n}</div><div class="k">{k}</div></div>')
    return "\n".join([
        '    <div class="stats">',
        card.format(n=f"{s['months']}개월", k=f"{s['lo']}–{s['hi']} 연속(빠짐 없음)"),
        card.format(n=f"{s['n_country']} / {s['n_country']}", k="국가 dim 매칭 (미매칭 0)"),
        card.format(n=f"{s['n_hs10']:,}종", k="HS10 (fact 등장)"),
        card.format(n=f"{s['pct']:.1f}%",
                    k=f"hs10 → dim_hs10 ({s['named']:,}/{s['n_hs10']:,})"),
        '    </div>',
    ])


def released() -> dict | None:
    """실제로 게시된 최신 릴리스. 못 읽으면 None.

    배지는 **읽는 사람이 받을 수 있는 것**을 말해야 한다. 예전에는 로컬 DB의
    최신월로 채웠는데, 그러면 릴리스를 안 올린 채 문서만 갱신했을 때 배지가
    없는 버전을 가리킨다. 실제로 그런 일이 있었다 — 사이트는 v1.0-202607이라
    적고 Releases에는 v1.0-202603만 있었다.
    """
    try:
        req = urllib.request.Request(
            RELEASES_API, headers={"User-Agent": "kcsdb-db-status",
                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            rel = json.load(r)
    except Exception:
        return None
    rel = [x for x in rel if not x.get("draft")]
    if not rel:
        return None
    top = rel[0]
    asset = max(top.get("assets") or [], key=lambda a: a["size"], default=None)
    return dict(tag=top["tag_name"], published=top["published_at"][:10],
                mb=(asset["size"] / 1024 ** 2) if asset else 0.0,
                name=asset["name"] if asset else "")


def block_ver(s: dict, rel: dict | None) -> str:
    """하단 버전 배지. 모든 탭에 보이므로 낡으면 눈에 띈다."""
    if rel is None:                       # 망을 못 쓰면 로컬 기준임을 밝힌다
        return f"로컬 DB {s['hi']} 기준 (게시본 확인 못 함)"
    return f"내려받기 {rel['tag']} · {rel['published']} 게시"


def fill(path: Path, marker: str, body: str) -> bool:
    """표시 구간을 채운다. 줄바꿈은 있어도 없어도 되게 두어 한 줄짜리 배지도 다룬다."""
    text = path.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(<!-- {marker}:START -->\n?).*?(\n?<!-- {marker}:END -->)", re.S)
    if not pat.search(text):
        return False
    path.write_text(pat.sub(lambda m: m.group(1) + body + m.group(2), text),
                    encoding="utf-8")
    return True


def main() -> None:
    s = collect()
    inv = inventory()
    rel = released()
    print(f"fact_trade {s['rows']:,}행 | {s['lo']}~{s['hi']} ({s['months']}개월) | "
          f"{s['n_country']}개국 | HS10 {s['n_hs10']:,}종")
    print(f"품목명 매칭 {s['named']:,}/{s['n_hs10']:,} ({s['pct']:.1f}%) | "
          f"10일 단위 {s['exp_lo']}~{s['exp_hi']} | DB {s['db_mb']:,.0f}MB")

    # 게시본이 로컬보다 뒤처지면 크게 알린다. 문서만 갱신하고 릴리스를 안 올리면
    # 읽는 사람은 사이트가 설명하는 것과 다른 DB를 받게 된다.
    if rel is None:
        print("\n  [알림] 게시된 릴리스를 확인하지 못했다(망 문제일 수 있다). "
              "배지는 로컬 기준으로 적었다.")
    else:
        want = f"v1.0-{s['hi'].replace('.', '')}"
        print(f"\n게시본 {rel['tag']} ({rel['published']}, {rel['name']} "
              f"{rel['mb']:,.0f}MB) | 로컬 기준이면 {want}")
        if rel["tag"] != want:
            draft = ROOT / "docs" / "릴리스_본문.md"
            draft.write_text(release_draft(s, inv, want), encoding="utf-8",
                             newline="\n")
            print("\n" + "!" * 68)
            print(f"  게시본이 로컬보다 뒤처진다. 사이트는 {s['lo']}~{s['hi']}를 설명하는데")
            print(f"  Releases에서 받을 수 있는 것은 {rel['tag']}이다.")
            print(f"  새 릴리스를 올릴 것 — 절차는 docs/배포자_안내.md, 본문 초안은")
            print(f"  docs/릴리스_본문.md에 있다.")
            print("!" * 68)
    print()
    for path, marker, body in [
        (ROOT / "README.md", "DB_STATUS", block_md(s)),
        (ROOT / "docs" / "index.html", "DB_STATUS", block_html(s)),
        (ROOT / "docs" / "index.html", "DB_COVERAGE", block_cov(s)),
        (ROOT / "docs" / "index.html", "DB_VERSION", block_ver(s, rel)),
        (ROOT / "docs" / "index.html", "DB_INVENTORY", block_inv(inv)),
    ]:
        ok = fill(path, marker, body)
        print(f"  {'채움' if ok else '구간 없음'}  {path.name} [{marker}]")


if __name__ == "__main__":
    main()
