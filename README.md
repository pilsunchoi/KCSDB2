# KCSDB2 — 관세청 무역통계 DB (재구축판)

관세청 OpenAPI(품목별 국가별 수출입실적(GW), 데이터 ID 15100475) 원본을 raw로 적재하고,
HS 개정·국가코드를 dim 계층에서 연결한 무역통계 DB.

- 데이터 범위: 2007.01–2026.03 (231개월), 240개국, HS10 15,403종, 27,533,937 거래행
- 설계 원칙: fact는 관세청 raw만. 파생·연결·참조는 전부 dim. 상세는 docs/DB_구축_원칙.md
- 검증: 04_validate PASS 9, WARN 4, INFO 1, FAIL 0 (2026-07-03)

---

## 이 패키지의 범위 (중요)

이 zip은 **코드·문서·외부참조자료·폴더구조**만 담는다. 실행 가능한 완결본이 아니다.
다음 세 요소가 빠져 있어, 이 패키지만으로는 DB를 재생성할 수 없다.

- **data/raw** — 원본 XML 56,852개. 로컬 수집 산출물. (재수집은 01_fetch_raw로 가능)
- **config/, scripts/utils/** — 인증키·API 호출 래퍼. v1 자산. (수집·프로브 실행에 필요)
- **data/interim, data/processed, logs** — 파이프라인 실행 산출물. (아래 실행순서로 생성)

DB를 처음부터 만들려면: data/raw 확보 → config·utils 배치 → 아래 실행순서.

---

## 파일별 설명

### scripts/

| 파일 | 설명 |
|------|------|
| 00_probe_update.py | 업데이트 시작점 판정. 로컬 raw 최신월 실측 + 대형국 프로브로 관세청 확정월 탐지(품목명세 유무로 잠정월 구분) |
| 02a_xml_to_parquet.py | raw XML → interim parquet 변환. 파생 컬럼 제거(raw 8필드만) + 관세청 국명 부산물 저장 |
| 02b_parquet_to_duckdb.py | parquet → DuckDB 적재. 스키마 검증(파생 컬럼 차단), 잠정월 제거, 적재 무결성 검증 |
| 03_build_dims.py | dim_country(관세청 국명+외교부 참조 병기, 나미비아 NA 처리) + dim_hs10(HS부호 파일, 발효일 실측) |
| 03b_build_hs6_concordance.py | HS6 개정 연계표 3종 PDF 추출 → dim_hs6_concordance. 다대다·폐지코드(deleted) 보존 |
| 03c_build_hs10_concordance.py | HS10 개정 연계 추정 → dim_hs10_concordance, dim_hs10_to_2022. 연도별 HSK 별표 PDF 파싱 → HS6 연계표로 후보 제한 → 품명 유사도 사전가중 → 이중비례조정(IPF) |
| 04_validate.py | 5계층 통합 검증(스키마/값/적재/dim/concordance). PASS·WARN·INFO·FAIL + 종료코드 |
| benchmark_queries.py | (진단 도구) 분석 쿼리 4종 성능 측정. 순수 연산시간(EXPLAIN ANALYZE) vs 결과 전송시간 분리. 데이터 갱신 시 재측정용 |

### docs/

| 파일 | 설명 |
|------|------|
| DB_구축_원칙.md | 재구축 설계 헌법. fact raw 원칙, dim 구조, HS6 연결 방침, 계층 분리, 검증 기준 |
| 세션_발견_노트.md | 데이터 함정 카탈로그 + 커버리지 확정치 + 과제 1·2·3 판정 봉인(0절). 책 교육사례 재료. **후속 세션 필수 업로드** |
| 후속세션_업로드_안내.md | 다음 세션에서 무엇을 올릴지 목적별 안내. DB 실물(676MB)은 업로드 불가, 로컬 쿼리 결과로 우회 |

### data/external/ (외부 참조자료)

DB 실물과 마찬가지로, 용량이 큰 원천 자료 일부는 저장소에 넣지 않고 내려받는 경로만 문서로 남긴다.

| 파일 | 설명 |
|------|------|
| 관세청_HS부호_20260101.xlsx | HS10 품목명·단위·발효일. dim_hs10 원천. 2026 유효코드만(폐지코드 없음) |
| 외교부_국가표준코드_20251222.csv | ISO2/3·영문명·한글명·대륙 3종. dim_country 참조. 나미비아 ISO2='NA' 주의 |
| HS연계표_2022to2007.pdf | HS6 개정 연계(2022↔2007). 6,592쌍. concordance 원천 |
| HS연계표_2022to2012.pdf | HS6 개정 연계(2022↔2012). 6,415쌍 |
| HS연계표_2022to2017.pdf | HS6 개정 연계(2022↔2017). 5,937쌍. deleted 1건(300219) 포함 |
| HSK_별표/ *(저장소 제외)* | 연도별 관세·통계통합품목분류표 별표 전문 PDF. HS10 코드+국문·영문 품명. dim_hs10_* 원천. 용량(56MB) 때문에 gitignore 처리했으므로 03c를 돌리려면 먼저 내려받아야 한다 — 받는 경로는 docs/DB_구축_원칙.md §3.4. 03c가 요구하는 것은 2011·2013·2015·2017·2021·2022년 여섯 개(39MB)이고, 2012·2014년 신구대비표와 2025·2026년 판은 참고용이다. **XLSX판은 앞자리 0을 잃어 9자리 표제와 10자리 코드가 뒤섞이므로 쓰지 말 것** |

### 루트

| 파일 | 설명 |
|------|------|
| requirements.txt | 재구축·수집·프로브 의존성 9종(Python 3.12: duckdb, pandas, pyarrow, openpyxl, pdfplumber, requests, python-dotenv, pyyaml, tqdm) |
| README.md | 이 문서 |

---

## 실행 순서

전제: kcsdb 환경(Python 3.12) 활성화, data/raw·config·utils 배치 완료.

```
python scripts\00_probe_update.py          # (선택) 신규 확정월 확인
python scripts\01_fetch_raw.py ...          # (신규월 있을 때만) 수집. v1 자산
python scripts\02a_xml_to_parquet.py        # XML → parquet
python scripts\02b_parquet_to_duckdb.py     # parquet → DuckDB (fact, meta)
python scripts\03_build_dims.py             # dim_country, dim_hs10
python scripts\03b_build_hs6_concordance.py # dim_hs6_concordance
python scripts\04_validate.py               # 통합 검증
python scripts\benchmark_queries.py         # (선택) 쿼리 성능 측정. 데이터 갱신 시 재확인
```

---

## 데이터 출처 및 라이선스 (배포 필수)

이 DB는 아래 공공데이터를 원천으로 한다. 두 자료(HS부호·HS연계표)는 KOGL 제1유형으로 출처표시가 의무이며, 위반 시 이용허락이 자동 종료된다. 재배포 시 이 절을 반드시 유지한다.

- **무역 실적**: 관세청_품목별 국가별 수출입실적(GW)(공공데이터포털 data.go.kr, 데이터 ID 15100475). OpenAPI 서비스 URL: `http://apis.data.go.kr/1220000/nitemtrade`. 출처: https://www.data.go.kr/data/15100475/openapi.do . 이용허락범위 제한 없음.
- **국가코드**: 외교부_국가표준코드(공공데이터포털 data.go.kr, 데이터 ID 15091117). 이용허락범위 제한 없음.
- **HS 품목명**: 관세청_HS부호(공공데이터포털 data.go.kr, 데이터 ID 15049722). 공공누리 제1유형(출처표시). 출처: 관세청, https://www.data.go.kr/data/15049722/fileData.do
- **HS 개정 연계표(HS6)**: 관세청 FTA 포털 HS연계표(HS2022→2007/2012/2017). 공공누리 제1유형(출처표시). 출처: 관세청 FTA 포털, https://www.customs.go.kr/ftaportalkor/
- **관세·통계통합품목분류표(HSK) 별표**: 기획재정부(현 재정경제부) 고시 「관세ㆍ통계통합품목분류표」의 별표 전문 및 신구대비표. 출처: 법제처 국가법령정보센터, https://www.law.go.kr/ . dim_hs10_concordance·dim_hs10_to_2022의 원천. 이 두 테이블은 고시 원문이 아니라 **원문에서 우리가 추정한 연계**이므로, 재배포 시 추정임을 함께 밝힌다.

**무역 실적 집계 기준 (관세청 정의).** 수출입 신고 통관 자료를 국가 및 HS Code(2·4·6·10단위)별로 집계한 국가별 품목별 무역통계다. 금액은 미화(USD)로, 수출은 FOB(신고금액)·수입은 CIF(과세가격) 기준이며, 중량은 순중량(kg)이다. 국가는 수출은 최종목적국, 수입은 원산국을 원칙으로 하고 무역통계부호상 ISO 코드로 분류한다. 단순 통과물품이나 일시 반입·반출 물품은 물적 자원의 증감이 없어 제외된다. 매월 수출입 신고의 정정·취하를 반영해 전월까지 자료를 현행화한다(주기 1개월).

본 저작물은 위 기관들이 공공누리 제1유형으로 개방한 저작물을 이용하였으며, 각 저작물은 위 출처에서 다운로드할 수 있다. 공공기관이 이 DB를 후원하거나 특수 관계에 있는 것으로 오인하게 하는 표시를 해서는 안 된다.

## 데이터 파일 배포 (DB 실물)

DB 실물(kcsdb.duckdb, ~681MB)은 GitHub 저장소 100MB 한도를 초과하므로 저장소에 포함되지 않는다. GitHub Releases의 첨부 파일로 배포한다. 학생은 저장소를 clone/다운로드한 뒤, Releases 페이지에서 압축된 DB를 별도로 받아 data/processed/ 에 놓는다. 상세는 docs/학생_사용안내.md 참조.

## DB 스키마

- **fact_trade** (거래): yyyymm, stat_cd, hs10, exp_dlr, imp_dlr, exp_wgt, imp_wgt, bal_payments
- **fact_total** (국가-월 총계): yyyymm, stat_cd, exp_dlr, imp_dlr, exp_wgt, imp_wgt, bal_payments
- **meta_calls** (수집이력): yyyymm, stat_cd, success, result_code, result_msg, item_count, response_bytes, elapsed_sec, timestamp
- **dim_country**: stat_cd(PK), name_ko_kcs, name_ko_mofa, name_en, iso2/3/num, continent_common/admin/mofa
- **dim_hs10**: hs10(PK), name_ko, name_en, unit_qty, unit_wgt, valid_from, sitc_like_code, sitc_like_name
- **dim_hs6_concordance**: hs2022, hs_past, past_version, relation(identity/mapped/deleted/new). **공표된 사실**(관세청 공식 연계표)
- **dim_hs10_concordance**: hs_from, hs_to, revision(2012/2017/2022), weight, score, relation(identity/moved). 개정 하나를 건너는 표. **추정**
- **dim_hs10_to_2022**: hs_past, past_version(2007/2012/2017), hs2022, weight, method(chain/hs6_fallback). 과거 체계를 현행 위로 한 번에 옮기는 표. **추정**

HS10 연계는 공식 승계표가 존재하지 않아 우리가 추정한 것이다. HS6 연계(공표된 사실)와 성격이 다르므로 테이블을 분리했다. 쓰는 법과 검증 결과는 docs/DB_구축_원칙.md §3.4 참조.

```sql
-- 과거 코드 체계를 현행(2022) 위로 옮겨 HS10 시계열을 잇는 예
SELECT c.hs2022, f.yyyymm, SUM(f.exp_dlr * c.weight) AS exp_dlr
FROM fact_trade f
JOIN dim_hs10_to_2022 c
  ON c.hs_past = f.hs10
 AND c.past_version = CASE WHEN f.yyyymm < 201201 THEN '2007'
                           WHEN f.yyyymm < 201701 THEN '2012' ELSE '2017' END
WHERE f.yyyymm < 202201 AND c.method = 'chain'   -- HS10 해상도가 필요하면 chain만
GROUP BY 1, 2;
-- 2022년 이후 자료는 이미 현행 체계이므로 변환 없이 UNION 한다.
```

분석(HS10→HS6 절단, 단가, margin 분해 등)은 DB 밖 분석 계층에서. DB는 raw+연결까지만 책임.

---

## 알려진 한계와 과제 판정 (2026-07-03)

한계는 원자료(관세청 공개자료) 한계이지 DB 결함이 아니다. 상세·봉인은 docs/세션_발견_노트.md 0절 참조.

- **hs10 커버리지 72.8%**: 폐지코드 4,194종은 2026 HS부호 파일에 없어 품목명 NULL. HS6 이하 해상도 분석에서는 불필요(hs6 절단으로 소거, concordance로 계열 식별). HS10 세분 분석에서만 문제. **2026-08-28: 확보 경로를 찾았다** — data.go.kr이 아니라 국가법령정보센터의 연도별 HSK 별표다. 4,194종 중 3,738종(89.1%, 폐지코드 거래액의 94.12%)의 품목명이 여기 있다. dim_hs10 반영은 미실시(출처가 관세청 파일이 아니라 기재부 고시라 열 분리 필요).
- **HS10 연계는 추정이다**: 공식 HS10 승계표가 없다. 위약 검정으로 잰 잡음 바닥은 2.06%(2022년 개정 기준)이고 그 개정이 옮긴 몫은 12.27%p다. `method='hs6_fallback'` 8,757행은 HS10이 아니라 HS6 해상도이며, 2011년 이전에 폐지된 코드(2007~2011년 수출액의 5.61%)가 여기 해당한다. 나뉜 비율은 개정 직후 6개월 수출액 분포에서 나온 조정값이지 관측값이 아니다.
- **concordance 커버리지**: 04_validate WARN(2007 66.1/2012 71.1/2017 80.8%)은 종수·가정적 상한. 시기별 올바른 버전+거래액 실측은 미매칭 0.004~0.007%. 완전 사각지대 130종, 거래액 0.004%. 거래액 가중 분석엔 무영향, 개수 기반 분석엔 종수 미매칭(124/63/34) 관여.
- **음수 중량 19행**: 관세청 사후 정정 원본. 보존. 분석 시 단가 계산에서 이상치 처리 필요.
- **쿼리 성능**: 인덱스 불필요 확정. self-join 등 순수 연산 초 단위 이내(EXPLAIN ANALYZE 0.9s). 대용량 결과의 파이썬 변환 비용은 인덱스로 해결 안 됨 — DuckDB 내 집계로 결과 축소 권장.

## DB는 특정 분석을 전제하지 않는다

fact는 raw, dim은 참조·연결뿐. margin·granularity·위기더미 등 분석 개념은 DB에 없다. 어떤 분석도 이 raw 위에서 동등하게 가능하다. 위 한계의 "무영향/관여" 판단은 분석 설계(거래액 가중 vs 개수 기반, HS6 vs HS10)가 정한다.
