# lbox-commentary-search 트러블슈팅 (2026.6 개편판)

증상이 나타난 시점에 해당 항목만 찾아 읽는다.

- **"주석·실무서"를 눌렀더니 도서 목록(`/book/list`)으로 가버림**: 좌측 내비게이션 링크를 누른 것이다. 검색 결과의 주석서는 **결과 패널 안의 `role="tab"` 버튼**으로 전환해야 한다(`references/extraction.md` 1-2의 JS). 검색을 잃었으면 작업(Task)으로 돌아가 "○○ 검색 결과" 카드를 클릭해 패널을 다시 연 뒤 탭을 누른다.

- **검색 결과 0건 (주석·실무서 탭)**: (ⓐ) 패널이 아직 로딩 중일 수 있으니 짧은 텀 후 재확인, (ⓑ) 이 작업에 주석서 결과가 없을 수 있음 → 키워드를 넓히거나 법령 한정을 풀어 재검색, (ⓒ) 판례 탭으로 잘못 보고 있지 않은지 확인.

- **`[BLOCKED: Cookie/query string data]`**: JS 결과에 raw URL·쿼리스트링·검색 키워드가 섞였다. href는 식별자 값(bookId·nodeId·tocId·volumeHistoryId)으로 **분해해서만** 반환하고, 본문 URL은 그 값으로 재구성한다. `location.search`/`a.href`는 JS 안에서 읽기만 하고 반환 JSON에 넣지 않는다.

- **본문 `header not found`**: URL의 `nodeId`에 해당하는 `[data-node-id]`가 viewer에서 안 잡힘(로드 미완·UI 변경). 짧은 텀 후 한 번 retry, 그래도 실패면 그 카드 건너뜀.

- **본문이 2벌로 보임 / 같은 문장이 반복**: 본문 페이지가 전체 DOM을 2벌 렌더한다. 형제 walk 중 **이미 본 `data-node-id`는 건너뛰고**, `headers[0]`(첫 copy)만 기준으로 한다(두 copy는 서로 다른 parent라 첫 copy만 걷으면 깨끗). `references/extraction.md` 2장.

- **섹션이 너무 큼(수만 자)**: `nodeId`가 상위 장을 가리키는 경우다. 전문을 `window.__lboxSection`에 저장하고 질의 키워드 인근만 발췌해 받는다. 한 번에 큰 문자열 반환은 truncated된다.

- **본문 visit 결과가 1,000~2,000자뿐 / 조문·참고문헌만 있음**: 조문 본문 노드 특성. SKILL 5.1 절차로 같은 도서·breadcrumb 부모의 하위 노드 카드를 추가 visit한다.

- **`Runtime.evaluate timed out`**: `navigate`·클릭 직후 곧바로 JS 호출 시 발생. 분리 호출 + 짧은 텀. 두 번 실패하면 그 카드 건너뜀. JS 내부에 대기 루프 금지.

- **카드 셀렉터가 안 먹힘**: `a[data-track-props]`(documentType `scholar`)가 0건이면 (ⓐ) 주석·실무서 탭이 활성인지, (ⓑ) 패널이 열렸는지 확인. UI가 바뀌었으면 `read_page`로 결과 영역 구조를 보고 셀렉터를 조정한다.

- **스니펫이 비거나 매우 짧음**: 키워드가 본문에 직접 등장하지 않는 결과(메타 매칭). 트리아주를 섹션 제목·breadcrumb·도서명에 더 비중을 두고, 본문 visit 후 재분류 비중을 늘린다.

- **법령 매핑 불명확**: 추측 금지. `references/filter-map.md`의 "매핑이 불명확한 법령" 절차로 사용자에게 확인하거나 한정 없이 진행한다.
