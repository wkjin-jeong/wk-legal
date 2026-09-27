# lbox-commentary-search 추출 기술 reference

SKILL.md 본문이 지시하는 시점에 해당 장만 읽는다. 모든 셀렉터·JS는 2026.6 개편판 라이브 검증본이다. lbox는 Next.js(App Router) 기반이며 본문은 서버 렌더되어 별도 API 없이 DOM에 들어온다.

## 0. 반환·호출 규약 — 결과는 `get_page_text`로, 한 페이지는 batch 한 번에

`javascript_tool`의 반환값은 약 1,000자에서 `[TRUNCATED]`로 잘린다(Claude Code·Cowork 실측). 카드 10건은 3~4천 자, 본문 머리부는 3천 자를 넘기 쉬워 그대로 반환하면 매번 잘린다. 그래서 이 문서의 추출 JS는 결과를 반환하지 않고 **페이지에 내놓는다**: `OUT()`이 body를 결과 텍스트만 담은 `<pre>`로 잠시 바꾸고 `OUT n자`만 반환하면, `get_page_text`가 그 텍스트를 잘림 없이 돌려준다.

1. **한 페이지는 batch 한 번에**: [`navigate`·클릭 → `computer` `wait` 2~3초 → 추출 JS → `get_page_text` → (같은 페이지의 다음 추출 JS → `get_page_text`) → 복구 JS]를 `browser_batch` 하나로 보낸다. 결과는 각 `get_page_text` 출력의 `Source element: <body>` 아래 JSON이다. batch 도구가 없으면 차례로 호출한다.
2. **복구 JS**: `if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; } 'restored'` — 떼어 둔 원래 body를 그대로 되돌리므로 페이지 상태·이벤트가 유지된다(2026-09-26 실측: lbox 작업 결과 패널·판례 본문, bigcase 검색 결과 — 복구 뒤 페이지 넘기기·사이드바 펼치기 정상). **복구 전에는 `computer` 클릭·`screenshot`을 하지 않는다**(빈 화면을 누르게 된다). 아래 JS는 모두 첫 줄에서 먼저 복구하므로, batch가 중간에 끊겨도 다음 JS가 되살린다.
3. 한 번에 내놓는 양은 12,000자 안팎까지로 한다(컨텍스트 절약). 더 필요하면 범위를 옮겨 다시 내놓는다 — `window.__lboxCommentary`·`window.__lboxSection`에 저장해 둔 값에서 자르면 DOM을 다시 읽지 않아도 된다. 1,000자보다 확실히 작은 결과(탭 클릭 여부·건수 등)는 그대로 반환해도 된다.
4. `get_page_text`에 JSON이 아니라 원래 페이지 글이 나오면 내놓기가 안 된 것이다 — 추출 JS의 반환값(`OUT n자`인지 오류인지)을 보고 다시 보낸다. 페이지 전체를 `get_page_text`로 읽어 본문을 얻으려 하지 않는다(사이드바·메뉴가 섞인다).
5. **렌더 대기·재시도**: 이동·클릭 직후 곧바로 JS를 실행하면 렌더링과 충돌해 `Runtime.evaluate timed out`(45초)이 날 수 있어 batch 안에 `wait`를 먼저 둔다(2026-09-26 실측 — bigcase 판례 본문 2초, lbox 판례 본문 3초면 백그라운드 탭에서도 본문·사이드바까지 적재됐다). 추출 결과가 0건·빈값이면(렌더 미완) `wait` 3초 → 추출 묶음만 다시 보낸다(최대 3회. lbox 작업(Task)의 결과 패널은 검색이 끝나야 채워지므로 제출 직후에는 `wait` 3초씩 최대 5회. **작업 주소로 다시 들어갈 때**(새로고침·재진입, 본문 visit 뒤 결과로 돌아갈 때)는 [`navigate` → `wait` 2초 → `computer` `zoom`(작은 영역, `scale` 0.3) 또는 `screenshot` 1회 → `wait` 3초 → 확인]으로 한다 — 백그라운드 탭(`document.visibilityState` 'hidden')에서는 화면을 한 번 그려야 작업 적재가 시작된다(2026-09-27 실측: 그리지 않으면 20초 넘게 스피너, `zoom` 한 번 뒤 약 5초에 timeline). 그래도 비면 5초씩 최대 5회 더 기다린다. timeline이 떴는데 패널이 닫혀 있으면 "○○ 검색 결과" 카드를 누른다 — 1-1 5.). `Runtime.evaluate timed out`이 나면 그 페이지만 이동과 추출을 따로 보낸다. JS 실행이 약 40초를 넘으면 `{"code":-32603,"message":"Internal error"}`로 강제 종료된다(`references/troubleshooting.md`).
6. **`await`는 최상위에서**: JS 안에서 기다려야 하면 async IIFE로 감싸지 말고 최상위 `await`를 쓴다 — async IIFE는 결과 대신 `{}`가 돌아온다(실측). 긴 대기는 JS 반복문이 아니라 `computer` `wait`로 한다(JS 실행이 약 40초를 넘으면 -32603 Internal error로 강제 종료된다).

