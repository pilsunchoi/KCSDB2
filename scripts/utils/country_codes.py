"""
국가코드 dim 테이블 빌더
=============================

외교부 국가표준코드 CSV (data.go.kr/data/15091117) 를 읽어
프로젝트에서 사용할 정규화된 dim_country 테이블을 생성한다.

학생 교훈
--------
함정: pandas 의 read_csv 는 문자열 "NA" 를 결측치(NaN) 로 자동 변환한다.
      나미비아의 ISO2 = NA 가 사라진다.
해결: keep_default_na=False, na_values=[''] 로 명시적 처리.
"""

from pathlib import Path
import pandas as pd


def load_mofa_country_codes(csv_path: Path) -> pd.DataFrame:
    """
    외교부 국가표준코드 CSV 를 읽어 정규화된 DataFrame 반환.

    Parameters
    ----------
    csv_path : Path
        외교부 CSV 경로 (예: data/external/country_codes_mofa_*.csv)

    Returns
    -------
    pd.DataFrame
        columns: iso2, iso3, iso_num, continent_en, continent_kr,
                 continent_mofa, name_en, name_kr
    """
    # keep_default_na=False : "NA" → NaN 자동 변환 방지 (나미비아 보호)
    # na_values=[''] : 진짜 빈 문자열만 NaN 으로 처리
    df = pd.read_csv(
        csv_path,
        encoding="utf-8-sig",   # BOM 제거
        keep_default_na=False,
        na_values=[""],
    )

    # 컬럼명 영문화 (DB 표준)
    column_map = {
        "국제표준화기구_2자리": "iso2",
        "국제표준화기구_3자리": "iso3",
        "국제표준화기구_숫자": "iso_num",
        "대륙명_공통 대륙코드": "continent_en",
        "대륙명_행정표준코드": "continent_kr",
        "대륙명_외교부 직제": "continent_mofa",
        "영문명": "name_en",
        "한글명": "name_kr",
    }
    df = df.rename(columns=column_map)

    # 자료형 정리
    df["iso2"] = df["iso2"].astype(str).str.strip().str.upper()
    df["iso3"] = df["iso3"].astype(str).str.strip().str.upper()
    df["iso_num"] = pd.to_numeric(df["iso_num"], errors="coerce").astype("Int64")

    # 검증
    assert df["iso2"].str.len().eq(2).all(), "ISO2 자릿수 오류"
    assert df["iso2"].is_unique, "ISO2 중복"

    return df


def get_iso2_list(csv_path: Path) -> list[str]:
    """본격 수집에서 사용할 ISO2 코드 리스트 반환."""
    df = load_mofa_country_codes(csv_path)
    return sorted(df["iso2"].tolist())


if __name__ == "__main__":
    # 단독 실행 시 검증
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    csv_path = PROJECT_ROOT / "data" / "external" / "country_codes_mofa_20251222.csv"

    df = load_mofa_country_codes(csv_path)
    print(f"Loaded {len(df)} rows")
    print(f"Columns: {df.columns.tolist()}")
    print()
    print("Continent distribution:")
    print(df["continent_en"].value_counts())
    print()
    print("Major Korean trade partners check:")
    for code in ["US", "CN", "JP", "DE", "VN", "TW", "GB", "HK", "SG", "IN", "NA"]:
        row = df[df["iso2"] == code]
        if not row.empty:
            kr = row["name_kr"].values[0]
            print(f"  {code} : {kr}")
        else:
            print(f"  {code} : NOT FOUND")
