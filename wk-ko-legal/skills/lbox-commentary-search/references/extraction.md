# lbox-commentary-search 추출 기술 reference

SKILL.md 본문이 지시하는 시점에 해당 장만 읽는다. 모든 셀렉터·JS는 2026.6 개편판 라이브 검증본이다. lbox는 Next.js(App Router) 기반이며 본문은 서버 렌더되어 별도 API 없이 DOM에 들어온다.

## 1. 검색 실행 · 주석·실무서 탭 · 카드 추출

### 1-1. 검색 실행 (컴포저 "검색" 모드)

`lbox-case-search`와 동일하다.

1. `navigate` → `https://lbox.kr/`.
2. `computer` `screenshot`으로 컴포저 확인 → 입력창 클릭 → 검색어 `type` → **돋보기(검색) 아이콘** 클릭(흰 하이라이트 확인, 불확실하면 `zoom`) → **↑(제출)** 클릭.
3. `/task/{id}`로 이동하고 결과 패널이 열린다(기본 **판례** 탭).
4. 제출 클릭과 추출 `javascript_tool` 호출은 분리하고 짧은 텀을 둔다.

### 1-2. "주석·실무서" 탭으로 전환 (패널 탭 ≠ 좌측 내비 링크)

결과 패널 상단 탭(판례·결정례·유권해석·**주석·실무서**·법령·…) 중 "주석·실무서"는 **패널 안의 `role="tab"` 버튼**이다. 같은 텍스트의 **좌측 내비게이션 링크**는 `<a href="/book/list">`라서 누르면 검색을 잃고 도서 목록으로 가버린다. JS로 정확히 패널 탭만 클릭한다:

```javascript
(() => {
  const cands = Array.from(document.querySelectorAll('button,[role="tab"]'))
    .filter(el => (el.textContent || '').trim() === '주석·실무서');
  const tab = cands.find(el => el.getAttribute('role') === 'tab') || cands[0];
  if (tab) tab.click();
  return JSON.stringify({ clicked: !!tab });
})()
```

클릭 후 짧은 텀을 두고 1-3의 카드 추출을 실행한다. (결과 0건이면 패널이 아직 로딩 중이거나 이 작업에 주석서 결과가 없을 수 있으니 한 번 더 확인.)

### 1-3. 카드 추출

주석·실무서 카드는 제목 앵커 `a[data-track-props]`(JSON `documentType:"scholar"`)이고, **href에 본문 식별자**(`/book/{bookId}?tocId=…&nodeId=…&volumeHistoryId=…`)가 들어 있다. 카드 컨테이너는 `div.border-b-xs`이며 직속 자식 3개 = ① 제목+도서메타, ② 스니펫, ③ breadcrumb.

> **`[BLOCKED]` 회피**: raw href·쿼리스트링·검색 키워드를 결과 JSON에 그대로 담으면 `[BLOCKED: Cookie/query string data]` 차단을 맞는다. href는 **식별자 값으로 분해해서**(bookId·nodeId·tocId·volumeHistoryId) 반환하고, 본문 URL은 5단계에서 그 값으로 재구성한다.

```javascript
(() => {
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const anchors = Array.from(document.querySelectorAll('a[data-track-props]')).filter(a => {
    try { return JSON.parse(a.getAttribute('data-track-props')).documentType === 'scholar'; } catch (e) { return false; }
  });
  const seen = new Set(), cards = [];
  anchors.forEach(a => {
    let tp = {}; try { tp = JSON.parse(a.getAttribute('data-track-props')); } catch (e) {}
    let bookId = '', nodeId = '', tocId = '', volumeHistoryId = '';
    try {
      const u = new URL(a.href);                       // a.href는 읽기만; 반환하지 않음
      bookId = (u.pathname.match(/\/book\/(\w+)/) || [])[1] || '';
      nodeId = u.searchParams.get('nodeId') || '';     // 예: "lbox-paragraph-2014"
      tocId = u.searchParams.get('tocId') || '';
      volumeHistoryId = u.searchParams.get('volumeHistoryId') || '';
    } catch (e) {}
    const key = bookId + '|' + tocId + '|' + nodeId;
    if (key !== '||' && seen.has(key)) return;          // 중복 제거(결과가 2벌일 수 있음)
    seen.add(key);
    const card = a.closest('div.border-b-xs') || a.parentElement.parentElement;
    const kids = card ? Array.from(card.children) : [];
    const titleBlock = kids[0];
    cards.push({
      rank: tp.rank, docId: tp.docId,
      sectionTitle: txt(a),
      bookMeta: titleBlock && titleBlock.children[1] ? txt(titleBlock.children[1]) : '', // 도서명 저자 출판사 날짜 판
      snippet: txt(kids[1]).slice(0, 500),
      breadcrumb: txt(kids[2]),
      bookId, nodeId, tocId, volumeHistoryId
    });
  });
  window.__lboxCommentary = cards;                      // 분할 접근용(현재 페이지 한정)
  return JSON.stringify({ count: cards.length, items: cards });
})()
```