아래 JS는 모두 이 두 줄로 시작한다.

```javascript
if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(이전 내놓기가 남아 있으면)
const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
```

## 1. 검색 실행 · 주석·실무서 탭 · 카드 추출

### 1-1. 검색 실행 (컴포저 "검색" 모드)

`lbox-case-search`와 동일하다.

1. `navigate` → `https://lbox.kr/`.
2. `computer` `screenshot`으로 컴포저 확인 → 입력창 클릭 → 검색어 `type` → **돋보기(검색) 아이콘** 클릭(흰 하이라이트 확인, 불확실하면 `zoom`) → **↑(제출)** 클릭.
3. `/task/{id}`로 이동하고 결과 패널이 열린다(기본 **판례** 탭).
4. 제출 클릭 뒤 추출은 같은 batch에서 `computer` `wait` 3초 뒤에 한다(0장).
5. **작업 페이지에서 다시 검색할 때**(재검색·0건 뒤 재검색): 컴포저는 제출 뒤에도 직전 검색어를 남기고(작업 주소를 다시 열면 마지막 검색어가 채워져 있을 수 있다), 결과 패널은 앞 검색의 탭·유형 칩·법령 필터를 이어받는다(2026-09-27 실측). 그대로 클릭 → `type`하면 검색어가 이어 붙은 질의가 나가고, 이어진 필터 때문에 결과가 조용히 좁혀진다(실측: 초기화하면 4건인 검색이 1건, 223건인 검색이 5건).
   - ⓐ 입력창 클릭 → `cmd+a`(Windows `ctrl+a`) → `Delete`.
   - ⓑ JS `document.querySelector('[contenteditable="true"]').innerText.trim().length`가 0인지 확인한다(아니면 ⓐ 반복). 길이만 반환하고 검색어는 반환하지 않는다(`[BLOCKED]` 회피). 컴포저는 contenteditable 하나이고 textarea는 없다.
   - ⓒ 새 검색어 `type` → 돋보기 하이라이트를 `zoom`으로 확인 → 제출 → `wait` 3초.
   - ⓓ 추출 전에 JS `(document.body.innerText.match(/적용된 필터 (\d+)개/) || [])[1] || '0'`로 적용 필터 수를 본다. 이번 검색 의도와 다르면(특히 법령 한정 해제) '적용된 필터 N개' 줄 오른쪽의 '초기화'를 누르거나(칩의 ×로 하나씩 풀 수도 있다) '법령별 도서목록' 모달에서 선택을 풀고 '적용하기'를 누른 뒤, 같은 JS가 '0'인지 보고 추출한다.
   - timeline의 "○○ 검색 결과" 카드를 눌러 패널을 다시 열면 그 카드의 검색어가 입력창에 채워진다 — 패널을 다시 연 뒤의 재검색에도 ⓐⓑ를 먼저 한다.

