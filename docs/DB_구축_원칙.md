# KCSDB 구축 원칙

작성일: 2026-07-03
대상: 관세청 무역통계 DB 재구축 (KCSDB2)
목적: 이 문서는 재구축의 설계 헌법이다. 모든 스크립트는 이 원칙을 따른다. 원칙과 코드가 충돌하면 코드를 고친다.

---

## 0. 최상위 원칙

DB는 관세청 원본을 최대한 그대로 담는다. 분석을 위한 가공은 DB가 아니라 분석 계층에서 한다.

이 원칙을 세 문장으로 압축한다.

1. fact 테이블은 관세청 API 응답 필드만 담는다. 파생 컬럼을 만들지 않는다.
2. 관측값을 변형하지 않는다. 관세청이 준 값을 그대로 저장한다.
3. 시계열 연결(HS 개정, 국가코드)과 참조 정보(국명, 품목명)는 dim 테이블에서 제공하되, fact는 건드리지 않는다.

---

## 1. 원본 XML 구조 (2026-07-03 실측 확인)

재구축의 모든 판단은 실제 XML 구조에 근거한다. 세 표본(202401_US 6,415행, 200701_US 5,532행, 202401_AD 7행)을 확인한 결과는 다음과 같다.

### 1.1 태그 구조

모든 item은 10개 태그를 가진다. 예외 없다.

```
balPayments, expDlr, expWgt, hsCd, impDlr, impWgt,
statCd, statCdCntnKor1, statKor, year
```

- `year` = "총계" → 총계 행 (국가-월 집계)
- `year` = "YYYY.MM" → 거래 행 (HS10 품목별)

### 1.2 결측 처리 (중요)

**관세청 API는 결측과 0을 구분하지 않는다.** 수치 태그(expDlr, impDlr, expWgt, impWgt, balPayments)는 항상 존재하며, 값이 없으면 `0`으로 채워져 온다. 세 표본 전체에서 결측 수치 태그는 0건이다.

따라서 "관측값 없으면 NULL, 0이면 0"이라는 구분은 이 데이터에서 원천적으로 불가능하다. 원본에 NULL이 존재하지 않기 때문이다. 관세청이 이미 "거래 없음 = 0"으로 인코딩한다.

**결론: 정수 캐스팅 `int(값)`은 손실이 아니다. 뭉갤 결측 자체가 없다.** 이전 KCSDB(v1)에서 `int(d.get("expDlr") or 0)`를 결측 손실로 지적했으나, 이는 철회한다. `or 0`은 도달하지 않는 방어 코드일 뿐이다.

### 1.3 0값 행의 보존

관세청은 다음 행들도 응답에 포함시킨다. 모두 raw로 보존한다.

- 수출만 0, 수입 양수 (또는 그 반대)
- **수출·수입 금액 모두 0인 거래 행** (202401_US 4건, 200701_US 1건 확인). 관세청이 이런 행을 왜 포함하는지는 불명이나, 원본에 있으므로 제거하지 않는다.
- `expWgt=0`인데 `expDlr>0`인 행 (202401_AD의 3304991000: expDlr=65, expWgt=0). 소액 거래에서 kg 미만이 반올림돼 중량이 0이 된 경우. **이 행은 분석 시 `단가=금액/중량` 계산을 불가능하게 만든다.** 그러나 이것은 분석 계층의 제약이지 DB의 결함이 아니다. DB는 (65, 0)을 그대로 보존한다.

### 1.4 총계 행의 특수성

총계 행은 `statCd=-`, `hsCd=-`, `statCdCntnKor1=-`, `statKor=-`로 온다. 국가코드가 응답에 없다.

- 총계 행의 `stat_cd`는 **파일명에서 추출**한다 (파생값). fact_total.stat_cd는 응답 원본(-)이 아니라 파일명 유래임을 문서에 명시한다.
- 이것이 유일하게 허용하는 "파생"이다. 총계 행에 국가코드를 붙이지 않으면 어느 국가의 총계인지 알 수 없기 때문이다.

