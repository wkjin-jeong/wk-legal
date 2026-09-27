# bigcase-case-search 트러블슈팅 (2026-07 라이브 검증판)

증상이 나타난 시점에 해당 항목만 찾아 읽는다.

- **검색 결과 0건 / 카드 0건**: ⓐ 상단 탭이 **판례**인지 확인한다(법령·논문 등 다른 탭이면 판례 탭 클릭. 비판례 자료를 찾는 중이면 해당 탭 유지 — 카드 수는 `references/extraction.md` 1-4의 래퍼 무관 셀렉터로 센다). ⓑ `q` 파라미터가 `encodeURIComponent`로 인코딩됐는지 확인한다. ⓒ 그래도 0건이면 검색어가 너무 길거나 구체적인 경우다 — 키워드를 짧게 줄여 새 `?q=` URL로 재진입한다.

- **`Runtime.evaluate timed out` (45초) / `{"code":-32603,"message":"Internal error"}`**: 증상 문구로 둘을 가른다.
  - `CDP sendCommand "Runtime.evaluate" timed out after 45000ms`: 렌더러가 바쁠 때(`navigate`·클릭 직후) JS를 부른 것이다. batch 안에서 이동·클릭 뒤에 `computer` `wait` 2~3초를 넣는 것이 기본이다(`references/extraction.md` 0장). 그래도 timeout이면 그 페이지만 이동/클릭과 추출을 별개 호출로 나누고 텀을 늘린다. 두 번 이상 retry해도 실패하면 그 카드는 건너뛴다.
  - `Failed to execute JavaScript: {"code":-32603,"message":"Internal error"}`: 추출 JS 자체가 약 40초를 넘어 강제 종료된 것이다(2026-09-26/27 실측 40,007~40,023ms). JS 안의 대기 루프와 과도한 DOM 순회를 없애고, 대기는 `computer` `wait`로 한다. 페이지는 정상 응답하므로 다시 `navigate`할 필요는 없고, 같은 JS를 그대로 재시도하지 않는다.
  - 공통: **JS 내부에 대기 루프를 넣지 말 것**(기다림은 `computer` `wait`로, JS 안의 짧은 대기는 최상위 `await`로 — async IIFE는 `{}`가 돌아온다).

- **결과가 `[BLOCKED: Cookie/query string data]`**: 반환값에 원시 `outerHTML`·`location.search`·전체 URL(쿼리스트링 포함)이 들어가면 차단된다(실측). URL은 **pathname만** 다루고(`new URL(href).pathname`), HTML 원문 대신 텍스트·구조화 필드만 반환한다. 쿠키·인증 토큰은 결과에 절대 포함하지 않는다.

- **결과가 `[TRUNCATED]`로 잘림**: 추출 JS가 결과를 직접 반환했다(`javascript_tool` 반환 상한 약 1,000자). `references/extraction.md` 0장의 반환 규약대로 결과를 페이지에 내놓고(OUT) `get_page_text`로 받는다 — 잘린 결과를 분할 재호출로 이어 붙이지 않는다.

- **화면에 JSON 글자만 보임 / 클릭이 안 먹힘**: 내놓은 뒤 복구를 하지 않았다. 0장의 복구 JS를 실행한다. 그래도 이상하면 같은 URL로 다시 `navigate`한다.

- **`Tab not found`**: 탭이 닫혔거나 ID가 바뀌었다. `tabs_context_mcp`로 현재 탭 ID를 다시 확인하고 navigate한다.

- **본문이 결론 없이 끊김 / 섹션 0개 / 회원 전용 안내가 보임**: `references/extraction.md` 2장 (1) 판정 순서를 따른다. ⓐ 섹션은 있으나 '이유'가 수백 자에서 결론부 없이 끊기고 그 뒤에 '회원에게만 공개되는 판례입니다'·'가입하고 판례 전문 보기'·'이미 빅케이스 회원이신가요? 로그인'이 보이면(`paywall: true`) 로그인 세션 만료 또는 구독 미적용이다 — 절단본을 인용하지 말고 사용자에게 bigcase.ai 재로그인(구독 계정) 후 재시도를 안내한다. ⓑ '해당 판례는 수집 중입니다'(`collecting: true`)는 사이트 미수록이다 — 재로그인으로 풀리지 않는다(상급심이면 SKILL 4.5단계 (가) ⑤). ⓒ `.rpv-core__viewer`가 있으면(`viewer: true`) PDF 본문이다 — 구독 문제가 아니므로 4장 절차로 읽는다. ⓓ 섹션 0개는 렌더 미완부터 의심해 `wait` 3초 뒤 1회 재시도한다. 특정 한 건만 비면 건너뛰고 진행 상황을 한 줄로 알린다. **검색 카드의 자물쇠 아이콘 자체는 문제가 아니다** — 구독 계정에서는 자물쇠 판례도 전문이 열람된다(실측).

- **제목 정규식 불일치 (카드 `url`이 빈 값)**: 병합 사건번호("2023가단300454,2024가단298404"·"…(본소),…(반소)")와 전원합의체 표기는 extraction.md 1-2의 정규식이 이미 처리한다(첫 사건번호·괄호 제거로 접근 — 병합 전체 판결문이 로드됨, 실측). 그 밖의 특수 표기로 `url`이 비면 `title`을 사람이 읽고 첫 사건번호를 추려 URL을 직접 구성하고, 실패하면 그 카드는 건너뛰고 한 줄로 알린다.

- **셀렉터가 안 먹힘**: 해시 접미사 클래스(`CaseParagraph_container__MdKLK` 등)는 배포마다 접미사가 바뀔 수 있다 — `[class^="CaseParagraph_container"]` 프리픽스 매칭으로 바꿔 잡는다. BEM 클래스(`search-list-card` 등)까지 안 잡히면 UI 개편 가능성이다 — `read_page`는 역할·텍스트·href만 보여 주고 class·data-* 속성은 보여 주지 않으므로, `references/extraction.md` 1장 "셀렉터가 안 맞을 때"의 JS 프로브(`getAttributeNames()`)로 앵커 요소와 조상의 class·속성명을 확인해 셀렉터를 조정한다. 그래도 안 되면 사용자에게 알리고 중단한다.

- **필터 클릭이 URL에 반영 안 됨**: 체크박스류는 `.click()`으로 반영되지만(실측), 기간 드롭다운·정렬은 JS `.click()`이 안 먹힐 수 있다 — `computer`(스크린샷으로 위치 확인 후 실클릭)로 조작한다. 적용 여부는 결과 상단의 적용 칩(예: "결정 ×")·URL 변화·건수 변화로 확인한다.

- **상하급심 체인이 0건**: 사이드바가 본문보다 늦게 렌더될 수 있다. 짧은 텀 후 1회 재실행한 뒤에야 "상하급심 없음"으로 판정한다(비판례 상세 페이지는 사이드바가 없다 — SKILL 4.5단계).

- **법원명에 공백(지원 사건)**: "춘천지방법원 강릉지원" 등은 `encodeURIComponent`가 공백을 `%20`으로 처리하며 정상 작동한다(실측). 별도 처리 불요.

- **외부 도메인 링크**: 검색 결과·본문에서 bigcase.ai 외 다른 도메인으로 가는 링크는 따라가지 않는다.