### 1-2. "주석·실무서" 탭으로 전환 (패널 탭 ≠ 좌측 내비 링크)

결과 패널 상단 탭(판례·결정례·유권해석·**주석·실무서**·법령·…) 중 "주석·실무서"는 **패널 안의 `role="tab"` 버튼**이다. 같은 텍스트의 **좌측 내비게이션 링크**는 `<a href="/book/list">`라서 누르면 검색을 잃고 도서 목록으로 가버린다. JS로 정확히 패널 탭만 클릭한다:

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const cands = Array.from(document.querySelectorAll('button,[role="tab"]'))
    .filter(el => (el.textContent || '').trim() === '주석·실무서');
  const tab = cands.find(el => el.getAttribute('role') === 'tab') || cands[0];
  if (tab) tab.click();
  return JSON.stringify({ clicked: !!tab });
})()
```

[이 JS → `wait` 2초 → 1-3 카드 추출 → `get_page_text` → 복구]를 batch 하나로 보낸다(0장). (결과 0건이면 패널이 아직 로딩 중이거나 이 작업에 주석서 결과가 없을 수 있다. 검색 직후에는 패널이 결과를 받아 다시 그려지며 판례 탭으로 돌아가 `clicked:true`인데도 0건일 수 있다(2026-09-27 실측 2회) — 탭 클릭 JS부터 다시 보낸다.)

### 1-3. 카드 추출

주석·실무서 카드는 제목 앵커 `a[data-track-props]`(JSON `documentType:"scholar"`)이고, **href에 본문 식별자**(`/book/{bookId}?tocId=…&nodeId=…&volumeHistoryId=…`)가 들어 있다. 예외: **PDF형 실무서**(예: 민사집행실무총서 — book 87·85·88)는 href가 `/book/{bookId}?query=…&page=N`이고 tocId·nodeId·volumeHistoryId가 없다(2026-09-26 실측) — 카드의 `bodyType`이 `pdf`다. 카드 컨테이너는 `div.border-b-xs` = **앵커의 3단계 부모**(2026-09-26 실측)이며 직속 자식 3개 = ① 제목+도서메타, ② 스니펫, ③ breadcrumb.

> **`[BLOCKED]` 회피**: raw href·쿼리스트링·검색 키워드를 결과 JSON에 그대로 담으면 `[BLOCKED: Cookie/query string data]` 차단을 맞는다. href는 **식별자 값으로 분해해서**(bookId·nodeId·tocId·volumeHistoryId·page) 반환하고, 본문 URL은 5단계에서 그 값으로 재구성한다. `query` 파라미터(검색 키워드)는 읽지도 반환하지도 않는다.

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const txt = el => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const anchors = Array.from(document.querySelectorAll('a[data-track-props]')).filter(a => {
    try { return JSON.parse(a.getAttribute('data-track-props')).documentType === 'scholar'; } catch (e) { return false; }
  });
  const seen = new Set(), cards = [];
  let layoutWarn = 0;
  anchors.forEach(a => {
    let tp = {}; try { tp = JSON.parse(a.getAttribute('data-track-props')); } catch (e) {}
    let bookId = '', nodeId = '', tocId = '', volumeHistoryId = '', page = '';
    try {
      const u = new URL(a.href);                       // a.href는 읽기만; 반환하지 않음
      bookId = (u.pathname.match(/\/book\/(\w+)/) || [])[1] || '';
      nodeId = u.searchParams.get('nodeId') || '';     // 예: "lbox-paragraph-2014"
      tocId = u.searchParams.get('tocId') || '';
      volumeHistoryId = u.searchParams.get('volumeHistoryId') || '';
      page = u.searchParams.get('page') || '';         // PDF형 실무서만. query(검색 키워드)는 읽지 않음
    } catch (e) {}
    const key = [tp.docId || '', bookId, tocId, nodeId, page].join('|');
    if (key.replace(/\|/g, '') && seen.has(key)) return;   // 같은 카드가 두 번 잡히는 경우 대비
    seen.add(key);
    // 카드 = 앵커의 3단계 부모(A → 제목 → 제목블록 → 카드). 2단계는 제목블록이라 필드가 한 칸씩 어긋난다.
    const card = a.closest('div.border-b-xs') || (a.parentElement && a.parentElement.parentElement && a.parentElement.parentElement.parentElement) || null;
    if (!card || card.children.length !== 3) layoutWarn++;
    const kids = card ? Array.from(card.children) : [];
    const titleBlock = kids[0];
    const mk = titleBlock && titleBlock.querySelector('[class*="bg-orange-"],[class*="bg-azure-"]');   // 유형 표지
    const docType = /^실무서_/.test(tp.docId || '') ? '실무서' : mk ? (/bg-orange-/.test(mk.getAttribute('class') || '') ? '실무서' : '주석서') : '';
    cards.push({
      rank: tp.rank, docId: tp.docId, docType,
      sectionTitle: txt(a),
      bookMeta: titleBlock && titleBlock.children[1] ? txt(titleBlock.children[1]) : '', // 도서명 저자 출판사 날짜 판
      snippet: txt(kids[1]).slice(0, 500),
      breadcrumb: txt(kids[2]),
      bookId, nodeId, tocId, volumeHistoryId, page,
      bodyType: (tocId && nodeId) ? 'node' : (page ? 'pdf' : '')
    });
  });
  window.__lboxCommentary = cards;                      // 분할 접근용(현재 페이지 한정)
  return OUT(JSON.stringify({ count: cards.length, layoutWarn, emptyMeta: cards.length > 0 && cards.every(c => !c.bookMeta && !c.breadcrumb), items: cards }));
})()
```

