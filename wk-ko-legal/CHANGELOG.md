# Changelog

## 2.0.0 — 2026-06-29

lbox.kr 전면 개편(2026.6, "AI 업무 환경" 전환) 대응 — lbox 연동 3종 스킬 갱신. 검색 진입·본문 URL·DOM 구조가 모두 바뀌어 개편 전 방식(직접 검색 URL, 본문 `<script>` 페이로드 복원, 숫자 statute 코드)은 폐기. 모든 변경은 라이브 검증.

- lbox-case-search: 검색이 직접 URL(`/v2/search/case` — 폐지·404) → 홈 컴포저 "검색" 모드 → 작업(Task) + 결과 패널로 전환. 결과 카드 `a[data-track-props]`(docId·rank, 결과 배지·인용/조회수). 본문 URL `/v2/case/…` → `/precedent/{법원}/{사건번호}`, `data-node-id`(topheader/before/issue/summary/main/judges) 직접 추출 — 본문이 비활성 탭에서도 정상 렌더되어 `<script>` 복원 폐기, 단 전 단락 2벌 렌더라 node-id dedup 필수. 상급심 추적은 우측 사이드바 "상·하위 판결"(`upperLowerCaseItem`)·"인용된 판례". 번호 페이지네이션.
- lbox-commentary-search: 결과 패널의 "주석·실무서" 탭(`role="tab"` 버튼 — 좌측 내비 `/book/list` 링크와 구분) 기반. 카드 documentType `scholar`, 본문 URL `/book/{bookId}?tocId=…&nodeId=…&volumeHistoryId=…`. 섹션 추출은 종전 `data-node-id`→`data-viewer-type="header"` 형제 수집 유지(2벌 dedup·큰 챕터 키워드 발췌 추가). **숫자 `statute` 코드 폐지** → 법령 이름·문서유형 필터로 대체: `references/statute-map.md` 삭제, `references/filter-map.md` 신설.
- lbox-case-progress: 핵심 API `/api/caseManage/caseEvents`는 그대로 동작(라이브 32건 검증). 개편 드리프트 3종 수정 — ① 2단계 컨텍스트 페이지 `/v2/case-events/list`(폐지·404) → `/project?tab=case-schedule`, 로그인 판정을 페이지 렌더가 아니라 `fetch_cases.js`의 `ok:false`(API) 기준으로 일원화; ② 오류 플래그 필드명 `caseNotExist`/`errorOccurred` → `isCaseNotExist`/`isErrorOccurred`(구명 하위호환 유지) — 종전 errorFlag가 항상 false였던 버그 수정; ③ 개별 사건 딥링크 폐지로 `detailPageUrl`을 사건일정 목록 페이지(`/project?tab=case-schedule`)로 통일. Python 스크립트·정규화 스키마 불변.
- plugin.json 2.0.0(메이저 — lbox 사이트 개편 대응 대규모 갱신). 스킬 수·구성 변동 없음(9 skills). ko-* 서면·자문·법령 API 스킬 6종은 변경 없음.

## 1.4.0 — 2026-06-11

행정·형사 스킬 확장 — 신규 2종 추가(7→9 skills), 확장계획서(wk-ko-legal-행정형사-스킬확장계획-20260610) 기반. 인용 조문은 전부 국가법령정보 API 실측 검증(행정소송법 MST 285913, 행정심판법 249041, 행정절차법 239291, 행정기본법 269955, 형법 284025, 형사소송법 269945).

- 신규 ko-administrative-drafting: 행정쟁송 서면 작성·검토(원고·청구인 측 — 행정청 측은 추후 확장). 행정소송 소장·준비서면, 행정심판청구서, 집행정지 신청서(행소법 §23 "회복하기 어려운 손해" / 행심법 §30 "중대한 손해" 구분), 의견제출서·이의신청서(행정기본법 §36). 선결 게이트(처분성·적격·기간·전치), 처분시법 get-asof 의무화, 위법사유 2축(절차/실체) 논증구조. references 8종
- 신규 ko-criminal-drafting: 형사 서면 작성·검토. 변호인의견서(수사), 영장심사·구속적부심·보석(필요적 보석 §95 골격), 변론요지서(자백/부인/일부 인부 노선 게이트), 항소·상고이유서(20일 기간 §361의3·§379, 상고이유 제한 §383 게이트), 고소장·고발장(모드 D — 입장 반전 분리). 행위시법 get-asof 의무화(형법 §1), 증거 인용 표기 "증거순번 ○, 증거기록 [○권] ○면" 고정, 양형기준 2단계 확인(로컬 json/md → 양형위 웹사이트), 적법 변호 활동 한정 명시. references 8종
- ko-legal-writing-plan 확장: 2단원 분류 (A)민사/(B)자문 → +(C)행정/(D)형사, references 신설 2종(administrative-pleadings — 처분분석보고서·절차 선택 설계, criminal-defense — 기록분석보고서·인부 노선 설계), 산출물유형에 처분분석보고서·기록분석보고서 추가, 6단원 핸드오프에 신규 2종 추가, description의 "형사·행정쟁송 제외" 경계 삭제. trigger-eval 행정·형사 케이스 갱신(기존 false 2건 → true)
- 경계 수정: ko-civil-litigation-drafting·ko-legal-advisory-drafting의 description과 "대상이 아닌 작업"에 신규 스킬 포인터 부기("의견서" 트리거는 자문=의뢰인 제출 / 형사=수사기관·법원 제출로 식별)
- plugin.json 1.4.0 (9 skills), README 스킬 표·운용 메모 갱신

