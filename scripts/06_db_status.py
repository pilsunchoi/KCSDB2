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

대상은 README.md와 docs/index.html이며, 파일마다 형식이 다르므로(마크다운 목록 대
HTML 카드) 각각에 맞게 만든다. 구간이 없는 파일은 건너뛴다.

적재 뒤에 돌린다:
    python scripts/02c_reload_year.py --year 2026
    python scripts/04_validate.py
    python scripts/06_db_status.py
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "processed" / "kcsdb.duckdb"


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
        e_lo, e_hi = con.sql("SELECT MIN(base_ym), MAX(base_ym) FROM fact_exp10d").fetchone()
    finally:
        con.close()

    fmt = lambda ym: f"{ym // 100}.{ym % 100:02d}"
    return dict(
        rows=rows, months=months, lo=fmt(lo), hi=fmt(hi),
        n_country=n_country, n_hs10=n_hs10, named=named,
        pct=100.0 * named / n_hs10,
        exp_lo=fmt(e_lo), exp_hi=fmt(e_hi),
        db_mb=DB.stat().st_size / 1024 ** 2,
    )


def block_md(s: dict) -> str:
    return (
        f"- 데이터 범위: 월 실적 {s['lo']}–{s['hi']} ({s['months']}개월), "
        f"{s['n_country']}개국, HS10 {s['n_hs10']:,}종, {s['rows']:,} 거래행\n"
        f"- 10일 단위 잠정치: {s['exp_lo']}–{s['exp_hi']}, 10대 품목 + 총수출\n"
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


def block_ver(s: dict) -> str:
    """하단 버전 배지. 모든 탭에 보이므로 낡으면 눈에 띈다."""
    return f"v1.0-{s['hi'].replace('.', '')} 기준"


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
    print(f"fact_trade {s['rows']:,}행 | {s['lo']}~{s['hi']} ({s['months']}개월) | "
          f"{s['n_country']}개국 | HS10 {s['n_hs10']:,}종")
    print(f"품목명 매칭 {s['named']:,}/{s['n_hs10']:,} ({s['pct']:.1f}%) | "
          f"10일 단위 {s['exp_lo']}~{s['exp_hi']} | DB {s['db_mb']:,.0f}MB")
    print()
    for path, marker, body in [
        (ROOT / "README.md", "DB_STATUS", block_md(s)),
        (ROOT / "docs" / "index.html", "DB_STATUS", block_html(s)),
        (ROOT / "docs" / "index.html", "DB_COVERAGE", block_cov(s)),
        (ROOT / "docs" / "index.html", "DB_VERSION", block_ver(s)),
    ]:
        ok = fill(path, marker, body)
        print(f"  {'채움' if ok else '구간 없음'}  {path.name} [{marker}]")


if __name__ == "__main__":
    main()