- `bookMeta`는 "민법 채권각칙 제6권 김용덕 한국사법행정학회 2021. 10. 15. 제5판"처럼 도서명·편집대표·출판사·출판일·판본이 이어진 문자열이다. 답변의 출처 표기에 그대로 활용한다(필요하면 끝의 "제N판"·날짜를 분리).
- `docType`은 제목블록의 유형 표지(주황 `bg-orange-` = 실무서, 하늘 `bg-azure-` = 주석서, 2026-09-26 실측)와 docId 접두(`실무서_…` = PDF형 실무서)로 정한다. 빈값이면 도서명 규칙(`references/filter-map.md` 문서유형)으로 보조 판별한다.
- `bodyType`: `node`(tocId·nodeId 있음 → 본문 2-1), `pdf`(page만 있음 → 2-2). 같은 도서의 PDF형 카드는 bookId가 같으므로 중복 제거 키에 docId·page를 넣는다.
- **자가진단**: `count`가 앵커 수와 같아도 `layoutWarn > 0`이거나 `emptyMeta`가 true(bookMeta·breadcrumb가 전부 빈값, snippet이 도서명·저자·출판사 문자열)면 카드 컨테이너를 잘못 잡은 것이다(UI 변경) — 결과를 쓰지 말고 `references/troubleshooting.md`의 "카드 필드가 한 칸씩 어긋남"대로 셀렉터를 고친다.
- 반환값은 `OUT n자`뿐이고 카드 JSON은 이어지는 `get_page_text`로 받는다(0장 — 세 호출을 batch 하나로).
- `window.__lboxCommentary`는 페이지를 넘길 때마다 새로 쓰이므로, 페이지 간 중복은 각 페이지가 반환한 `items`를 대화 안에서 같은 키(docId·bookId·tocId·nodeId·page)로 한 번 더 거른다.

**셀렉터가 안 맞을 때**: `a[data-track-props]`가 0건이거나 자가진단에 걸리면 (ⓐ 주석·실무서 탭 활성, ⓑ 패널 열림)을 먼저 본다. UI가 바뀌었으면 아래 JS 프로브로 카드 앵커의 속성명과 조상 class를 확인해 셀렉터를 조정한다. `read_page`는 역할·텍스트·href만 보여 주고 class·data-* 속성은 보여 주지 않으므로 프로브는 JS로 한다. 결과에는 속성명·클래스명만 담고 href 값·검색어는 넣지 않는다(`[BLOCKED]` 회피).