## 1.3.0 — 2026-06-10

SKILL.md 비대화 검토 보고서(2026-06-10) 권고 P1~P4 반영 — 구조 개편(기능 변경 없음, 기술 내용은 전부 이동·보존).

- [P1] lbox 검색 2종 본문 계층화(progressive disclosure):
  - lbox-case-search 452줄 → 248줄, lbox-commentary-search 402줄 → 205줄
  - 신설 references: extraction.md(카드·본문·링크 추출 JS와 DOM 구조 — 원문 그대로 이동), report-format.md(보고서 템플릿·한계 표기 형식), troubleshooting.md(증상별 대응표), statute-map.md(commentary 매핑표)
  - 본문에는 워크플로·정책(상급심 의무 조회, 재검색 정책, 한계 양처 명시, 금지사항)과 "어느 시점에 어떤 reference를 읽을지" 포인터만 유지
- [P2] description 압축: writing-plan 831→570자, ko-law-api 811→503자, advisory 746→614자 (트리거·경계 의미 보존, 1024자 한도 잔여 확보)
- [P3] ko-law-api 5.2 과거법령 레시피를 api_reference 6-C·7장으로 일원화(본문에는 명령·선택 규칙만)
- [P4] tools/build.py 비대화 감시 추가: description 1024자 한도 검사(오류) + 본문 400줄 초과 경고(WARN)

## 1.2.1 — 2026-06-10

행정규칙 연혁 조회 정정 — 공식 활용가이드(행정규칙 목록/본문 조회 API) 대조 결과 반영.

- 행정규칙 목록 조회의 `nw`(1 현행, 2 연혁) 파라미터 확인(라이브 검증: 전자금융감독규정 연혁 47건, 1.2.0 체인 발견분 전부 포함). 1.2.0의 "행정규칙은 연혁 검색 API가 없다" 전제는 nw 미지정 실측에 따른 오류 — 자치법규(v2 계획)와 동일 패턴의 누락이었음
- `versions/get-asof --target admrul`: `--query`(현행+nw=2 병합 직접 검색)를 기본·권장 경로로 신설, `--id`(신구법비교 체인 역추적)는 교차 검증용 보조 경로로 유지. 검색 응답의 행정규칙종류를 인용 표기에 사용(예: "고시 제2025-4호로 개정되기 전의 것")
- _search_pages 항목 태그 일반화(<law>/<admrul>), SKILL.md 5.4·api_reference 6-C 정정(nw·knd·prmlYd·modYd, 본문 응답의 별표 PDF·첨부파일 링크 필드)
- 회귀 테스트: nw 경로와 체인 경로의 선택본 일치(@2024-12-25 → 시행 20241224), 법령·자치법규 경로 영향 없음

## 1.2.0 — 2026-06-10

ko-law-api 과거(연혁) 법령 조회 기능 추가 — 행위시(형사)·처분시(행정)·법률행위시(민사) 기준의 시점본 조회. 기능계획 v2 + 공식 OPEN API 활용가이드 8건 대조 + 전 항목 라이브 검증 기반.

- law_api.py:
  - 신규 명령 `versions`(시점별 버전 목록)·`get-asof`(기준일 시행본 일괄 조회 — 선택 로직 내장, max{시행일자 ≤ 기준일}, 선택 결과·연혁본 표지·시행기간·판례식 구법 표기를 stderr 헤더로 출력)
  - target 추가: `eflaw`(시행일 법령 — LID 한정·nw 필터), `admrulOldAndNew`(행정규칙 신구법비교)
  - 자치법규 연혁: ordin `--nw 2` 지원(현행+연혁 병합), `--sborg` 추가, 명칭 변경·다중 명칭 경고
  - 행정규칙 체인 역추적(라이브 검증 발견): 신구법비교 구조문의 일련번호로 과거 전문 조회·체인 반복 — `versions/get-asof --target admrul`(기본 상한 15단계)
  - 함정 차단: eflaw 본문에 ID 사용 시(현행 반환·efYd 무시) 오류 처리, 제정 전 기준일 오류+최초 시행일 안내, JO는 law 전용
