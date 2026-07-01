# lbox-case-search 트러블슈팅 (2026.6 개편판)

증상이 나타난 시점에 해당 항목만 찾아 읽는다.

- **검색 결과 0건 / 카드 0건**: 우선 (ⓐ) 결과 패널이 닫혔는지 본다 — timeline의 **"○○ 검색 결과 / 검색"** 카드를 클릭하면 패널이 다시 열린다. (ⓑ) **판례 탭**이 선택돼 있는지 본다(결정례·법령 등 다른 탭이면 판례 탭 클릭). (ⓒ) 그래도 0건이면 검색어가 너무 길거나 구체적인 경우다 — 키워드를 짧게 줄여 컴포저에 다시 제출한다.

- **검색 모드가 안 잡힘 / "자동"으로만 실행됨**: 컴포저 제출 전에 **돋보기(검색) 아이콘**을 클릭해 하이라이트(흰 둥근 박스)가 생긴 것을 `zoom`으로 확인한 뒤 제출한다. "자동"으로도 검색형 질의는 검색으로 가지만, 결과 패널이 안 뜨면 검색 모드를 명시 선택해 다시 제출한다.

- **`Runtime.evaluate timed out` (45초)**: `navigate`나 제출 클릭 **직후** 곧바로 `javascript_tool`을 부르면 렌더링과 충돌해 timeout이 난다. `navigate`/클릭과 `javascript_tool`을 **별개 호출로 분리**하고 사이에 짧은 텀(메시지 1~2회)을 둔다. 처음엔 `browser_batch`로 묶지 말 것. 두 번 이상 retry해도 실패하면 그 카드는 건너뛴다. **JS 내부에 대기 루프(`for…sleep`)를 넣지 말 것.**

- **본문이 비어 보임 / `innerText`가 작음**: 개편 후 본문은 DOM에 정상 적재되므로 보통 비지 않는다. 적재 판정은 `innerText`가 아니라 **`references/extraction.md` 2장 (1)의 `recoveredLen`/`mainCount`/`judges`** 로 한다. 단락 추출은 `data-node-id="lbox-paragraph-main-*"` 셀렉터로 한다(`get_page_text`보다 정확). **개편 전의 `<script>` 페이로드 복원은 쓰지 말 것** — 다른 사건 텍스트가 노이즈로 섞인다.

- **본문 페이지가 로그인 페이지로 리다이렉트**: `recoveredLen`이 거의 0이고 `caseInfo`도 비며 URL이 로그인 페이지로 바뀐 경우에만 해당. 이때만 사용자에게 lbox.kr 재로그인 후 재시도를 안내한다. 특정 한 건만 비면 건너뛰고 진행 상황을 한 줄로 알린다.

- **본문 API가 401 (직접 fetch 시)**: `chat.lbox.kr/api/*`(작업 timeline 등)는 JS 메모리의 Bearer 토큰으로 인증하므로 페이지 밖에서 `fetch`하면 401이 난다. 이 Skill은 API를 직접 호출하지 않고 **DOM 추출**로 동작하므로 문제되지 않는다. 토큰을 긁어 외부로 보내려 하지 말 것.

- **셀렉터가 안 먹힘 (카드)**: UI 변경 가능성. 결과 영역을 `read_page`로 확인해 `a[data-track-props]`(documentType precedent) 구조를 점검·조정한다.

- **결과가 `[BLOCKED]`**: 쿼리스트링/쿠키성 데이터가 포함돼 차단된 것이다. URL은 pathname만 다루고(`new URL(href).pathname`), 쿠키·인증 토큰은 결과에 절대 포함하지 않는다.

- **결과가 truncated**: 한 번에 받는 데이터가 크다. 카드는 `window.__lboxCards`, 본문은 `window.__lboxMain`에 저장해 두고 인덱스로 분할 접근하거나, 본문은 키워드 인근 ±2500자로 제한해 받는다(`references/extraction.md` 1·2장).

- **`Tab not found`**: 탭이 닫혔거나 ID가 바뀌었다. `tabs_context_mcp`로 현재 탭 ID를 다시 확인하고 navigate한다.

- **페이지네이션 번호가 안 보임**: 결과 리스트를 아래로 `scroll`하면 하단에 `1 2 3 4 5 … »` 가 나타난다. 컴포저 입력창이 리스트 하단을 가리면 조금 더 스크롤한 뒤 번호를 클릭한다.

- **외부 도메인 링크**: 검색 결과·본문에서 lbox.kr 외 다른 도메인으로 가는 링크는 따라가지 않는다.