```javascript
// 앵커 → 조상 5단계의 tag.class 전부 [data-*·href 속성명] — class를 자르지 않는다(카드를 가리는 class가 뒤쪽에 올 수 있다)
// 사이드바 작업 목록 링크도 a[data-track-props]라서 첫 앵커를 그냥 잡으면 안 된다(2026-09-27 실측) — documentType으로 거른다
(() => {
  const isScholar = a => { try { return JSON.parse(a.getAttribute('data-track-props')).documentType === 'scholar'; } catch { return false; } };
  const a = [...document.querySelectorAll('a[data-track-props]')].find(isScholar) || document.querySelector('a[href^="/book/"][href*="?"]');   // 대체 경로 — 좌측 내비 /book/list 제외
  if (!a) return 'no-anchor';
  const out = []; let e = a;
  for (let i = 0; i < 5 && e; i++, e = e.parentElement)
    out.push(e.tagName + '.' + String(e.className).trim().split(/\s+/).join('.') + ' [' + e.getAttributeNames().filter(n => n.startsWith('data-') || n === 'href').join(',') + ']');
  return JSON.stringify(out);
})()
```

### 1-4. 페이지 순회 · 필터

- 결과는 **10건/페이지**, 결과 리스트 하단 페이지네이션은 판례 탭과 같은 `« ‹ [번호 5개] › »`다('…' 없음). ‹·›는 이전·다음 5페이지 묶음, «·»는 첫·마지막 페이지다(aria-label 첫 페이지로 이동·이전으로 이동·다음으로 이동·마지막 페이지로 이동, 현재 페이지는 `button[aria-current=page]`, 2026-09-27 실측). **»는 누르지 않는다** — 마지막 페이지로 건너뛰어 중간 페이지가 빠진다.
- 다음 페이지: 아래 JS로 번호 버튼을 누른다 — 스크롤·스크린샷이 필요 없다. 현재 묶음에 N이 없으면 JS가 ›(다음으로 이동)를 눌러 묶음을 넘기고 `{advanced:true}`를 돌려준다. ›는 다음 묶음의 첫 페이지로 바로 이동한다(2026-09-27 실측 '소멸시효' 500건: 2페이지에서 N=6 → 6페이지 rank 51~60). 같은 batch에서 `wait` 2초 뒤 같은 JS를 다시 보내 N을 누른다. 묶음을 여럿 건너야 하면 `clicked:true`가 나올 때까지 [JS → `wait` 2초]를 되풀이한다(실측: 12페이지 → 50페이지, advanced 7회 뒤 clicked, rank 491~500).
- `{lastBlock:true}`면 N이 마지막 페이지를 넘은 것이므로 순회를 끝낸다(마지막 묶음에서는 ›가 disabled다. 실측: '채권양도' 197건 — 20페이지 rank 191~197의 7건, N=21 → lastBlock. 500건 — N=51 → lastBlock). `no-pagination`이면 결과가 한 페이지뿐이거나(실측 2건: 페이지네이션 자체가 없다) 패널이 닫혔거나 탭이 바뀐 것이다(카드 수로 가린다).
- 판례 탭 JS(`lbox-case-search` extraction.md 1-3)와 DOM이 같아 그대로 쓰되, 모달(role=dialog) 분기는 뺐다 — 주석·실무서 탭에는 페이지네이션이 든 모달이 없다. '법령별 도서목록' 모달도 role=dialog이지만 페이지네이션이 없으니(2026-09-27 실측), 열려 있으면 `Escape`로 닫고 순회한다.

