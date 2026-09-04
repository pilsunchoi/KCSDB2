# NOTICE — 자료에 관한 고지

[`LICENSE`](LICENSE)(MIT)는 **이 저장소의 코드와 문서에만** 적용된다.
아래 원자료는 그 대상이 아니며 각 제공기관의 조건을 따른다.

## 원자료

| 자료 | 제공 | 조건 |
|---|---|---|
| 품목별 국가별 수출입실적, 성질별·신성질별 실적, 10일 단위 잠정치, HS부호, HSK별 분류 | 관세청 (공공데이터포털) | 공공누리 — **출처표시 의무** |
| HS 개정 연계표(HS6) | 관세청 FTA 포털 | 공공누리 — **출처표시 의무** |
| 국가표준코드 | 외교부 (공공데이터포털) | 이용허락범위 제한 없음 |
| 관세·통계통합품목분류표(HSK) 별표 | 기획재정부 고시 / 국가법령정보센터 | 공공저작물 |
| 특일정보(공휴일) | 한국천문연구원 (공공데이터포털) | 공공누리 |
| 광공업생산지수, 경기종합지수 | 국가데이터처 (KOSIS) | 공공누리 |

**공공누리 제1유형은 출처표시가 의무이고, 빠뜨리면 이용허락이 자동으로 종료된다.**
자료별 데이터셋 번호와 URL은 README의 「데이터 출처 및 라이선스 (배포 필수)」 절에 있다.
**재배포할 때 그 절을 그대로 유지해야 한다.**

공공기관이 이 DB를 후원하거나 특수 관계에 있는 것으로 오인하게 하는 표시를 해서는 안 된다.

## 배포되는 DB 파일

DB 실물(`kcsdb.duckdb`)은 이 저장소에 없고 GitHub Releases로 배포된다.
그 파일도 이 라이선스의 대상이 아니며, 위 원자료의 조건을 그대로 따른다.

## 우리가 추정한 것

`dim_hs10_concordance`와 `dim_hs10_to_2022`는 **공표된 연계표가 아니다.**
HS10 해상도의 공식 승계표가 존재하지 않으므로 이 저장소가 기재부 고시 별표에서
추정한 연결이며, 관세청·기획재정부의 공식 판단이 아니다.
`dim_hs10_to_nqi`는 현행 코드에는 관세청 공표 대응(`method='direct'`)을 쓰지만
옛 코드는 위 추정을 거치므로(`method='chain'`) 그 오차를 물려받는다.

만든 방법과 한계는 대시보드의 「HS 연계」 탭과 [`docs/DB_구축_원칙.md`](docs/DB_구축_원칙.md) §3.4에 있다.
**재배포하거나 인용할 때는 추정임을 함께 밝힌다.**

---

This NOTICE accompanies the MIT licence in `LICENSE`, which covers the source code and
documentation only. The underlying statistical data remains subject to the terms of its
providers — chiefly the Korea Customs Service, the Ministry of Foreign Affairs, the
Ministry of Economy and Finance, the Korea Astronomy and Space Science Institute, and
Statistics Korea / MODS. Several are released under the Korea Open Government License
Type 1, which makes attribution mandatory. The HS10 concordances are this repository's
own estimate, not an official determination.