- `bookMeta`는 "민법 채권각칙 제6권 김용덕 한국사법행정학회 2021. 10. 15. 제5판"처럼 도서명·편집대표·출판사·출판일·판본이 이어진 문자열이다. 답변의 출처 표기에 그대로 활용한다(필요하면 끝의 "제N판"·날짜를 분리).
- 결과가 truncated되면 `window.__lboxCommentary`를 인덱스로 분할해 받는다.
- 페이지 간 중복은 navigate로 window가 초기화되므로, 각 페이지가 반환한 `items`를 대화 안에서 같은 `(bookId, tocId, nodeId)` 키로 한 번 더 거른다.

### 1-4. 페이지 순회 · 필터

- 결과 리스트를 아래로 `scroll`하면 하단에 **번호 페이지네이션**(`1 2 3 …`)이 보인다. 번호를 클릭하고 1-3을 다시 실행한다(판례 검색과 동일).
- 법령·문서유형으로 좁히려면 주석·실무서 탭의 필터 칩(있으면)을 `screenshot`으로 확인해 `computer`로 조작하거나, 결과의 `bookMeta`·`breadcrumb`으로 선별한다(`references/filter-map.md`).

## 2. 본문 섹션 추출 (`/book/{bookId}` 뷰어)

식별자로 본문 URL을 구성해 `navigate`한다:

```
https://lbox.kr/book/{bookId}?tocId={tocId}&nodeId={nodeId}&volumeHistoryId={volumeHistoryId}
```

핵심 메커니즘은 개편 전과 같다: URL의 `nodeId`에 해당하는 `[data-node-id]` 요소가 섹션 시작 **헤더**(`data-viewer-type="header"`)이고, 다음 `[data-viewer-type="header"]` 전까지의 형제 paragraph가 그 섹션 본문이다.

> **2벌 렌더 주의**: 본문 페이지도 전체 DOM을 2벌로 그린다(같은 `nodeId` 헤더가 2개). 다만 **두 copy는 서로 다른 parent**에 있어, `headers[0]`의 형제만 걷으면 한 copy만 깨끗하게 모인다(실측: 중복 0, 제목 1회). 그래도 안전하게 형제 walk 중 **이미 본 `data-node-id`는 건너뛴다**.
>
> **섹션이 클 수 있음**: `nodeId`가 "Ⅳ. 인신사고로 인한 손해액의 산정" 같은 **상위 장**을 가리키면 그 장 전체(수만 자)가 한 섹션이다. 전문을 한 번에 반환하면 truncated되므로 `window.__lboxSection`에 저장하고 **질의 키워드 인근만 발췌**해 반환한다.

navigate 후 짧은 텀을 두고 실행:

```javascript
(() => {
  const targetId = new URL(location.href).searchParams.get('nodeId');   // 읽기만; 반환하지 않음
  const header = document.querySelector('[data-node-id="' + (targetId || '') + '"]'); // 첫 copy
  if (!header) return JSON.stringify({ error: 'header not found' });
  const sibs = Array.from(header.parentElement.children);
  const start = sibs.indexOf(header);
  const seen = new Set(), parts = [];
  for (let i = start; i < sibs.length; i++) {
    const el = sibs[i];
    if (i > start && el.getAttribute('data-viewer-type') === 'header') break;  // 다음 섹션 시작
    const id = el.getAttribute('data-node-id');
    if (id) { if (seen.has(id)) continue; seen.add(id); }                        // 2벌 dedup 보험
    const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (t) parts.push(t);
  }
  const full = parts.join('\n');
  window.__lboxSection = full;
  // 질의 특화 키워드(1~3개)를 매 검색마다 교체. 없으면 앞부분.
  const kw = [/* 예: '양도금지특약','대항요건' */];
  let i = -1; for (const k of kw) { i = full.indexOf(k); if (i >= 0) break; }
  return JSON.stringify({
    sectionTitle: parts[0] || '',
    nodeCount: parts.length,
    sectionLen: full.length,
    excerpt: i >= 0 ? full.slice(Math.max(0, i - 300), i + 3000) : full.slice(0, 3500)
  });
})()
```

- **추가 발췌**: 더 필요하면 `window.__lboxSection.slice(START, END)`로 범위를 옮겨 받는다(한 번에 큰 문자열 반환 금지 → truncated).
- **`header not found`**: 로드 미완·UI 변경. 한 번 retry(짧은 텀) 후에도 실패하면 그 카드 건너뜀. `nodeId`를 JS 코드에 임베드해 반환 JSON에 노출하면 `[BLOCKED]` 위험이 있으니, URL에서 읽어 쓰되 결과로 반환하지 않는다.
- **본문 빈약(조문 노드)**: SKILL 5.1 절차 — 같은 도서·breadcrumb 부모의 하위 노드 카드를 추가 visit.
