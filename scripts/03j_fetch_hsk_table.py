"""
03j_fetch_hsk_table.py — 연도별 HSK 품목분류표 수집 (관세법령정보포털)

무엇을 푸는가:
  10단위 연계표(dim_hs10_to_2022)는 연도별 별표를 견주어 만든다. 그런데 국가법령정보센터에는
  2011년보다 옛 고시가 없어, 2011년 이전에 사라진 코드 414개는 사슬에 들어오지 못하고
  HS6 해상도로만 이어져 있다(method='hs6_fallback'). 그 몫이 2007~2011년 수출의 5.60%,
  1,219억 달러다. 개정 달력에도 2008·2009년 개정이 문서 없이 짐작으로만 잡혀 있다.

  관세법령정보포털은 한국의 연도별 관세율표를 2002년부터 갖고 있고, 10단위 코드와 국·영문
  품명을 함께 준다. 국가법령정보센터의 별표 전문과 같은 내용이다. 이것으로 두 구멍을 메운다.

받는 방법:
  POST https://unipass.customs.go.kr/clip/hsinfosrch/openULS0201005Q.do
  hsfdCd에 류(2자리)를 주면 그 류 전체가 한 번에 온다. 호(4자리) 단위로 받으면 연도당
  1,200번이 필요하지만 류 단위면 97번이면 된다. 부(sctCd)는 응답에 영향을 주지 않는다.

  공개 포털이므로 요청 간격을 둔다(--delay, 기본 0.7초). 받은 HTML은 캐시에 남겨
  다시 받지 않고 파싱만 다시 할 수 있게 한다.

입력: 없음 (포털에서 받는다)
출력:
  data/raw/clip_hsk/<연도>/<류>.html   원본 캐시 (gitignore)
  data/external/HSK_별표/HSK_별표_<연도>.csv   code, leaf, path, name_en

검증:
  그해 거래 자료에 나오는 코드가 그해 표에 있는지 센다. 표가 온전하면 대부분 들어맞아야
  한다. 개정 시행 전후 잔여 통관 때문에 완전히 100%가 되지는 않는다.

실행:
  python scripts\\03j_fetch_hsk_table.py --years 2007 2008 2009 2010
  python scripts\\03j_fetch_hsk_table.py --years 2009 --parse-only   # 캐시에서 다시 파싱만
"""

from __future__ import annotations

import argparse
import html
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw" / "clip_hsk"
OUT_DIR = PROJECT_ROOT / "data" / "external" / "HSK_별표"
DB_PATH = PROJECT_ROOT / "data" / "processed" / "kcsdb.duckdb"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = LOG_DIR / f"fetch_hsk_table_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

URL = "https://unipass.customs.go.kr/clip/hsinfosrch/openULS0201005Q.do"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://unipass.customs.go.kr/clip/index.do",
    "Content-Type": "application/x-www-form-urlencoded",
}
RYU = range(1, 100)   # 류 01~99. 없는 류는 빈 응답이 온다.


def fetch_ryu(sess: requests.Session, year: int, ryu: int) -> str:
    data = dict(cntyCd="KR", aplyYy=str(year), cntyNm="한국", compareCrrspndNation="KR",
                sctYear="20070101", hstdYear="20070101", manlOrgnTpcd="01", tabTpcd="3",
                sctCd="01", hstdCd=f"{ryu:02d}", hsfdCd=f"{ryu:02d}", searchVal=f"{ryu:02d}")
    r = sess.post(URL, data=data, timeout=60)
    r.raise_for_status()
    return r.text


def parse(page: str) -> list[tuple[str, str, str, str]]:
    """표에서 (10자리 코드, 잎 품명, 계층 경로 품명, 영문 품명)을 뽑는다.

    화면의 품목번호는 4자리·2자리·4자리 세 칸으로 나뉜다. 뒤 칸이 빈 행은 호(4자리)나
    소호(6자리)의 표제이므로, 그것을 지나가며 쌓아 두었다가 10단위 행에서 경로로 붙인다.
    `scripts/utils/byeolpyo.py`가 PDF에서 만드는 것과 같은 (code, leaf, path, name_en)이다.

    경로의 깊이가 PDF판(류까지 올라감)보다 얕지만 문제되지 않는다. 이 표를 쓰는 개정들은
    세계관세기구 개정이 아니어서 후보가 같은 HS6 안으로 제한되고, 그러면 경로는 후보마다
    같은 값이라 판별에 기여하지 않는다. 가르는 일은 잎 품명이 한다.
    """
    out, h4, h6 = [], "", ""
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        c = [html.unescape(re.sub(r"<[^>]+>", " ", x)).replace(chr(0xa0), " ").strip() for x in tds]
        if len(c) < 4 or not re.fullmatch(r"\d{4}", c[0]):
            continue
        ko = re.sub(r"\s+", " ", c[3])
        en = re.sub(r"\s+", " ", c[4]) if len(c) > 4 else ""
        if not c[1]:                       # 호 표제
            h4, h6 = ko, ""
        elif not c[2]:                     # 소호 표제
            h6 = ko
        elif re.fullmatch(r"\d{2}", c[1]) and re.fullmatch(r"\d{4}", c[2]):
            path = " ".join(x for x in (h4, h6) if x)
            out.append((c[0] + c[1] + c[2], ko, path, en))
    return out


def collect(year: int, delay: float, parse_only: bool) -> pd.DataFrame:
    cache = RAW_DIR / str(year)
    cache.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers.update(HEADERS)

    rows, empty = [], []
    for ryu in RYU:
        f = cache / f"{ryu:02d}.html"
        if f.exists():
            page = f.read_text(encoding="utf-8")
        elif parse_only:
            continue
        else:
            page = fetch_ryu(sess, year, ryu)
            f.write_text(page, encoding="utf-8")
            time.sleep(delay)
        got = parse(page)
        if got:
            rows.extend(got)
        else:
            empty.append(ryu)
        if ryu % 20 == 0:
            logger.info("  %d년 제%02d류까지 %d개", year, ryu, len(rows))

    df = pd.DataFrame(rows, columns=["code", "leaf", "path", "name_en"]).drop_duplicates("code")
    logger.info("%d년: %d개 (빈 류 %s)", year, len(df),
                ",".join(f"{r:02d}" for r in empty) or "없음")
    return df


def verify(year: int, df: pd.DataFrame):
    """그해 거래 자료의 코드가 표에 들어 있는지 센다."""
    if not DB_PATH.exists():
        return
    import duckdb

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        traded = con.execute(
            "SELECT DISTINCT hs10 FROM fact_trade WHERE yyyymm BETWEEN ? AND ?",
            [year * 100 + 1, year * 100 + 12]).df()["hs10"]
    finally:
        con.close()
    if traded.empty:
        return
    have = set(df["code"])
    hit = traded.isin(have).sum()
    logger.info("  검증 %d년 거래코드 %d개 중 표에 있는 것 %d개 (%.2f%%)",
                year, len(traded), hit, 100.0 * hit / len(traded))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", required=True)
    ap.add_argument("--delay", type=float, default=0.7, help="요청 간격(초)")
    ap.add_argument("--parse-only", action="store_true", help="받지 않고 캐시만 파싱")
    args = ap.parse_args()

    for y in args.years:
        df = collect(y, args.delay, args.parse_only)
        if df.empty:
            logger.warning("%d년: 받은 것이 없다", y)
            continue
        p = OUT_DIR / f"HSK_별표_{y}.csv"
        df.sort_values("code").to_csv(p, index=False, encoding="utf-8-sig")
        logger.info("  저장 %s", p.name)
        verify(y, df)


if __name__ == "__main__":
    main()