```javascript
// N = 갈 페이지 번호(문자열) — 현재 페이지보다 큰 값만(뒤로 가기는 지원하지 않는다). 같은 batch에서 wait 2초 → 카드 추출 → get_page_text → 복구를 잇는다.
// ›(다음으로 이동)는 다음 5페이지 묶음, »(마지막 페이지로 이동)는 쓰지 않는다.
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const N = '2';
  const isNum = b => /^\d+$/.test((b.textContent || '').trim());
  const next = document.querySelector(`button[aria-label='다음으로 이동']`);
  let bar = next && next.parentElement;
  for (let i = 0; i < 3 && bar && !Array.from(bar.querySelectorAll('button')).some(isNum); i++) bar = bar.parentElement;
  const nums = bar ? Array.from(bar.querySelectorAll('button')).filter(isNum) : [];
  if (!nums.length) return JSON.stringify({ clicked: false, reason: 'no-pagination' });
  const shown = nums.map(b => +b.textContent.trim());
  const btn = nums.find(b => b.textContent.trim() === N);
  const off = next.disabled || next.getAttribute('aria-disabled') === 'true';
  if (btn) { btn.click(); return JSON.stringify({ clicked: true, shown }); }
  if (+N > Math.max(...shown) && !off) { next.click(); return JSON.stringify({ clicked: false, advanced: true, shown }); }
  return JSON.stringify({ clicked: false, shown, lastBlock: off });
})()
```

- 추출 뒤 복구하면 페이지 넘기기가 그대로 된다(실측: 11페이지 카드 추출 → `get_page_text` → 복구 → 12페이지 rank 111~120).

- 법령·문서유형으로 좁히려면 주석·실무서 탭의 유형 칩과 '법령별 도서목록' 모달을 `screenshot`으로 확인해 `computer`로 조작하거나, 결과의 `bookMeta`·`breadcrumb`으로 선별한다(`references/filter-map.md`).

## 2. 본문 섹션 추출 (`/book/{bookId}` 뷰어)

본문 뷰어는 두 유형이다. 카드 `bodyType`으로 가른다: `node`(주석서, 엘박스 스칼라 실무서 — book 82·115 등) → 2-1, `pdf`(민사집행실무총서 등 PDF형 실무서) → 2-2.

### 2-1. nodeId형 본문

식별자로 본문 URL을 구성해 `navigate`한다:

```
https://lbox.kr/book/{bookId}?tocId={tocId}&nodeId={nodeId}&volumeHistoryId={volumeHistoryId}
```

핵심 메커니즘은 개편 전과 같다: URL의 `nodeId`에 해당하는 `[data-node-id]` 요소가 섹션 시작 **헤더**(`data-viewer-type="header"`)이고, 다음 `[data-viewer-type="header"]` 전까지의 형제 paragraph가 그 섹션 본문이다.

> **중복 렌더 대비**: 과거(2026-07) 본문 DOM이 2벌로 그려지는 것이 관찰됐다. 2026-09-26/27 실측(book 38·53·74·78·115)은 1벌이다. 다시 2벌이 되어도 `headers[0]`의 형제만 걷고 이미 본 `data-node-id`는 건너뛰므로 결과는 같다(보험).
>
> **섹션이 클 수 있음**: `nodeId`가 "Ⅳ. 인신사고로 인한 손해액의 산정" 같은 **상위 장**을 가리키면 그 장 전체(수만 자)가 한 섹션이다. 전문을 다 받으면 컨텍스트를 낭비하므로 `window.__lboxSection`에 저장하고 **질의 키워드 인근만 발췌**해 내놓는다.

