# lbox-commentary-search 트러블슈팅 (2026.6 개편판)

증상이 나타난 시점에 해당 항목만 찾아 읽는다.

- **"주석·실무서"를 눌렀더니 도서 목록(`/book/list`)으로 가버림**: 좌측 내비게이션 링크를 누른 것이다. 검색 결과의 주석서는 **결과 패널 안의 `role="tab"` 버튼**으로 전환해야 한다(`references/extraction.md` 1-2의 JS). 검색을 잃었으면 작업(Task)으로 돌아가 "○○ 검색 결과" 카드를 클릭해 패널을 다시 연 뒤 탭을 누른다(카드를 누르면 입력창에 그 검색어가 채워진다 — 재검색 전에 비운다, `references/extraction.md` 1-1).

- **검색 결과 0건 (주석·실무서 탭)**: (ⓐ) 패널이 아직 로딩 중일 수 있으니 짧은 텀 후 재확인, (ⓑ) 이 작업에 주석서 결과가 없을 수 있음 → 키워드를 넓히거나 법령 한정을 풀어 재검색(같은 작업이면 이월된 필터 칩을 초기화 — `references/extraction.md` 1-1), (ⓒ) 판례 탭으로 잘못 보고 있지 않은지 확인.

- **`[BLOCKED: Cookie/query string data]`**: JS 결과에 raw URL·쿼리스트링·검색 키워드가 섞였다. href는 식별자 값(bookId·nodeId·tocId·volumeHistoryId·page)으로 **분해해서만** 반환하고(`query` 파라미터는 검색 키워드이므로 반환 금지), 본문 URL은 그 값으로 재구성한다. `location.search`/`a.href`는 JS 안에서 읽기만 하고 반환 JSON에 넣지 않는다.

- **본문 `header not found`**: URL의 `nodeId`에 해당하는 `[data-node-id]`가 viewer에서 안 잡힘(로드 미완·UI 변경). 짧은 텀 후 한 번 retry, 그래도 실패면 그 카드 건너뜀. `[data-node-id]`가 0개이고 `.rpv-core__viewer`가 있으면 PDF형 실무서(구조 차이)다 — retry하지 말고 `references/extraction.md` 2-2로(적재 초기에는 canvas가 없을 수 있다).

- **PDF형 본문이 표지·빈 쪽만 보이거나 목표 쪽 레이어가 안 생김**: 백그라운드 탭은 적재가 느리고 편차가 크다(110초 성공·190초 이상 미점프 실측 모두 있음) — 프로브로 기다리고, 상한 절반까지 빈값이면 `scroll` 2틱 뒤 이어 보되 상한을 넘기면 `본문 미확인(PDF형 실무서)`. 추출 뒤 복구하면 뷰어가 맨 위로 돌아가므로 `screenshot`·`zoom` 전에 목표 레이어를 `scrollIntoView()`로 다시 띄운다(`references/extraction.md` 2-2).

- **본문이 2벌로 보임 / 같은 문장이 반복**: 본문 페이지가 전체 DOM을 2벌 렌더한다. 형제 walk 중 **이미 본 `data-node-id`는 건너뛰고**, `headers[0]`(첫 copy)만 기준으로 한다(두 copy는 서로 다른 parent라 첫 copy만 걷으면 깨끗). `references/extraction.md` 2장.

- **섹션이 너무 큼(수만 자)**: `nodeId`가 상위 장을 가리키는 경우다. 전문을 `window.__lboxSection`에 저장하고 질의 키워드 인근만 발췌해 내놓는다(한 번에 12,000자 안팎까지 — `references/extraction.md` 0장).

- **결과가 `[TRUNCATED]`로 잘림**: 추출 JS가 결과를 직접 반환했다(`javascript_tool` 반환 상한 약 1,000자). `references/extraction.md` 0장의 반환 규약대로 결과를 페이지에 내놓고(OUT) `get_page_text`로 받는다 — 잘린 결과를 분할 재호출로 이어 붙이지 않는다.

- **화면에 JSON 글자만 보임 / 클릭이 안 먹힘**: 내놓은 뒤 복구를 하지 않았다. 0장의 복구 JS를 실행한다. 그래도 이상하면 같은 URL로 다시 `navigate`한다(작업 페이지는 결과 패널이 닫혀 있으면 timeline의 "○○ 검색 결과" 카드를 눌러 다시 연다 — 카드를 누르면 입력창에 그 검색어가 채워진다, 재검색 전에 비운다, `references/extraction.md` 1-1).

- **본문 visit 결과가 1,000~2,000자뿐 / 조문·참고문헌만 있음**: 조문 본문 노드 특성. SKILL 5.1 절차로 같은 도서·breadcrumb 부모의 하위 노드 카드를 추가 visit한다.

- **`Runtime.evaluate timed out`**: `navigate`·클릭 직후 곧바로 JS 호출 시 발생. batch 안에서 이동·클릭 뒤에 `computer` `wait` 2~3초를 넣는 것이 기본이다(`references/extraction.md` 0장). 그래도 timeout이면 그 페이지만 이동/클릭과 추출을 별개 호출로 나누고 텀을 늘린다. 두 번 이상 retry해도 실패하면 그 카드는 건너뛴다. **JS 내부에 대기 루프를 넣지 말 것**(기다림은 `computer` `wait`로, JS 안의 짧은 대기는 최상위 `await`로 — async IIFE는 `{}`가 돌아온다).

- **카드 셀렉터가 안 먹힘**: `a[data-track-props]`(documentType `scholar`)가 0건이면 (ⓐ) 주석·실무서 탭이 활성인지, (ⓑ) 패널이 열렸는지 확인. UI가 바뀌었으면 `read_page`로 결과 영역 구조를 보고 셀렉터를 조정한다.

- **다음 페이지로 안 넘어감 / 같은 카드가 다시 나옴**: `references/extraction.md` 1-4 JS의 반환값을 본다. `advanced:true`면 묶음만 넘긴 것이다 — `wait` 2초 뒤 같은 JS를 다시 보낸다. `lastBlock:true`면 마지막 페이지를 넘었으니 순회를 끝낸다. `no-pagination`이면 결과가 한 페이지뿐이거나 패널·탭이 바뀐 것이다 — 카드 수와 주석·실무서 탭 선택을 확인한다. `clicked:true`인데 rank가 그대로면 `wait` 2초 뒤 카드 추출만 다시 보낸다. »(마지막 페이지로 이동)는 누르지 않는다.

- **카드 필드가 한 칸씩 어긋남**: 건수는 맞는데 1-3 결과의 `layoutWarn > 0`이거나 `emptyMeta`가 true(bookMeta·breadcrumb가 전부 빈값이고 snippet이 도서명·저자·출판사 문자열)면 카드 컨테이너를 잘못 잡은 것이다(UI 변경). 앵커에서 조상으로 올라가며 자식 3개(제목블록·스니펫·breadcrumb)인 첫 조상을 JS 프로브로 찾아 셀렉터를 고친다(`references/extraction.md` 1-3).

- **스니펫이 비거나 매우 짧음**: 먼저 `emptyMeta`·`layoutWarn`으로 컨테이너 오인이 아닌지 확인한다(위 항목). 아니면 키워드가 본문에 직접 등장하지 않는 결과(메타 매칭)다. 트리아주를 섹션 제목·breadcrumb·도서명에 더 비중을 두고, 본문 visit 후 재분류 비중을 늘린다.

- **법령 매핑 불명확**: 추측 금지. `references/filter-map.md`의 "매핑이 불명확한 법령" 절차로 사용자에게 확인하거나 한정 없이 진행한다.
