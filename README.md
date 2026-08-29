# KCSDB2 — 관세청 무역통계 DB (재구축판)

관세청 OpenAPI 원본을 raw로 적재하고, HS 개정·국가코드·분류 체계를 dim 계층에서 연결한 무역통계 DB.
원천은 둘이다. 품목별 국가별 월 실적(15100475)이 본체이고, 2026-08에 수출 10대 품목의
10일 단위 잠정치(15157908)를 더했다. 후자는 월 자료보다 훨씬 빠른 대신 국가도 중량도 없다.

<!-- DB_STATUS:START -->
- 데이터 범위: 월 실적 2007.01–2026.07 (235개월), 240개국, HS10 15,406종, 28,093,109 거래행
- 10일 단위 잠정치: 2016.01–2026.08, 10대 품목 + 총수출
- DB 파일: 약 713MB
<!-- DB_STATUS:END -->
- 설계 원칙: fact는 관세청 raw만. 파생·연결·참조는 전부 dim. 상세는 docs/DB_구축_원칙.md
- 검증: 04_validate PASS 9, WARN 4, INFO 1, FAIL 0 (최근 실행 2026-08-29). WARN은 설계상 예상된 불완전이고 FAIL이 0인 것이 요점이다

*위 수치는 `python scripts/06_db_status.py`가 DB를 읽어 자동으로 채운다. 손으로 고치지 않는다.*

---

## 이 패키지의 범위 (중요)

이 zip은 **코드·문서·외부참조자료·폴더구조**만 담는다. 실행 가능한 완결본이 아니다.
다음 세 요소가 빠져 있어, 이 패키지만으로는 DB를 재생성할 수 없다.

