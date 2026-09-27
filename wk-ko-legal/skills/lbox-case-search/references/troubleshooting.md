# lbox-case-search 트러블슈팅 (2026.6 개편판)

증상이 나타난 시점에 해당 항목만 찾아 읽는다.

- **검색 결과 0건 / 카드 0건**: 우선 (ⓐ) 결과 패널이 닫혔는지 본다 — timeline의 **검색 카드**(돋보기 아이콘 + 검색어)를 클릭하면 패널이 다시 열린다. (ⓑ) **판례 탭**이 선택돼 있는지 본다(결정례·법령 등 다른 탭이면 판례 탭 클릭). (ⓒ) 같은 작업에서 재제출했다면 이전 검색의 필터가 이어져 0건일 수 있다 — "적용된 필터 N개"·선고일 칩을 풀거나 조정한다(실측: 날짜 칩 하나 해제로 0 → 113개). 입력창에 직전 검색어가 이어 붙지 않았는지도 본다(`references/extraction.md` 1-1). (ⓓ) 그래도 0건이면 검색어가 너무 길거나 구체적인 경우다 — 키워드를 짧게 줄여 컴포저에 다시 제출한다.

- **검색 모드가 안 잡힘 / "자동"으로만 실행됨**: 컴포저 제출 전에 **돋보기(검색) 아이콘**을 클릭해 하이라이트(흰 둥근 박스)가 생긴 것을 `zoom`으로 확인한 뒤 제출한다. "자동"으로도 검색형 질의는 검색으로 가지만, 결과 패널이 안 뜨면 검색 모드를 명시 선택해 다시 제출한다.

- **`Runtime.evaluate timed out` (45초)**: `navigate`나 제출 클릭 **직후** 곧바로 `javascript_tool`을 부르면 렌더링과 충돌해 timeout이 난다. batch 안에서 이동·클릭 뒤에 `computer` `wait` 2~3초를 넣는 것이 기본이다(`references/extraction.md` 0장). 그래도 timeout이면 그 페이지만 이동/클릭과 추출을 별개 호출로 나누고 텀을 늘린다. 두 번 이상 retry해도 실패하면 그 카드는 건너뛴다. **JS 내부에 대기 루프를 넣지 말 것**(기다림은 `computer` `wait`로, JS 안의 짧은 대기는 최상위 `await`로 — async IIFE는 `{}`가 돌아온다).

- **본문이 비어 보임 / `innerText`가 작음**: 개편 후 본문은 DOM에 정상 적재되므로 보통 비지 않는다. 적재 판정은 `innerText`가 아니라 **`references/extraction.md` 2장 (1)의 `recoveredLen`/`mainCount`/`judges`** 로 한다. 단락 추출은 `data-node-id="lbox-paragraph-main-*"` 셀렉터로 한다 — 페이지 전체를 `get_page_text`로 읽으면 2벌 렌더·사이드바가 섞인다(`get_page_text`는 반환 규약으로 내놓은 결과를 받을 때만 쓴다). **개편 전의 `<script>` 페이로드 복원은 쓰지 말 것** — 다른 사건 텍스트가 노이즈로 섞인다.

- **본문 자리에 "판례 로그인 후에 이용하실 수 있습니다."** (로그인 만료): lbox는 비로그인·세션 만료 때 리다이렉트하지 않고 같은 URL에서 본문 자리에 이 문구만 낸다(`references/extraction.md` 2장 (1)의 `mainCount` 0·`loginWall`). 건너뛰지 말고 즉시 중단하고 사용자에게 Chrome에서 lbox.kr 재로그인을 요청한다. 앞 판결이 열렸다고 로그인 상태라고 단정하지 않는다(일부 판결은 비로그인에도 보인다). `notFound`("존재하지 않거나 삭제된 페이지")면 법원 표기를 SKILL 1단계 직조회 규칙으로 고쳐 1회 재시도한다. 둘 다 아닌데 특정 한 건만 비면 건너뛰고 진행 상황을 한 줄로 알린다.

- **본문 API가 401 (직접 fetch 시)**: `lbox.kr/route-api/ai/v3/*`(작업·권한 등 페이지가 실제로 쓰는 API — 비인증 401)는 JS 메모리의 Bearer 토큰으로 인증하므로 페이지 밖에서 `fetch`하면 401이 난다. 이 Skill은 API를 직접 호출하지 않고 **DOM 추출**로 동작하므로 문제되지 않는다. 토큰을 긁어 외부로 보내려 하지 말 것.

- **셀렉터가 안 먹힘 (카드)**: UI 변경 가능성. `read_page`는 역할·텍스트·href만 보여 주고 class·data-* 속성은 보여 주지 않으므로, `references/extraction.md` 1장 "셀렉터가 안 맞을 때"의 JS 프로브로 카드 앵커의 속성명과 조상 class를 확인해 `a[data-track-props]`(documentType precedent) 셀렉터를 조정한다(대체 경로 `a[href^="/case/"]`).

- **결과가 `[BLOCKED]`**: 쿼리스트링/쿠키성 데이터가 포함돼 차단된 것이다. URL은 pathname만 다루고(`new URL(href).pathname`), 쿠키·인증 토큰은 결과에 절대 포함하지 않는다.

- **결과가 `[TRUNCATED]`로 잘림**: 추출 JS가 결과를 직접 반환했다(`javascript_tool` 반환 상한 약 1,000자). `references/extraction.md` 0장의 반환 규약대로 결과를 페이지에 내놓고(OUT) `get_page_text`로 받는다 — 잘린 결과를 분할 재호출로 이어 붙이지 않는다.

- **화면에 JSON 글자만 보임 / 클릭이 안 먹힘**: 내놓은 뒤 복구를 하지 않았다. 0장의 복구 JS를 실행한다. 그래도 이상하면 같은 URL로 다시 `navigate`한다(작업 페이지는 백그라운드 탭에서 화면을 한 번 그려야 적재되므로 `references/extraction.md` 0장-5의 재진입 절차(`zoom` 1회 뒤 대기)를 따르고, 결과 패널이 닫혀 있으면 timeline 검색 카드를 눌러 다시 연다).

- **`Tab not found`**: 탭이 닫혔거나 ID가 바뀌었다. `tabs_context_mcp`로 현재 탭 ID를 다시 확인하고 navigate한다.

- **페이지 번호 JS가 `clicked:false`**: `{advanced:true}`면 ›로 다음 5페이지 묶음을 넘긴 것이므로 `wait` 2초 뒤 같은 JS를 다시 보낸다. `{lastBlock:true}`면 N이 마지막 페이지를 넘은 것이므로 순회를 끝낸다. »(마지막 페이지로 이동)는 누르지 않는다 — 중간 페이지를 건너뛴다(`references/extraction.md` 1-3).

- **외부 도메인 링크**: 검색 결과·본문에서 lbox.kr 외 다른 도메인으로 가는 링크는 따라가지 않는다.