### 1.5 후행 공백

초기 연도(200701)의 `statKor`에 후행 공백이 있다 (`"경주말 "`). 2024년에는 없다. `.strip()`은 dim 테이블에 저장할 때만 적용한다. 원본 XML은 그대로 보존한다.

---

## 2. fact 테이블 설계

### 2.1 fact_trade (거래 행)

관세청 API 응답 필드만. 파생 컬럼 없음.

| 컬럼 | 출처 | 비고 |
|------|------|------|
| yyyymm | 파일명 | 호출 시기. 응답의 year(YYYY.MM)와 일치 검증 |
| stat_cd | 응답 statCd | 관세청 원본. 변형 없음 |
| hs10 | 응답 hsCd | 관세청 원본. 변형 없음 (strip만) |
| exp_dlr | 응답 expDlr | 관세청 원본 |
| imp_dlr | 응답 impDlr | 관세청 원본 |
| exp_wgt | 응답 expWgt | 관세청 원본 |
| imp_wgt | 응답 impWgt | 관세청 원본 |
| bal_payments | 응답 balPayments | 관세청 원본. exp_dlr-imp_dlr과 항등이나 API가 주는 값이므로 보존 |

**제거하는 컬럼 (v1 대비):**
- `year`, `month` — yyyymm에서 파생. 만들지 않는다.
- `hs2`, `hs4`, `hs6` — hs10 슬라이싱 파생. dim_hs10에서 JOIN으로 얻는다.
- `stat_kor` — 국가 한국어명. dim_country에서 얻는다.
- `stat_kor_item` — 품목 한국어명. dim_hs10에서 얻는다.

**bal_payments 보존 판단:** exp_dlr-imp_dlr과 정확히 일치하는 파생값이다. 그러나 관세청 API가 원래 제공하는 필드이므로 raw 충실성 원칙에 따라 보존한다. "파생 컬럼을 만들지 않는다"는 원칙은 *우리가 계산해 넣는* 컬럼에 적용된다. 관세청이 준 것은 보존한다.

### 2.2 fact_total (총계 행)

| 컬럼 | 출처 | 비고 |
|------|------|------|
| yyyymm | 파일명 | |
| stat_cd | **파일명** | 응답은 `-`. 파일명 유래 파생 (유일 허용 파생) |
| exp_dlr | 응답 expDlr | |
| imp_dlr | 응답 impDlr | |
| exp_wgt | 응답 expWgt | |
| imp_wgt | 응답 impWgt | |
| bal_payments | 응답 balPayments | |

fact_total은 국가-월 집계만 담는다. HS 컬럼 없음.

---

## 3. dim 테이블 설계

fact가 raw인 대신, 모든 연결·참조·교정은 dim이 담당한다.

### 3.1 dim_country

관세청 stat_cd를 키로 하고, 외교부 표준명을 **참조로** 붙인다. fact는 어느 경우든 관세청 raw를 유지한다.

- `stat_cd` (PK) — 관세청 원본 코드
- `name_ko_kcs` — 관세청 응답 한국어명 (원본 보존)
- `name_ko_mofa`, `name_en`, `iso2`, `iso3`, `iso_num` — 외교부 참조
- `continent_*` — 대륙 참조

**교정 정책:** 관세청 코드가 외교부 표준에 매칭되지 않으면 참조 컬럼은 NULL로 둔다 ("없으면 없는 대로"). 관세청 고유 코드(EU 등)가 여기 해당한다. 국가명 오타 교정은 하지 않는다. 외교부 표준명을 **별도 컬럼으로 병기**하는 것으로 교정을 대신한다. 원본(name_ko_kcs)은 훼손하지 않는다.

### 3.2 dim_hs10

관세청 HS부호 파일(관세청_HS부호_YYYYMMDD.xlsx)로 품목명·단위·발효일을 채운다.