- **data/raw** — 원본 XML 5만여 개. 로컬 수집 산출물. (재수집은 01_fetch_raw로 가능)
- **config/, scripts/utils/** — 인증키·API 호출 래퍼. v1 자산. (수집·프로브 실행에 필요)
- **data/interim, data/processed, logs** — 파이프라인 실행 산출물. (아래 실행순서로 생성)

DB를 처음부터 만들려면: data/raw 확보 → config·utils 배치 → 아래 실행순서.

---

## 파일별 설명

### scripts/

| 파일 | 설명 |
|------|------|
| 00_probe_update.py | 업데이트 시작점 판정. 로컬 raw 최신월 실측 + 대형국 프로브로 관세청 확정월 탐지(품목명세 유무로 잠정월 구분) |
| 01_fetch_raw.py | 관세청 OpenAPI에서 (월 × 국가) 원본 XML 수집. 이미 받은 페어는 자동 skip. `--ym-from`·`--ym-to`로 월 범위를 제한한다 — **미확정월을 받으면 빈 응답이 성공으로 기록되어 그 달이 영구히 빈 채로 굳으므로**, 00이 알려 준 확정 상한을 반드시 넣는다 |
| 02a_xml_to_parquet.py | raw XML → interim parquet 변환. 파생 컬럼 제거(raw 8필드만) + 관세청 국명 부산물 저장 |
| 02b_parquet_to_duckdb.py | parquet → DuckDB 적재. 스키마 검증(파생 컬럼 차단), 잠정월 제거, 적재 무결성 검증. **DB 파일을 지우고 새로 만든다** — 재구축 전용이고 평소 갱신에는 02c를 쓴다 |
| 02c_reload_year.py | 해당 연도 fact 행만 교체. dim 열두 개와 fact_exp10d의 vintage 이력을 보존한 채 새 달을 더한다. 02b의 잠정월 제거 규칙을 그대로 따른다 |
| 03_build_dims.py | dim_country(관세청 국명+외교부 참조 병기, 나미비아 NA 처리) + dim_hs10(HS부호 파일, 발효일 실측) |
| 03b_build_hs6_concordance.py | HS6 개정 연계표 3종 PDF 추출 → dim_hs6_concordance. 다대다·폐지코드(deleted) 보존 |
| 03c_build_hs10_concordance.py | HS10 개정 연계 추정 → dim_hs10_concordance, dim_hs10_to_2022. 연도별 HSK 별표 PDF 파싱 → HS6 연계표로 후보 제한 → 품명 유사도 사전가중 → 이중비례조정(IPF). 03b 이후 실행 |
| 03d_build_hs10_name_hist.py | HS10 품명 이력 → dim_hs10_name_hist. 폐지코드 품목명(dim_hs10의 NULL)을 기재부 고시 별표에서 되찾는다 |
| 03e_build_nqi.py | 신성질별 분류 → dim_nqi, dim_hs10_to_nqi, dim_hs10_to_major10. HS 개정에 흔들리지 않는 공식 분류로 계열을 잇는다. 과거 코드는 dim_hs10_to_2022로 이어 붙이므로 03c 이후 실행 |
| 03f_build_workday.py | 상순·중순·하순 조업일수 → dim_workday10d. KASI 검증 공휴일 달력 사용. 10일 단위 자료를 견주려면 반드시 필요하다 |
| 03g_fetch_holidays.py | 공휴일 달력을 KASI 특일정보 API에서 받아 늘린다. `isHoliday='Y'`만 받고(24절기 등 쉬지 않는 날 제외), 기존 날짜는 건드리지 않고 없는 기간만 덧붙인다. **03f보다 먼저 실행한다** — 달력이 끝나는 지점부터는 조업일수를 낼 수 없다 |
| 05_fetch_exp10d.py | 수출 10대 품목 10일 단위 잠정치 수집 → fact_exp10d, v_exp10d_seg. 값이 바뀔 때만 새 행을 쌓아 개정 이력을 남긴다(vintage) |
| utils/api_client.py | 관세청 OpenAPI 호출 래퍼. 00·01 공유. 재시도·오류코드 판정 |
| utils/country_codes.py | 외교부 국가표준코드 CSV 로더. 00·01·03 공유 |
| utils/byeolpyo.py | HSK 별표 PDF 파서. 03c·03d 공유. 열 좌표 복원·앞자리 0 보존·쪽번호 제거 |
| 04_validate.py | 5계층 통합 검증(스키마/값/적재/dim/concordance). PASS·WARN·INFO·FAIL + 종료코드 |
| 06_db_status.py | DB를 읽어 README·index.html의 현황 수치 구간을 다시 쓴다. 행수·범위·개월수 같은 값을 손으로 적지 않기 위한 것이다. 갱신 절차의 마지막에 돌린다 |
| benchmark_queries.py | (진단 도구) 분석 쿼리 4종 성능 측정. 순수 연산시간(EXPLAIN ANALYZE) vs 결과 전송시간 분리. 데이터 갱신 시 재측정용 |

### docs/

| 파일 | 설명 |
|------|------|
| DB_구축_원칙.md | 재구축 설계 헌법. fact raw 원칙, dim 구조, HS6 연결 방침, 계층 분리, 검증 기준 |
| 세션_발견_노트.md | 데이터 함정 카탈로그 + 커버리지 확정치 + 과제 1·2·3 판정 봉인(0절). 책 교육사례 재료. **후속 세션 필수 업로드** |
| 후속세션_업로드_안내.md | 다음 세션에서 무엇을 올릴지 목적별 안내. DB 실물은 용량 때문에 업로드 불가, 로컬 쿼리 결과로 우회 |

### data/external/ (외부 참조자료)

DB 실물과 마찬가지로, 용량이 큰 원천 자료 일부는 저장소에 넣지 않고 내려받는 경로만 문서로 남긴다.

| 파일 | 설명 |
|------|------|
| 관세청_HS부호_20260101.xlsx | HS10 품목명·단위·발효일. dim_hs10 원천. 2026 유효코드만(폐지코드 없음) |
| 외교부_국가표준코드_20251222.csv | ISO2/3·영문명·한글명·대륙 3종. dim_country 참조. 나미비아 ISO2='NA' 주의 |
| 관세청_HSK별_신성질별_20260101.xlsx | HSK 10단위에 신성질별·성질별 분류를 붙인 표. dim_nqi·dim_hs10_to_nqi·dim_hs10_to_major10 원천. 공공데이터포털 15049720, 신청 불요·연간 갱신. **시트 둘의 열 이름이 다르다** — 2026년 시트만 `HS10단위부호`가 `국제적 상품분류체계(HS)10단위부호`다 |
| KASI_공휴일.csv | 한국천문연구원 특일정보로 전수 대조한 공휴일. dim_workday10d 원천. 달력이 끝나는 지점부터는 조업일수를 낼 수 없으므로 `03g_fetch_holidays.py`로 미리 늘려 둔다 |
| HS연계표_2022to2007.pdf | HS6 개정 연계(2022↔2007). 6,592쌍. concordance 원천 |
| HS연계표_2022to2012.pdf | HS6 개정 연계(2022↔2012). 6,415쌍 |
| HS연계표_2022to2017.pdf | HS6 개정 연계(2022↔2017). 5,937쌍. deleted 1건(300219) 포함 |
| HSK_별표/ *(저장소 제외)* | 연도별 관세·통계통합품목분류표 별표 전문 PDF. HS10 코드+국문·영문 품명. dim_hs10_* 원천. 용량(56MB) 때문에 gitignore 처리했으므로 03c·03d를 돌리려면 먼저 내려받아야 한다 — 받는 경로는 docs/DB_구축_원칙.md §3.4. 03c가 요구하는 것은 2011·2013·2015·2017·2021·2022년 여섯 개(39MB)이고, 2012·2014년 신구대비표와 2025·2026년 판은 참고용이다. **XLSX판은 앞자리 0을 잃어 9자리 표제와 10자리 코드가 뒤섞이므로 쓰지 말 것** |

### 루트

| 파일 | 설명 |
|------|------|
| requirements.txt | 재구축·수집·프로브 의존성 9종(Python 3.12: duckdb, pandas, pyarrow, openpyxl, pdfplumber, requests, python-dotenv, pyyaml, tqdm) |
| README.md | 이 문서 |

---

## 실행 순서

전제: kcsdb 환경(Python 3.12) 활성화, data/raw·config·utils 배치 완료.

**두 갈래를 구분한다.** 새 달을 더하는 것과 DB를 처음부터 만드는 것은 절차가 다르다.
`02b`는 **DB 파일을 지우고 다시 만들기 때문에** 새 달 몇 개를 더하려고 쓰면 dim 테이블
열두 개가 함께 사라진다. 더구나 `fact_exp10d`의 vintage 이력은 API가 현재 값만 주므로
원리상 복구할 수 없다. 갱신에는 `02c`를 쓴다.

### A. 새 달을 더할 때 (평소)

```
python scripts\00_probe_update.py            # 관세청 확정월이 어디까지인지 확인
python scripts\01_fetch_raw.py --year-from 2026 --year-to 2026 --ym-from 202608 --ym-to <확정상한>
python scripts\02a_xml_to_parquet.py --year 2026 --force
python scripts\02c_reload_year.py --year 2026 # 해당 연도 fact 행만 교체 (dim 보존)
python scripts\05_fetch_exp10d.py             # 10일 단위 잠정치 갱신
python scripts\04_validate.py                 # 통합 검증
python scripts\06_db_status.py                # 문서의 현황 수치 자동 갱신
```

`--ym-to`에 00이 알려 준 확정 상한을 넣는다. **미확정월을 받으면 빈 응답이 성공으로
기록되어 그 달이 영구히 빈 채로 굳는다.** 나중에 확정치가 올라와도 다시 받지 않는다.

dim은 다시 만들지 않는다. 국가·품목명은 외부 파일, HS 연계표는 별표 PDF, 조업일수는
KASI 달력에서 오므로 거래 행이 늘어난다고 달라질 것이 없다.

### B. 처음부터 만들 때 (재구축·스키마 변경)

```
python scripts\02a_xml_to_parquet.py         # XML → parquet (전 연도)
python scripts\02b_parquet_to_duckdb.py      # parquet → DuckDB. DB 파일을 새로 만든다
python scripts\03_build_dims.py              # dim_country, dim_hs10
python scripts\03b_build_hs6_concordance.py  # dim_hs6_concordance
python scripts\03c_build_hs10_concordance.py # dim_hs10_concordance, dim_hs10_to_2022
python scripts\03d_build_hs10_name_hist.py   # dim_hs10_name_hist
python scripts\03e_build_nqi.py              # dim_nqi, dim_hs10_to_nqi, dim_hs10_to_major10
python scripts\03g_fetch_holidays.py --to 2027 # 공휴일 달력 연장 (03f보다 먼저)
python scripts\03f_build_workday.py          # dim_workday10d
python scripts\05_fetch_exp10d.py --full     # fact_exp10d, v_exp10d_seg (2016~)
python scripts\04_validate.py                # 통합 검증
python scripts\06_db_status.py               # 문서의 현황 수치 자동 갱신
python scripts\benchmark_queries.py          # (선택) 쿼리 성능 측정
```

03c는 별표 PDF를 파싱하고 IPF를 돌려 무겁다. 그리고 `fact_exp10d`에 vintage가 쌓여
있다면 **02b를 돌리기 전에 parquet로 빼 두었다가 되돌려야 한다.**

---

## 데이터 출처 및 라이선스 (배포 필수)

이 DB는 아래 공공데이터를 원천으로 한다. 두 자료(HS부호·HS연계표)는 KOGL 제1유형으로 출처표시가 의무이며, 위반 시 이용허락이 자동 종료된다. 재배포 시 이 절을 반드시 유지한다.

- **무역 실적**: 관세청_품목별 국가별 수출입실적(GW)(공공데이터포털 data.go.kr, 데이터 ID 15100475). OpenAPI 서비스 URL: `http://apis.data.go.kr/1220000/nitemtrade`. 출처: https://www.data.go.kr/data/15100475/openapi.do . 이용허락범위 제한 없음.
- **국가코드**: 외교부_국가표준코드(공공데이터포털 data.go.kr, 데이터 ID 15091117). 이용허락범위 제한 없음.
- **HS 품목명**: 관세청_HS부호(공공데이터포털 data.go.kr, 데이터 ID 15049722). 공공누리 제1유형(출처표시). 출처: 관세청, https://www.data.go.kr/data/15049722/fileData.do
- **HS 개정 연계표(HS6)**: 관세청 FTA 포털 HS연계표(HS2022→2007/2012/2017). 공공누리 제1유형(출처표시). 출처: 관세청 FTA 포털, https://www.customs.go.kr/ftaportalkor/
- **관세청 수출 주요품목별 10일 단위 잠정치**: 공공데이터포털 15157908. fact_exp10d의 원천. 상순분 11일, 중순분 21일, 월 전체 익월 1일 공표. 10대 품목은 「현행 수출 성질별」 분류로 정의되며 응답에 품목 이름이 없어 itemUsdAmt 번호 순서를 실적 대조로 확정했다.
- **관세청 HSK별 신성질별·성질별 분류**: 공공데이터포털 15049720. dim_nqi·dim_hs10_to_nqi·dim_hs10_to_major10의 원천. 관세청이 정한 공식 대응이라 우리가 추정한 HS10 연계표와 성격이 다르다.
- **한국천문연구원 특일정보**: dim_workday10d의 원천. 공휴일 달력을 전수 대조해 만들었다.
- **관세·통계통합품목분류표(HSK) 별표**: 기획재정부(현 재정경제부) 고시 「관세ㆍ통계통합품목분류표」의 별표 전문 및 신구대비표. 출처: 법제처 국가법령정보센터, https://www.law.go.kr/ . dim_hs10_concordance·dim_hs10_to_2022·dim_hs10_name_hist의 원천. 앞의 두 테이블은 고시 원문이 아니라 **원문에서 우리가 추정한 연계**이므로 재배포 시 추정임을 함께 밝힌다. dim_hs10_name_hist는 고시 원문 그대로다.

**무역 실적 집계 기준 (관세청 정의).** 수출입 신고 통관 자료를 국가 및 HS Code(2·4·6·10단위)별로 집계한 국가별 품목별 무역통계다. 금액은 미화(USD)로, 수출은 FOB(신고금액)·수입은 CIF(과세가격) 기준이며, 중량은 순중량(kg)이다. 국가는 수출은 최종목적국, 수입은 원산국을 원칙으로 하고 무역통계부호상 ISO 코드로 분류한다. 단순 통과물품이나 일시 반입·반출 물품은 물적 자원의 증감이 없어 제외된다. 매월 수출입 신고의 정정·취하를 반영해 전월까지 자료를 현행화한다(주기 1개월).

본 저작물은 위 기관들이 공공누리 제1유형으로 개방한 저작물을 이용하였으며, 각 저작물은 위 출처에서 다운로드할 수 있다. 공공기관이 이 DB를 후원하거나 특수 관계에 있는 것으로 오인하게 하는 표시를 해서는 안 된다.

## 데이터 파일 배포 (DB 실물)

DB 실물(kcsdb.duckdb)은 GitHub 저장소 100MB 한도를 크게 초과하므로 저장소에 포함되지 않는다. GitHub Releases의 첨부 파일로 배포한다. 학생은 저장소를 clone/다운로드한 뒤, Releases 페이지에서 압축된 DB를 별도로 받아 data/processed/ 에 놓는다. 상세는 docs/학생_사용안내.md 참조.

## DB 스키마

- **fact_trade** (거래): yyyymm, stat_cd, hs10, exp_dlr, imp_dlr, exp_wgt, imp_wgt, bal_payments
- **fact_total** (국가-월 총계): yyyymm, stat_cd, exp_dlr, imp_dlr, exp_wgt, imp_wgt, bal_payments
- **meta_calls** (수집이력): yyyymm, stat_cd, success, result_code, result_msg, item_count, response_bytes, elapsed_sec, timestamp
- **dim_country**: stat_cd(PK), name_ko_kcs, name_ko_mofa, name_en, iso2/3/num, continent_common/admin/mofa
- **dim_hs10**: hs10(PK), name_ko, name_en, unit_qty, unit_wgt, valid_from, sitc_like_code, sitc_like_name
- **dim_hs6_concordance**: hs2022, hs_past, past_version, relation(identity/mapped/deleted/new). **공표된 사실**(관세청 공식 연계표)
- **dim_hs10_concordance**: hs_from, hs_to, revision(2012/2017/2022), weight, score, relation(identity/moved). 개정 하나를 건너는 표. **추정**
- **dim_hs10_to_2022**: hs_past, past_version(2007/2012/2017), hs2022, weight, method(chain/hs6_fallback). 과거 체계를 현행 위로 한 번에 옮기는 표. **추정**
- **dim_hs10_name_hist**: hs10, byeolpyo_year, name_ko, name_en. 별표 여섯 판본(2011·2013·2015·2017·2021·2022)의 품명 이력 72,129행. dim_hs10이 못 채운 폐지코드 품명을 여기서 얻는다. **기재부 고시 원문**
- **dim_nqi**: nqi5(PK), 대·중·소·세 계층 코드와 이름. 관세청 신성질별 758개. 2012년 제정 후 체계가 유지돼 HS 개정에 흔들리지 않는다. **관세청 공표**
- **dim_hs10_to_nqi**: hs10, nqi5, weight, method(direct/chain). 현행 코드는 공표 대응표 그대로, 폐지 코드는 dim_hs10_to_2022로 이어 붙였다. 거래액 커버리지 2007~2011년 95.3%, 이후 99% 이상
- **dim_hs10_to_major10**: hs10, item, weight, method. 10대 수출품목(반도체·승용차 등). **신성질별이 아니라 현행 수출 성질별** 기준이며 관세청 공표치와 대조해 확정했다
- **dim_workday10d**: base_ym, cutoff(10/20/99), seg(상순/중순/하순), days, workdays, holidays. **증분 구간**이지 누적이 아니다
- **fact_exp10d** (10일 단위 잠정치): base_ym, cutoff, priod_dt, item, exp_kusd, fetched_at. **누적치·천 달러 원본 그대로**. 같은 시점을 여러 번 관측하므로 값이 바뀔 때마다 새 행을 쌓는다
- **v_exp10d_seg** (뷰): 최신 관측만 남기고 누적을 구간 증분으로 바꾼다. seg_kusd=증분, cum_kusd=누적. 둘을 헷갈리면 cutoff=99가 월 전체가 아니라 하순 증분이 되어 값이 40%로 나온다

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

- **hs10 커버리지 72.8%**: 폐지코드 4,194종은 2026 HS부호 파일에 없어 품목명 NULL. **2026-08-28 해결** — `dim_hs10_name_hist`에서 3,738종(89.1%, 폐지코드 거래액의 94.12%)의 품명을 얻는다. `dim_hs10` 자체는 그대로 두었다(품명이 판본에 따라 변해 열 하나로 담을 수 없다 — 15,255종 중 4,806종(31.5%)이 판본별로 다른 품명을 갖는다). 남는 456종은 대부분 2011년 이전 폐지분이다.
- **HS10 연계는 추정이다**: 공식 HS10 승계표가 없다. 위약 검정으로 잰 잡음 바닥은 2.06%(2022년 개정 기준)이고 그 개정이 옮긴 몫은 12.27%p다. `method='hs6_fallback'` 8,757행은 HS10이 아니라 HS6 해상도이며, 2011년 이전에 폐지된 코드(2007~2011년 수출액의 5.61%)가 여기 해당한다. 나뉜 비율은 개정 직후 6개월 수출액 분포에서 나온 조정값이지 관측값이 아니다.
- **concordance 커버리지**: 04_validate WARN(2007 66.1/2012 71.1/2017 80.8%)은 종수·가정적 상한. 시기별 올바른 버전+거래액 실측은 미매칭 0.004~0.007%. 완전 사각지대 130종, 거래액 0.004%. 거래액 가중 분석엔 무영향, 개수 기반 분석엔 종수 미매칭(124/63/34) 관여.
- **음수 중량 19행**: 관세청 사후 정정 원본. 보존. 분석 시 단가 계산에서 이상치 처리 필요.
- **쿼리 성능**: 인덱스 불필요 확정. self-join 등 순수 연산 초 단위 이내(EXPLAIN ANALYZE 0.9s). 대용량 결과의 파이썬 변환 비용은 인덱스로 해결 안 됨 — DuckDB 내 집계로 결과 축소 권장.

## DB는 특정 분석을 전제하지 않는다

fact는 raw, dim은 참조·연결뿐. margin·granularity·위기더미 등 분석 개념은 DB에 없다. 어떤 분석도 이 raw 위에서 동등하게 가능하다. 위 한계의 "무영향/관여" 판단은 분석 설계(거래액 가중 vs 개수 기반, HS6 vs HS10)가 정한다.