[`navigate` → `wait` 3초 → 이 JS → `get_page_text` → 복구]를 batch 하나로 보낸다(0장):

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const targetId = new URL(location.href).searchParams.get('nodeId');   // 읽기만; 반환하지 않음
  const useFirst = false;   // 절·장 제목 노드(아래 header not found 판별)면 true로 바꿔 viewer의 첫 header부터 읽는다
  const hs = document.querySelectorAll('[data-viewer-type="header"]');
  const header = document.querySelector('[data-node-id="' + (targetId || '') + '"]') || (useFirst ? hs[0] : null); // 첫 copy
  if (!header) return JSON.stringify({ error: 'header not found', viewerHeaders: hs.length,   // 진단값 — 짧아서 직접 반환
    nodeCount: document.querySelectorAll('[data-node-id]').length, pdfViewer: !!document.querySelector('.rpv-core__viewer'),
    firstHeader: hs[0] ? (hs[0].innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80) : '' });
  const sibs = Array.from(header.parentElement.children);
  const start = sibs.indexOf(header);
  const seen = new Set(), parts = [];
  for (let i = start; i < sibs.length; i++) {
    const el = sibs[i];
    if (i > start && el.getAttribute('data-viewer-type') === 'header') break;  // 다음 섹션 시작
    const id = el.getAttribute('data-node-id');
    if (id) { if (seen.has(id)) continue; seen.add(id); }                        // 중복 렌더 대비(보험)
    const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (t) parts.push(t);
  }
  const full = parts.join('\n');
  window.__lboxSection = full;
  // 질의 특화 키워드(1~3개)를 매 검색마다 교체. 없으면 앞부분.
  const kw = [/* 예: '양도금지특약','대항요건' */];
  let i = -1; for (const k of kw) { i = full.indexOf(k); if (i >= 0) break; }
  return OUT(JSON.stringify({
    sectionTitle: parts[0] || '',
    nodeCount: parts.length,
    sectionLen: full.length,
    excerpt: i >= 0 ? full.slice(Math.max(0, i - 300), i + 3000) : full.slice(0, 3500)
  }));
})()
```

- **추가 발췌**: 더 필요하면 0장의 두 줄 뒤에 `OUT(window.__lboxSection.slice(START, END))`를 실행해 범위를 옮겨 받는다(한 번에 12,000자 안팎까지).
- **`header not found`**: 함께 나온 진단값으로 원인을 가른다. ⓐ `nodeCount` 0 — `pdfViewer`가 true면 PDF형(구조 차이)이다. retry하지 말고 2-2로 간다(적재 초기에는 canvas가 없을 수 있어 canvas 유무로 가리지 않는다). false면 로드 미완이다 — `wait` 3초 뒤 1회 retry. ⓑ `viewerHeaders` ≥ 1인데 대상만 없고 카드 스니펫이 섹션 제목 그대로(예: '제3절 소송구조 …')면 절·장 제목 노드다. viewer는 이 노드를 그리지 않고 첫 하위 섹션(`firstHeader`)을 보여 주므로 retry해도 매번 같다(2026-09-27 실측 book 45: '제3절 소송구조' → '[총설]'). retry 없이 같은 breadcrumb의 첫 하위 노드 카드로 가거나, `useFirst`를 true로 바꿔 `firstHeader`부터 읽는다. ⓒ 그 밖(UI 변경 가능) — 한 번 retry 후에도 실패하면 그 카드 건너뜀. `nodeId`를 JS 코드에 임베드해 반환 JSON에 노출하면 `[BLOCKED]` 위험이 있으니, URL에서 읽어 쓰되 결과로 반환하지 않는다.
- **본문 빈약(조문 노드)**: SKILL 5.1 절차 — 같은 도서·breadcrumb 부모의 하위 노드 카드를 추가 visit.

### 2-2. PDF형 실무서 본문

PDF형 실무서(카드 `bodyType` `pdf`, 또는 docId가 `실무서_…pdf_N`)는 React PDF Viewer가 쪽을 canvas로 그리고 글은 텍스트 레이어에만 있다. `[data-node-id]`가 없어 2-1 JS는 늘 `header not found`다.

**기본은 본문을 열지 않는다**(카드당 1~2분 대기와 숫자 대조 비용). 스니펫·도서·page만 보고서에 적고 `본문 미확인(PDF형 실무서)`로 표시한다. 그 실무서가 핵심 쟁점의 **유일한 근거**일 때(예: 보전·집행 절차 실무를 주석서·nodeId형 도서로 확인할 수 없을 때)만 아래 절차로 연다.

1. **조회**: `navigate` → `https://lbox.kr/book/{bookId}?page={page}` → `wait` 10초 → 프로브 → (목표 레이어에 `T`가 붙으면) 아래 추출 JS → `get_page_text` → 복구. 목표 레이어가 없으면 `wait` 10초 → 프로브를 되풀이한다. **상한은 약 60초, 백그라운드 탭(`document.visibilityState` `hidden`)이면 약 120초**다(2026-09-27 실측 page=481, hidden: 45초까지 빈 뷰어 → 65초 표지 layer 0만 → 90초 목표 부근 레이어 생성 → 약 110초에 목표 레이어 텍스트·canvas). 적재 시간은 크게 흔들린다 — 상한 절반이 지나도 프로브가 빈값(뷰어 spinner만)이면 `screenshot`으로 확인한 뒤 뷰어에서 `scroll` 2틱 → 프로브를 이어 간다(2026-09-27 실측 page=1088, hidden: 스크롤 전 약 100~135초 무적재, 스크롤 뒤 표지부터 적재). 적재가 시작돼도 목표 쪽으로 점프하지 않을 수 있으므로(같은 실측: 144~190초 이상 layer 0~4뿐) 상한에서 멈추고 아래 5(실패)로 간다. 프로브는 레이어 번호만 반환한다(텍스트가 있으면 `T`):