- `hs10` (PK)
- `description_ko`, `description_en` — 관세청 품목명
- `unit_qty`, `unit_wgt` — 수량단위코드, 중량단위코드
- `valid_from` — 적용시작일자 (실측). v1의 근사적 버전 태깅(1.1 일괄 가정)을 실측 발효일로 대체
- `first_yyyymm`, `last_yyyymm` — fact_trade에서 관측된 등장 범위

**한계:** 관세청 HS부호 파일(2026 기준)은 2026년 유효 코드만 담는다. 폐지 코드가 없다. 2007-2021에만 존재하다 폐지된 HS10은 이 파일과 매칭되지 않아 description이 NULL이 된다. 이는 "정직한 NULL" 정책으로 수용한다. 폐지 코드 품목명이 필요하면 과거 연도 HS부호 파일을 추가 확보해야 한다 (후속 과제).

### 3.3 dim_hs6_concordance (신규, 시계열 연결의 핵심)

HS 개정 간 대응을 담는다. 이것이 매 분석마다 반복할 수 없는 연결 작업을 DB 계층에서 한 번 해결하는 테이블이다.

- 단위: **HS6** (국제표준, 공식 연계표 공개됨)
- 출처: 관세청 FTA 포털 HS 연계표 (아래 3개 PDF, 2026-07-03 확보)
- 구조: (hs2022, hs_past, past_version) — **다대다 매핑**
- 용도: 개정을 걸친 HS6 시계열을 안정 코드로 연결

**확보한 자료 (data/external/):**
- HS연계표_2022to2007.pdf (154p, 6,592쌍, 변경 1,924)
- HS연계표_2022to2012.pdf (150p, 6,415쌍, 변경 1,422)
- HS연계표_2022to2017.pdf (139p, 5,936쌍, 변경 694)

출처 URL (fileKey 기반, 세션 만료 가능하므로 원본 보존):
- 2022→2007: nttFileDownload.do?fileKey=baee47db73be787e49c4e253fc2f5c23
- 2022→2012: nttFileDownload.do?fileKey=44117257ba1e58e075bc9b4ddb851897
- 2022→2017: nttFileDownload.do?fileKey=da55980b75d6a9f5020d6770db37acf7
- 페이지: customs.go.kr/ftaportalkor/cm/cntnts/cntntsView.do?mi=3397&cntntsId=1059

**형식:** PDF지만 2열(HS2022 6자리, 과거 6자리) 순수 코드쌍. pdfplumber로 페이지당 1표 정확 추출. 병합셀·다단헤더 없음.

**구조적 특성 — HS2022 허브 스타형:** 세 파일 모두 HS2022를 기준으로 과거 각 버전에 대응한다. HS2022→2007 파일이 가장 먼 구간을 덮으므로, 세 파일 결합 시 2007~2022 전 구간 연결이 가능하다. "2012↔2007 직접 대응이 없다"는 우려는 해소됨.

**다대다 관계 (핵심 제약):** 1:1 치환 불가능하다. HS2022→2007 기준, 하나의 2022 코드가 여러 2007 코드에 대응(분할 역방향) 301건, 여러 과거 코드가 한 2022 코드로 통합 574건. 예: 010130(2022) → 010110, 010190(2007) 둘 다 대응.
- **DB 계층:** concordance 테이블은 다대다를 그대로 보존한다. 규칙을 부과하지 않는다.
- **분석 계층:** 시계열 집계 시 연결 컴포넌트(같은 상품군으로 묶이는 코드 집합)를 계산해 처리한다. 이 처리는 DB 밖에서 한다.

**한계 — HS2022 스타형의 사각지대:** 2007에 있다가 HS2022에서 완전 소멸한 코드는 세 파일 어디에도 없을 수 있다(2022 대응점이 없어서). 재구축 시 fact의 HS6 중 세 연계표 어디에도 안 나타나는 코드를 목록화해 규모를 측정한다.

