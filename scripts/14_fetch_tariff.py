"""
14_fetch_tariff.py — 연도별 HSK 10단위 실행세율 수집 (관세법령정보포털)

무엇을 푸는가:
  KCSDB2에는 관세율이 없다. 품목분류와 세율 차이를 함께 보려면(세율이 높은 품목의 수입이
  세율 낮은 비슷한 품목으로 옮겨 가는가) 코드마다 그해 적용된 세율이 있어야 한다.
  공공데이터포털의 품목번호별 관세율표(15051179)는 현행판만 있어 과거를 못 준다.

  관세법령정보포털은 2002년부터 연도별로 두 화면을 준다. 둘을 합쳐야 실행세율이 된다.
    관세율표(openULS0201005Q)   기본세율 + 탄력·양허 세율(WTO 협정세율 C, 농림축산물
                                양허관세 W1·W2, 조정관세 L, 할당관세 P, 특별긴급관세 T,
                                아시아·태평양 협정세율 E 등). 연중 변경 표시는 없다.
    주요세율보기(openULS0201017Q) 기본·WTO·아시아태평양 + FTA 일곱 상대(중국·EU·미국·
                                아세안·인도·베트남·캐나다). 연중에 바뀌면 기간을 나눠
                                보여 준다(한-EU FTA는 7월 1일 인하). 조정·할당·양허는 없다.
  그 밖의 FTA(칠레·EFTA·호주 등)는 품목 상세 화면에만 있어 코드마다 따로 받아야 하므로
  여기서는 받지 않는다. 연구 대상 코드가 정해지면 그것만 받는다.

받는 방법:
  두 화면 모두 hsfdCd에 류(2자리)를 주면 그 류 전체가 온다. 연도당 97회씩이다.
  관세율표 화면은 `03j_fetch_hsk_table.py`와 같은 요청이라 캐시(data/raw/clip_hsk)를
  함께 쓴다 — 2007~2010년은 이미 받아 두었다.

입력: 없음 (포털에서 받는다)
출력:
  data/raw/clip_hsk/<연도>/<류>.html            관세율표 캐시 (03j와 공유)
  data/raw/clip_tariff_main/<연도>/<류>.html    주요세율보기 캐시
  data/processed/kcstariff.duckdb               별도 DB 파일 (KCSDB2와 ATTACH로 결합)
    tariff_code   year, hs10, name_ko, source              그해 관세율표의 10단위 코드
    tariff_rate   year, hs10, rate_cd, rate_txt, adval, specific, valid_from, valid_to, source
                  source는 table(관세율표) · main(주요세율보기의 FTA 열) · fill(관세율표 화면을
                  받지 못한 코드를 주요세율보기의 기본·WTO·아시아태평양 세율로 채운 것)
    dim_rate_cd   rate_cd, rate_nm, source
    meta_fetch    page, year, ryu, fetched_at, bytes

세율 값:
  rate_txt는 화면 문구 그대로다. adval은 종가세율(%), specific은 종량세액(원)이다.
  「270% 또는 6,210원」은 둘 다 채운다. 어느 쪽을 적용하는지는 여기서 정하지 않는다.
  FTA 세율은 특혜를 신청했을 때 적용 가능한 세율이지 실제 납부 세율이 아니다.
  포털은 10단위 세율이 참고용이고 법적 효력이 없다고 밝힌다.

검증:
  ① 두 화면의 코드 집합이 같은가 ② 두 화면에 함께 나오는 기본세율·WTO 세율이 같은가
  ③ 알려진 값 몇 개(건고추 270%, 고추다진양념 조정관세 45% 등) ④ 그해 수입액 중
  세율표에 코드가 있는 몫.

실행:
  python scripts\\14_fetch_tariff.py                       # 2007~2026 전부
  python scripts\\14_fetch_tariff.py --years 2025 --ryu 9 21
  python scripts\\14_fetch_tariff.py --parse-only          # 캐시에서 다시 적재만
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW = PROJECT_ROOT / "data" / "raw"
DB_OUT = PROJECT_ROOT / "data" / "processed" / "kcstariff.duckdb"
DB_TRADE = PROJECT_ROOT / "data" / "processed" / "kcsdb.duckdb"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_PATH = LOG_DIR / f"fetch_tariff_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

BASE = "https://unipass.customs.go.kr/clip/hsinfosrch/"
PAGES = {
    "table": ("openULS0201005Q.do", RAW / "clip_hsk"),
    "main": ("openULS0201017Q.do", RAW / "clip_tariff_main"),
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://unipass.customs.go.kr/clip/index.do",
    "Content-Type": "application/x-www-form-urlencoded",
}
YEARS = range(2007, 2027)
RYU = range(1, 100)
ERROR_MARK = "프로그램 오류발생"

# 주요세율보기의 열 이름. 화면 머리글은 '중국'처럼 짧아 이름을 여기서 준다.
MAIN_NAMES = {
    "FCN1": "한ㆍ중국 FTA협정세율(선택1)", "FEU1": "한ㆍEU FTA협정세율(선택1)",
    "FUS1": "한ㆍ미 FTA 협정세율(선택1)", "FAS1": "한ㆍ아세안 FTA협정세율(선택1)",
    "FIN1": "한ㆍ인도 FTA협정세율(선택1)", "FVN1": "한ㆍ베트남 FTA협정세율(선택1)",
    "FCA1": "한ㆍ캐나다 FTA협정세율(선택1)",
}


def fetch(sess: requests.Session, page: str, year: int, ryu: int) -> str:
    """류 하나를 받는다. 관세율표는 03j와 캐시를 함께 쓰므로 요청 변수도 03j와 같게 둔다."""
    ymd = "20070101" if page == "table" else f"{year}0101"
    data = dict(cntyCd="KR", aplyYy=str(year), cntyNm="한국", compareCrrspndNation="KR",
                sctYear=ymd, hstdYear=ymd, manlOrgnTpcd="01", tabTpcd="3",
                sctCd="01", hstdCd=f"{ryu:02d}", hsfdCd=f"{ryu:02d}", searchVal=f"{ryu:02d}")
    r = sess.post(BASE + PAGES[page][0], data=data, timeout=60)
    r.raise_for_status()
    # 서버가 표를 절반쯤 보내다 오류 안내문으로 끝내는 일이 있다(2011년 제90류). 상태 코드는
    # 200이라 문구로 가려낸다 — 그대로 캐시하면 코드 수백 개가 조용히 빠진다.
    if ERROR_MARK in r.text:
        raise requests.RequestException("응답이 오류 안내문으로 끝났다")
    return r.text


def cell_text(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x))).replace(chr(0xa0), " ").strip()


def parse_rate(txt: str) -> tuple[float | None, float | None]:
    """'270% 또는 6,210원' → (270.0, 6210.0). 못 읽으면 None."""
    a = re.search(r"([\d.]+)\s*%", txt)
    s = re.search(r"([\d,]+(?:\.\d+)?)\s*원", txt)
    return (float(a.group(1)) if a else None,
            float(s.group(1).replace(",", "")) if s else None)


def parse_table(page: str, year: int) -> tuple[list, list]:
    """관세율표 화면 → (코드 행, 세율 행).

    10단위 행마다 기본세율 칸이 있고, 탄력·양허 세율은 표에 비어 있는 span을 페이지의
    스크립트(f_KorAdTax)가 행 번호(korAdTax_N)로 채운다. 행 번호로 둘을 잇는다.
    """
    codes, rates, rowno = [], [], {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        h = re.search(r'name="hsSgn_Mn" value="(\d{10})"', tr)
        k = re.search(r'korAdTax_(\d+)', tr)
        if not (h and k):
            continue
        tds = [cell_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        hs10 = h.group(1)
        rowno[k.group(1)] = hs10
        codes.append((year, hs10, tds[3] if len(tds) > 3 else ""))
        if len(tds) > 5 and tds[5]:
            rates.append((year, hs10, "A", tds[5], None, None, "table"))

    js = page[page.find("function f_KorAdTax"):]
    js = js[:js.find("</script>")]
    for blk in re.split(r'showID = "#korAdTax_" \+ "', js)[1:]:
        n = blk[:blk.find('"')]
        cds = re.findall(r'<span title="([^"]+)"><a href="#"><span class="textColor">(\w+)</span>', blk)
        vals = re.findall(r"htm2 \+= '<span><a href=\"#\">([^<]*)</a>", blk)
        if len(cds) != len(vals):
            logger.warning("  %d년 행 %s: 세율 구분 %d개와 값 %d개가 어긋난다", year, n, len(cds), len(vals))
        hs10 = rowno.get(n)
        if hs10 is None:
            continue
        for (nm, cd), v in zip(cds, vals):
            rates.append((year, hs10, cd, v.strip(), nm, None, "table"))
    return codes, rates


PERIOD = re.compile(r"(.*?)\s*-\s*\((\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})?\)")


def parse_main(page: str, year: int) -> tuple[list, list]:
    """주요세율보기 화면 → 세율 행. 머리글 마지막 줄의 구분기호(A, C, FCN1 …)가 열 순서다.

    첫 줄에도 'E1'이라는 표시가 있어 머리글 전체에서 구분기호를 모으면 열이 하나 늘어난다.
    """
    thead = page[page.find("<thead"):page.find("</thead>")]
    last = re.findall(r"<tr[^>]*>(.*?)</tr>", thead, re.S)[-1]
    cols = [c for c in (cell_text(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", last, re.S))
            if re.fullmatch(r"[A-Z][A-Z0-9]*", c)]
    out, names = [], []
    body = page[page.find("<tbody"):]
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        tds = [cell_text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) < 4 + len(cols) or not (re.fullmatch(r"\d{4}", tds[0])
                                             and re.fullmatch(r"\d{2}", tds[1])
                                             and re.fullmatch(r"\d{4}", tds[2])):
            continue
        hs10 = tds[0] + tds[1] + tds[2]
        names.append((year, hs10, tds[3]))
        for cd, v in zip(cols, tds[4:4 + len(cols)]):
            if not v:
                continue
            segs = PERIOD.findall(v)
            if segs:
                for val, f, t in segs:
                    out.append((year, hs10, cd, val.strip(), None, (f, t or None), "main"))
            else:
                out.append((year, hs10, cd, v, None, None, "main"))
    return out, names


def collect(years, ryus, pages, delay, parse_only):
    sess = requests.Session()
    sess.headers.update(HEADERS)
    meta = []
    n_req = 0
    for y in years:
        for page in pages:
            cache = PAGES[page][1] / str(y)
            cache.mkdir(parents=True, exist_ok=True)
            for ryu in ryus:
                f = cache / f"{ryu:02d}.html"
                if f.exists() or parse_only:
                    continue
                for attempt in range(3):
                    try:
                        txt = fetch(sess, page, y, ryu)
                        break
                    except requests.RequestException as e:
                        logger.warning("  %s %d년 제%02d류 실패(%d회): %s", page, y, ryu, attempt + 1, e)
                        time.sleep(5 * (attempt + 1))
                else:
                    # 같은 자리에서 매번 끊기는 류가 있다(2011년 관세율표 제90류는 9006519000
                    # 행에서 서버 오류가 난다. 호 단위로 받아도 같다). 캐시하지 않고 넘어가며,
                    # build가 그 코드들을 주요세율보기로 채운다.
                    logger.error("  %s %d년 제%02d류를 받지 못해 건너뛴다", page, y, ryu)
                    continue
                f.write_text(txt, encoding="utf-8")
                meta.append((page, y, ryu, datetime.now(), len(txt)))
                n_req += 1
                time.sleep(delay)
            logger.info("%d년 %s 캐시 준비 (이번에 받은 누적 %d회)", y, page, n_req)
    return meta


def build(years, ryus):
    codes, rates, main_codes = [], [], []
    for y in years:
        for ryu in ryus:
            ft = PAGES["table"][1] / str(y) / f"{ryu:02d}.html"
            fm = PAGES["main"][1] / str(y) / f"{ryu:02d}.html"
            for f in (ft, fm):
                if f.exists() and ERROR_MARK in f.read_text(encoding="utf-8"):
                    logger.warning("  오류 안내문이 섞인 캐시: %s (지우고 다시 받을 것)", f)
            if ft.exists():
                c, r = parse_table(ft.read_text(encoding="utf-8"), y)
                codes += c
                rates += r
            if fm.exists():
                r, c = parse_main(fm.read_text(encoding="utf-8"), y)
                rates += r
                main_codes += c

    code = (pd.DataFrame(codes, columns=["year", "hs10", "name_ko"])
            .drop_duplicates(["year", "hs10"]).assign(source="table"))
    mc = pd.DataFrame(main_codes, columns=["year", "hs10", "name_ko"]).drop_duplicates(["year", "hs10"])
    fill = (mc.merge(code[["year", "hs10"]], how="left", indicator=True)
            .query("_merge == 'left_only'").drop(columns="_merge"))
    code = pd.concat([code, fill.assign(source="main")], ignore_index=True)
    rt = pd.DataFrame(rates, columns=["year", "hs10", "rate_cd", "rate_txt", "rate_nm", "period", "source"])

    # 관세율표 화면을 받지 못한 코드는 주요세율보기의 기본·WTO·아시아태평양 세율로 채운다.
    # 그 코드에 조정·할당·양허관세가 있었다면 빠진다(2011년 제90류는 앞뒤 해에 그런 세율이 없다).
    fk = set(zip(fill.year, fill.hs10))
    isfill = ((rt.source == "main") & ~rt.rate_cd.str.startswith("F")
              & pd.Series([(y, h) in fk for y, h in zip(rt.year, rt.hs10)], index=rt.index))
    rt.loc[isfill, "source"] = "fill"
    if len(fill):
        logger.warning("  관세율표 화면에 없어 주요세율보기로 채운 코드: %s",
                       fill.groupby("year").size().to_dict())
    rt["valid_from"] = [date(y, 1, 1) if p is None else date.fromisoformat(p[0])
                        for y, p in zip(rt.year, rt.period)]
    rt["valid_to"] = [date(y, 12, 31) if p is None or p[1] is None else date.fromisoformat(p[1])
                      for y, p in zip(rt.year, rt.period)]
    ad = rt.rate_txt.map(parse_rate)
    rt["adval"] = [a for a, _ in ad]
    rt["specific"] = [s for _, s in ad]

    names = rt.dropna(subset=["rate_nm"]).drop_duplicates("rate_cd")[["rate_cd", "rate_nm"]]
    names["source"] = "table"
    fta = pd.DataFrame([(k, v, "main") for k, v in MAIN_NAMES.items()], columns=names.columns)
    names = pd.concat([names, fta]).drop_duplicates("rate_cd")
    names.loc[names.rate_cd == "A", ["rate_nm", "source"]] = ["기본세율", "table"]
    if "A" not in set(names.rate_cd):
        names.loc[len(names)] = ["A", "기본세율", "table"]
    return code, rt, names


def verify(code: pd.DataFrame, rt: pd.DataFrame):
    """두 화면의 대조와 알려진 값 확인, 수입액 커버리지."""
    tb, mn = rt[rt.source == "table"], rt[rt.source == "main"]
    for y in sorted(code.year.unique()):
        ct = set(code[(code.year == y) & (code.source == "table")].hs10)
        cm = set(mn[mn.year == y].hs10)
        if cm:
            logger.info("  %d년 코드: 관세율표 %d / 주요세율보기 %d (한쪽에만 %d)",
                        y, len(ct), len(cm), len(ct ^ cm))

    # 두 화면에 함께 나오는 기본세율(A)과 WTO 세율(C)이 같은가. 문구는 천 단위 쉼표가
    # 화면마다 달라('1218원'과 '1,218원') 숫자로 견준다.
    key = ["year", "hs10", "adval", "specific", "rate_txt"]
    for cd in ("A", "C"):
        a = tb[tb.rate_cd == cd].drop_duplicates(["year", "hs10"])[key]
        b = mn[mn.rate_cd == cd].drop_duplicates(["year", "hs10"])[key]
        m = a.merge(b, on=["year", "hs10"])
        if len(m):
            ok = (m.adval_x.fillna(-1) == m.adval_y.fillna(-1)) & (m.specific_x.fillna(-1) == m.specific_y.fillna(-1))
            logger.info("  %s 세율 두 화면 일치 %.2f%% (%d쌍)", cd, 100 * ok.mean(), len(m))
            if ok.mean() < 0.99:
                logger.warning("  어긋나는 예: %s", m[~ok].head(5).to_dict("records"))
    rt = rt[rt.source.isin(["table", "fill"]) | rt.rate_cd.str.startswith("F")]   # 적재 대상만 남긴다

    unread = rt[rt.adval.isna() & rt.specific.isna()]
    logger.info("  숫자로 못 읽은 세율 %d행 (%.3f%%) 예: %s", len(unread), 100 * len(unread) / max(len(rt), 1),
                unread.rate_txt.value_counts().head(5).to_dict())

    known = [  # (연도, 코드, 구분, 종가, 종량, 시작일)
        (2025, "0904210000", "W2", 270.0, 6210.0, None),
        (2009, "0910101000", "W2", 377.3, 931.0, None),
        (2025, "2103909050", "L", 45.0, None, None),
        (2025, "2103909050", "FCN1", 44.5, None, None),
        (2025, "2103909050", "FEU1", 5.6, None, date(2025, 1, 1)),
        (2025, "2103909050", "FEU1", 2.8, None, date(2025, 7, 1)),
        (2012, "8703231010", "C", 8.0, None, None),
        (2025, "0710807000", "C", 27.0, None, None),
    ]
    for y, h, cd, a, s, f in known:
        if y not in set(rt.year):
            continue
        hit = rt[(rt.year == y) & (rt.hs10 == h) & (rt.rate_cd == cd)]
        if f is not None:
            hit = hit[hit.valid_from == f]
        assert len(hit) == 1, f"{y} {h} {cd}: {len(hit)}행"
        r = hit.iloc[0]
        assert r["adval"] == a and (s is None or r["specific"] == s), f"{y} {h} {cd}: {r['rate_txt']}"
    logger.info("  알려진 값 확인 통과")

    if DB_TRADE.exists():
        con = duckdb.connect(str(DB_TRADE), read_only=True)
        try:
            imp = con.execute("SELECT yyyymm//100 AS year, hs10, SUM(imp_dlr) AS v FROM fact_trade "
                              "GROUP BY 1, 2").df()
        finally:
            con.close()
        have = code[["year", "hs10"]].assign(ok=1)
        m = imp.merge(have, on=["year", "hs10"], how="left")
        m = m[m.year.isin(code.year.unique())]
        cov = m.assign(ok=m.ok.fillna(0)).groupby("year").apply(lambda g: (g.v * g.ok).sum() / g.v.sum())
        logger.info("  수입액 중 그해 세율표에 코드가 있는 몫: %s",
                    ", ".join(f"{y} {100 * c:.2f}%" for y, c in cov.items()))


def load(code, rt, names, meta):
    """새 파일에 쓰고 바꿔 끼운다.

    DuckDB는 CREATE OR REPLACE로 표를 갈아도 파일이 줄지 않아 다시 적재할 때마다 커진다
    (한 번 다시 적재하자 27MB가 53MB가 됐다). 받은 기록(meta_fetch)만 옛 파일에서 옮겨 온다.
    """
    tmp = DB_OUT.with_name(DB_OUT.stem + ".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        con.register("code_df", code)
        con.register("rt_df", rt.drop(columns=["period", "rate_nm"]))
        con.register("nm_df", names)
        con.execute("""
            CREATE OR REPLACE TABLE tariff_code AS
            SELECT CAST(year AS INTEGER) AS year, hs10, name_ko, source FROM code_df ORDER BY year, hs10""")
        con.execute("""
            CREATE OR REPLACE TABLE tariff_rate AS
            SELECT CAST(year AS INTEGER) AS year, hs10, rate_cd, rate_txt,
                   CAST(adval AS DOUBLE) AS adval, CAST(specific AS DOUBLE) AS specific,
                   CAST(valid_from AS DATE) AS valid_from, CAST(valid_to AS DATE) AS valid_to, source
            FROM rt_df ORDER BY year, hs10, rate_cd, valid_from""")
        con.execute("CREATE OR REPLACE TABLE dim_rate_cd AS SELECT * FROM nm_df ORDER BY rate_cd")
        con.execute("""CREATE TABLE meta_fetch
                       (page VARCHAR, year INTEGER, ryu INTEGER, fetched_at TIMESTAMP, bytes INTEGER)""")
        if DB_OUT.exists():
            con.execute(f"ATTACH '{DB_OUT.as_posix()}' AS old (READ_ONLY)")
            has = con.execute("SELECT COUNT(*) FROM duckdb_tables() "
                              "WHERE database_name = 'old' AND table_name = 'meta_fetch'").fetchone()[0]
            if has:
                con.execute("INSERT INTO meta_fetch SELECT * FROM old.meta_fetch")
            con.execute("DETACH old")
        if meta:
            con.executemany("INSERT INTO meta_fetch VALUES (?, ?, ?, ?, ?)", meta)
        con.execute("COMMENT ON TABLE tariff_rate IS '관세법령정보포털 연도별 관세율표(기본·탄력·양허)와 "
                    "주요세율보기(FTA 일곱 상대). FTA는 적용 가능 세율이지 납부 세율이 아니다. 포털 고지상 법적 효력 없음'")
        for t in ("tariff_code", "tariff_rate", "dim_rate_cd"):
            logger.info("  %s %d행", t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    finally:
        con.close()
    os.replace(tmp, DB_OUT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=list(YEARS))
    ap.add_argument("--ryu", type=int, nargs="+", default=list(RYU), help="시험용: 일부 류만")
    ap.add_argument("--pages", nargs="+", default=list(PAGES), choices=list(PAGES))
    ap.add_argument("--delay", type=float, default=0.7, help="요청 간격(초)")
    ap.add_argument("--parse-only", action="store_true", help="받지 않고 캐시만 적재")
    args = ap.parse_args()

    meta = collect(args.years, args.ryu, args.pages, args.delay, args.parse_only)
    code, rt, names = build(args.years, args.ryu)
    logger.info("파싱: 코드 %d, 세율 %d행", len(code), len(rt))
    verify(code, rt)
    # 주요세율보기의 기본·WTO·아시아태평양 열은 관세율표와 겹치므로 대조에만 쓰고 FTA 열만 싣는다.
    # 관세율표 화면을 받지 못해 채운 코드(fill)는 예외다.
    rt = rt[rt.source.isin(["table", "fill"]) | rt.rate_cd.str.startswith("F")]
    load(code, rt, names, meta)
    logger.info("완료: %s", DB_OUT)


if __name__ == "__main__":
    main()