```javascript
if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; } Array.from(document.querySelectorAll('[data-testid^="core__page-layer-"]')).map(pl => pl.getAttribute('data-testid').replace('core__page-layer-', '') + (pl.querySelector('.rpv-core__text-layer') ? 'T' : '')).join(',')
```

2. **추출 JS**:

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  // 접두 선택자는 로딩 자리표시(core__page-layer-loading-N)까지 잡으므로 라벨이 숫자인 레이어만 쓴다
  const L = Array.from(document.querySelectorAll('[data-testid^="core__page-layer-"]')).map(pl => {
    const id = pl.getAttribute('data-testid').replace('core__page-layer-', '');
    const t = pl.querySelector('.rpv-core__text-layer');
    const s = t ? (t.textContent || '') : '';
    return (/^\d+$/.test(id) && t) ? { layer: +id, len: s.length, text: s.replace(/\s+/g, ' ').trim().slice(0, 3000) } : null;
  }).filter(Boolean);
  return OUT(JSON.stringify({ n: L.length, layers: L }));
})()
```

3. **목표 쪽 확인**: 목표 레이어 번호는 page 값과 같다(2026-09-27 실측 page=481 → layer 481, 화면 인쇄 446쪽 'Ⅳ. 추심' = 카드 제목). 뷰어는 표지(layer 0)부터 적재하고 page 점프는 늦으며, 점프 뒤에도 앞쪽 레이어(477·478)부터 텍스트가 잡힌다 — layer = page인 항목이 없으면 본문으로 쓰지 않는다. page 값은 PDF 쪽 순번이라 인쇄 면수와 다르다(481 → 446쪽). **복구하면 뷰어가 맨 위(표지)로 돌아간다**(body 교체로 스크롤 위치가 사라짐) — `screenshot`·`zoom` 전에 JS `document.querySelector('[data-testid="core__page-layer-{page}"]').scrollIntoView()` → `wait` 2초로 목표 쪽을 다시 띄우고, 화면의 쪽 표시로 확인한다.
4. **품질**: 텍스트 레이어에서는 숫자·조문 번호가 제자리에서 빠져 뒤로 밀리거나 사라지고(화면 "제233조의 … 신청" → 텍스트 "…신233청", 화면 "제243조 제2항" → 텍스트 "제 조( 243제 항"), 서식 박스 안 글이 빠진다. 인용할 조문 번호·숫자·날짜·금액은 `computer` `zoom` 화면으로 대조한 것만 쓴다.
5. **실패**: 상한 안에 목표 쪽 레이어가 없으면 스니펫·도서·page만 적고 `본문 미확인(PDF형 실무서)`로 표시한다.