**HS10이 아니라 HS6인 이유:**
1. 공식 연계표가 HS6까지만 공개된다 (FTA 원산지 용도).
2. 경제 분석(수출 변동 분해, granularity, margin 분해)의 사실상 표준 해상도가 HS6다.
3. HS10 다대다 매핑은 자료 접근이 불확실하고 규칙 설계가 복잡하다. raw+최소연결 원칙에서 벗어난다.

**설계 결과:** fact는 HS10 raw를 보존하고, 개정 연결은 HS6 concordance로 별도 제공한다. 분석 시 HS10→HS6 절단 후 concordance로 시계열을 잇는다. 절단은 분석 계층에서 수행한다 (`SUBSTR(hs10,1,6)`).

---

## 4. 계층 분리 원칙

### DB 계층 (이 문서의 대상)
- raw 적재 (fact_trade, fact_total)
- 참조·연결 (dim_country, dim_hs10, dim_hs6_concordance)
- 내부 무결성 검증

### 분석 계층 (DB 밖, 별도 스크립트)
- HS10→HS6 절단
- 단가(금액/중량) 계산 및 wgt=0 처리
- extensive/intensive margin 분해
- price/quantity 분해 (HS6 셀 내부 이질성 문제는 여기서 다룬다)
- 위기 국면 더미, 행정부 비교
- Gabaix granularity

**분해 방법론 결함(HS6 셀 내 단가 이질성, uncovered 비중)은 분석 계층 사안이다. DB는 이를 해결하지 않는다. DB는 raw를 정확히 보존해 분석 계층이 올바르게 처리할 수 있게 한다.**

---

## 5. 무결성 검증 (재구축 후 필수)

1. fact_trade 금액 합 = fact_total 금액 (같은 yyyymm×stat_cd)
2. bal_payments = exp_dlr - imp_dlr (전 행)
3. yyyymm(파일명) = year 필드(YYYY.MM) 일치
4. 시간 연속성 (200701~최신월 빠진 월 없음)
5. fact.stat_cd → dim_country 매칭률 (미매칭 코드 목록화)
6. fact.hs10 → dim_hs10 매칭률 (폐지 코드로 인한 미매칭 목록화)
7. SUBSTR(hs10,1,6) → dim_hs6_concordance 커버리지

검증은 단일 스크립트(04_validate.py) 하나로 통합한다. v1처럼 검증을 여러 버전 파일로 분열시키지 않는다.

---

## 6. 재구축 시작점

원본 XML(data/raw, 56,852개)이 보존돼 있다. **API 재수집 불필요.** 02a(XML→parquet)부터 재설계한다.

수집 스크립트(01_fetch_raw, utils/api_client)는 검증된 상태이므로 재사용한다. 단 재수집은 하지 않는다.

---

## 7. 폴더 구조 (신규)

```
KCSDB2/
├── docs/
│   └── DB_구축_원칙.md          (이 문서)
├── scripts/
│   ├── 02a_xml_to_parquet.py    (재설계: 파생 컬럼 제거)
│   ├── 02b_parquet_to_duckdb.py (재사용 + 스키마 조정)
│   ├── 03_build_dims.py         (dim_country, dim_hs10)
│   ├── 03b_build_hs6_concordance.py (신규)
│   └── 04_validate.py           (통합 검증)
├── data/
│   ├── raw/                     (기존 XML 재사용, 심볼릭 링크 또는 복사)
│   ├── interim/                 (parquet)
│   ├── processed/               (kcsdb.duckdb)
│   └── external/                (HS부호 파일, 외교부 국가코드, HS6 연계표)
└── logs/
```

v1 스크립트 중 폐기 대상: 진단·일회성 스크립트 전부 (00_test_api, 03a_diagnose, diagnose_*, check_pre_explore, 06_decompose 버그판, 04_validate_v6_additions 등). 핵심만 재작성해 KCSDB2로 옮긴다.