- SKILL.md: 신설 5장 "과거(연혁) 법령 조회 — 행위시·처분시 기준"(기준일 결정 정책: 자료에서 명백하면 스킬이 1차 판단·근거 명시, 불분명할 때만 사용자 확인 / 법령·자치법규·행정규칙 워크플로 / 구법 인용 표기), 기존 5~9장 → 6~10장, description에 과거 법령 트리거 추가, 오류 처리·협업 단원 보강
- api_reference.md: 6-C 신설(eflaw 파라미터·ID 함정·선택 규칙, ordin nw=2 — v1.1.0까지의 "자치법규 연혁 미지원" 기재 정정, admrulOldAndNew 체인, lsHistory 부존재, oldAndNew는 2단계 보류), 과거 법령 레시피 추가
- 결정사항 확정: 버전 선택은 스크립트 내장 자동, 법령 신구법(oldAndNew)은 2단계 보류, 민사(법률행위 당시) 사례 포함
- 테스트(실측, 전부 통과): 자본시장법 §46 @2019-06-01(삭제 전 "적합성 원칙" 회수), 민법 LID 한정 39버전(오염 0), 동일 MST 복수 시행일 선택(민법 2025-01-31), 제정 전 기준일(금소법) 오류, 가평군 조례 연혁 @2015-01-01, eflaw ID/efYd 차단, admrulOldAndNew 본문, 현행 기준일 하위 호환, 행정규칙 체인 @2024-12-25

## 1.1.0 — 2026-06-10

검증 보고서(wk-ko-legal-스킬검증보고서-2026-06-10) 권고 P1~P6 반영. 조문 정정은 전부 국가법령정보 API 실측 기준.

- [P1] ko-legal-advisory-drafting 금융법령 조문 정정:
  - 자본시장법: §46-2·§47·§49 삭제(2020. 3. 24., 금소법 이관) 반영, §48 라벨 정정(손해배상책임), §63 추가. 투자권유 규제의 금소법(§17~§21) 이관 주의 문구 신설(02-체크리스트). 04-방법론 예시 §46→금소법 §17, 05-가이드 인용 예 §46-2→§174 교체
  - 전자금융거래법: §21(안전성 확보의무)·§22(거래기록)·§24(약관 명시·변경통지)로 라벨 정정, §6 추가. 5.2항 약관 개정 효력 §33→§24
  - 은행법: §46(예금지급불능 조치 — 약관 아님)→§52(약관의 변경 등), §28(겸영업무) 분리, 동일차주(§35) 표현 정정
- [P2] ko-civil-litigation-drafting 문체 모순 해소 — SKILL 7장 "–이다/–한다"체 → 합쇼체("–습니다/–합니다")로 reference·템플릿과 통일, 05-체크리스트 문체 항 동일 정리
- [P3] 사물관할 기준 정정 — "2024. 9. 1.부 개정"(실재하지 않음) → 사물관할규칙 §2, 5억 기준 2022. 1. 28. 개정으로 정정
- [P4] drafting 2종 description에 ko-legal-writing-plan 역방향 경계 한 줄 추가("쟁점 정리" 트리거 중첩 완화)
- [P5] lbox-case-search 본문 추출 항목·보고서 템플릿에 법원·선고일자·판결 종류 필드 추가(서면 인용 형식과 연결)
- [P6] law_api.py docstring OC 우선순위 정정(--oc > 환경변수 > .env), ordin 본문 조회 예시 --id→--mst(api_reference 포함), 비표준 용어 "견아(적)" → "판단 (어조)" 전면 교체
- 파일명 날짜 형식 YYYYMMDD로 전 스킬 통일(civil·advisory·writing-plan 예시 및 lbox-case-progress 스냅샷 YYYYMMDD_HHMMSS 포함), 저장 위치 규칙을 단일 표현("사용자가 선택한 작업 폴더, 없으면 outputs/ + computer:// 링크")으로 통일(lbox-case-progress는 $LBOX_DIR 설계라 제외), 민감정보 파일명 금지 규칙 전 스킬 확산

## 1.0.0 — 2026-06-10

플러그인 통합 초판. 개별 .skill 7종 체제를 단일 플러그인으로 전환.

- 스킬명 단순화: korean-* → ko-* 4종(civil-litigation-drafting, legal-advisory-drafting, legal-writing-plan, law-api). 상호참조 일괄 갱신. 런타임 호환을 위해 law_api.py 내부 식별자·설정 경로는 구명칭 유지.
- 2026-06-10 유지보수 반영분 포함:
  - 저작권법 제7조 제2호 → 제3호 정정(5개 파일, 법령 API 실측 검증)
  - ko-legal-advisory-drafting references 5종 패키징 누락 해소
  - diff_report.py JSON 파싱 예외 처리(현재 손상 exit 2, 스냅샷 손상 WARN 후 진행)
  - law_api.py pdftotext 부재·실패 및 .env 읽기 실패 stderr 경고
  - lbox-commentary-search 섹션 추출 nodeId URL 자체 도출(섹션당 JS 호출 2→1회, 라이브 검증)
  - lbox 검색 2종 window 변수 페이지 간 누적 오류 정정
  - api.md nextEvent 스키마 갱신, 테스트 잔여물 정리
