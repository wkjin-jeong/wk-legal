# lbox-case-search 추출 기술 reference

SKILL.md 본문이 지시하는 시점에 해당 장만 읽는다. 모든 셀렉터·JS는 2026.6 개편판 라이브 검증본이다. lbox는 Next.js(App Router) 기반이며, 본문은 서버 렌더되어 별도 검색/본문 API 호출 없이 DOM에 들어온다.

## 0. 반환·호출 규약 — 결과는 `get_page_text`로, 한 페이지는 batch 한 번에

`javascript_tool`의 반환값은 약 1,000자에서 `[TRUNCATED]`로 잘린다(Claude Code·Cowork 실측). 카드 10건은 3~4천 자, 본문 머리부는 3천 자를 넘기 쉬워 그대로 반환하면 매번 잘린다. 그래서 이 문서의 추출 JS는 결과를 반환하지 않고 **페이지에 내놓는다**: `OUT()`이 body를 결과 텍스트만 담은 `<pre>`로 잠시 바꾸고 `OUT n자`만 반환하면, `get_page_text`가 그 텍스트를 잘림 없이 돌려준다.

1. **한 페이지는 batch 한 번에**: [`navigate`·클릭 → `computer` `wait` 2~3초 → 추출 JS → `get_page_text` → (같은 페이지의 다음 추출 JS → `get_page_text`) → 복구 JS]를 `browser_batch` 하나로 보낸다. 결과는 각 `get_page_text` 출력의 `Source element: <body>` 아래 JSON이다. batch 도구가 없으면 차례로 호출한다.
2. **복구 JS**: `if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; } 'restored'` — 떼어 둔 원래 body를 그대로 되돌리므로 페이지 상태·이벤트가 유지된다(2026-09-26 실측: lbox 작업 결과 패널·판례 본문, bigcase 검색 결과 — 복구 뒤 페이지 넘기기·사이드바 펼치기 정상). **복구 전에는 `computer` 클릭·`screenshot`을 하지 않는다**(빈 화면을 누르게 된다). 아래 JS는 모두 첫 줄에서 먼저 복구하므로, batch가 중간에 끊겨도 다음 JS가 되살린다.
3. 한 번에 내놓는 양은 12,000자 안팎까지로 한다(컨텍스트 절약). 더 필요하면 범위를 옮겨 다시 내놓는다 — `window.__lboxCards`·`window.__lboxMain`에 저장해 둔 값에서 자르면 DOM을 다시 읽지 않아도 된다. 1,000자보다 확실히 작은 결과(탭 클릭 여부·건수 등)는 그대로 반환해도 된다.
4. `get_page_text`에 JSON이 아니라 원래 페이지 글이 나오면 내놓기가 안 된 것이다 — 추출 JS의 반환값(`OUT n자`인지 오류인지)을 보고 다시 보낸다. 페이지 전체를 `get_page_text`로 읽어 본문을 얻으려 하지 않는다(사이드바·메뉴가 섞인다).
5. **렌더 대기·재시도**: 이동·클릭 직후 곧바로 JS를 실행하면 렌더링과 충돌해 `Runtime.evaluate timed out`(45초)이 날 수 있어 batch 안에 `wait`를 먼저 둔다(2026-09-26 실측 — bigcase 판례 본문 2초, lbox 판례 본문 3초면 백그라운드 탭에서도 본문·사이드바까지 적재됐다). 추출 결과가 0건·빈값이면(렌더 미완) `wait` 3초 → 추출 묶음만 다시 보낸다(최대 3회. lbox 작업(Task)의 결과 패널은 검색이 끝나야 채워지므로 제출 직후에는 `wait` 3초씩 최대 5회. **작업 주소로 다시 들어갈 때**(새로고침·재진입)는 [`navigate` → `wait` 2초 → `computer` `zoom`(작은 영역, `scale` 0.3) 또는 `screenshot` 1회 → `wait` 3초 → 확인]으로 한다 — 백그라운드 탭(`document.visibilityState` 'hidden')에서는 화면을 한 번 그려야 작업 적재가 시작된다(2026-09-27 실측: 그리지 않으면 20초 넘게 스피너, `zoom` 한 번 뒤 약 5초에 timeline). 그래도 비면 5초씩 최대 5회 더 기다린다. timeline이 떴는데 패널이 닫혀 있으면 검색 카드를 누른다). timeout이 나면 그 페이지만 이동과 추출을 따로 보낸다. JS 실행 자체가 약 40초를 넘으면 `-32603 Internal error`로 강제 종료된다 — 대기는 JS가 아니라 `computer` `wait`로 한다(`references/troubleshooting.md`).
6. **`await`는 최상위에서**: JS 안에서 기다려야 하면 async IIFE로 감싸지 말고 최상위 `await`를 쓴다 — async IIFE는 결과 대신 `{}`가 돌아온다(실측). 긴 대기는 JS 반복문이 아니라 `computer` `wait`로 한다.

아래 JS는 모두 이 두 줄로 시작한다.

```javascript
if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(이전 내놓기가 남아 있으면)
const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
```

## 1. 검색 실행 · 결과 카드 추출 · 페이지 순회

### 1-1. 컴포저 "검색" 모드로 검색 실행

개편된 lbox에는 직접 검색 URL이 없다. 홈 컴포저에서 검색한다.

1. `navigate` → `https://lbox.kr/`.
2. `computer` `screenshot`으로 컴포저를 확인한다. 컴포저는 상단 가운데의 입력창(placeholder "내용을 입력하세요")이고, 그 아래 줄 오른쪽에 **모드 아이콘**이 있다: `[자동]`(라벨) · **돋보기(검색)** · 다이아(질의) · 펜(문서) · **↑(제출)**.
3. 입력창을 클릭하고 검색어를 `type` 한다.
4. **돋보기(검색) 아이콘**을 클릭한다. 선택되면 그 아이콘에 둥근 강조 박스(테마에 따라 흰색 또는 회색)가 생기고 툴팁은 "법률 콘텐츠 검색"이다(불확실하면 `computer` `zoom`으로 아이콘 영역을 확대해 확인). "자동" 모드로도 검색형 질의는 검색으로 라우팅되지만, 결정적으로 하려면 **검색 모드를 명시 선택**한다.
5. **↑(제출)** 을 클릭한다. `/task/{id}`로 이동하고 결과 패널이 열린다.

> 좌표는 화면 크기마다 다르므로 매번 `screenshot`으로 확인한다. `find`("composer input", "submit button")로 ref를 잡아 `computer{ref}` 클릭해도 된다. 제출 클릭 뒤 결과 추출은 같은 batch에서 `computer` `wait` 3초 뒤에 한다(0장). 결과 패널은 검색이 끝나야 채워지므로 카드가 0건이면 `wait` 3초 → 추출 묶음만 다시(최대 5회 — 재진입은 0장-5의 재진입 대기).

**같은 작업 안에서 재제출할 때**(재검색·폴백): 컴포저는 제출 뒤에도 직전 검색어를 남기고, 결과 패널의 필터(사건유형·법원·선고일)도 다음 재제출에 이어진다. 그대로 클릭하고 `type`하면 두 검색어가 이어 붙은 질의가 제출되고, 이어진 필터 때문에 거짓 0건이 나온다(2026-09-27 실측).

1. 하단 컴포저 입력창 클릭 → `cmd+a`(Windows는 `ctrl+a`) → `Delete`.
2. JS `document.querySelector('[contenteditable=true]').innerText.trim().length`가 0인지 확인한다(검색어 자체는 반환하지 않는다 — lbox-commentary-search와 같은 방식). 아니면 1.을 되풀이한다.
3. 새 검색어를 `type`하고 돋보기 선택을 `zoom`으로 확인한다. 모드는 같은 화면에서 이어 제출하면 유지되지만 작업을 다시 열면 "자동"으로 돌아가므로(2026-09-27 실측) 아니면 돋보기를 누른다.
4. 제출 → `wait` 3초 → 결과 패널의 "적용된 필터 N개"와 선고일 칩을 확인한다(JS `(document.body.innerText.match(/적용된 필터 (\d+)개/) || [])[1] || '0'`). 의도하지 않은 필터는 칩의 ×나 필터 줄 오른쪽의 "초기화"로 푼 뒤 추출한다(필터가 없으면 "적용된 필터" 줄이 사라져 JS는 '0').

**결과 패널이 닫혀 있을 때**(작업을 다시 열었거나 패널을 접은 경우 — 재진입 대기는 0장-5): timeline의 **검색 카드**(돋보기 아이콘 + 검색어, 오른쪽 ›, 필터를 건 검색은 필터 아이콘과 개수)를 클릭하면 패널이 다시 열린다. 패널이 timeline을 가리면 패널 오른쪽 위 ×로 먼저 접는다. 카드를 누르면 그 검색을 제출할 때의 필터와 검색어(컴포저 입력창)가 복원된다 — 이어서 재제출하려면 위 1.부터 한다. 패널이 열렸는지는 아래 카드 추출 JS의 `count > 0`으로 확인한다.

### 1-2. 결과 카드 추출 (판례 탭)

결과 패널 상단 탭에서 **판례** 탭이 기본 선택이다(필요 시 결정례·법령 등 다른 탭 클릭). 카드는 제목 앵커 `a[data-track-props]`이며, `data-track-props`는 `{"docId":"법원-사건번호","documentType":"precedent","rank":N}` 형태다(앵커에 `href="/case/{법원}/{사건번호}"`도 있지만 rank·documentType은 `data-track-props`에만 있으므로 기본은 `data-track-props`다. 셀렉터가 안 맞으면 `a[href^="/case/"]`를 대체 경로로 쓴다).

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const cards = Array.from(document.querySelectorAll('a[data-track-props]')).map(a => {
    let tp; try { tp = JSON.parse(a.getAttribute('data-track-props')); } catch (e) { return null; }
    if (!tp || tp.documentType !== 'precedent') return null;
    const docId = tp.docId || '';
    const dash = docId.indexOf('-');                 // 법원명엔 '-'가 없으므로 첫 '-'로 분리
    const court = dash > 0 ? docId.slice(0, dash) : '';
    const caseNo = dash > 0 ? docId.slice(dash + 1) : '';
    // 카드 컨테이너(스니펫·결과배지·인용/조회수 포함) = div.border-b-xs. 결과 리스트는 ul>div 구조로 li가 없다.
    // 폴백: 앵커 하나만 품는 조상 중 "조회 N"이 든 가장 가까운 것 — 현행 카드 = 앵커의 3단계 부모
    // (A→SPAN→제목블록→카드, 2026-09-27 실측). 앵커를 둘 이상 품으면(리스트 전체) 멈춘다.
    let card = a.closest('div.border-b-xs');
    if (!card) {
      let el = a, best = null;
      for (let i = 0; i < 5 && el.parentElement; i++) {
        el = el.parentElement;
        if (el.querySelectorAll('a[data-track-props]').length > 1) break;
        best = el;
        if (/조회\s*[\d,]+/.test(el.innerText || '')) break;
      }
      card = best || a;
    }
    const cardText = (card.innerText || card.textContent || '').replace(/\s+/g, ' ').trim();
    return {
      rank: tp.rank, docId, court, caseNo,
      title: (a.textContent || '').replace(/\s+/g, ' ').trim(),
      cardText: cardText.slice(0, 400),            // 사건명·스니펫·결과배지(파기환송 등)·인용N·조회N
      url: (court && caseNo) ? 'https://lbox.kr/case/' + encodeURIComponent(court) + '/' + encodeURIComponent(caseNo) : ''
    };
  }).filter(Boolean);
  const seen = new Set();                            // docId 기준 dedup(혹시 모를 2벌 렌더 대비)
  const uniq = cards.filter(c => { if (seen.has(c.docId)) return false; seen.add(c.docId); return true; });
  window.__lboxCards = uniq;                          // 분할 접근용(현재 페이지 한정)
  const diag = {                                      // 컨테이너 자가진단(아래 목록)
    distinct: new Set(uniq.map(c => c.cardText)).size,
    noView: uniq.filter(c => !/조회\s*[\d,]+/.test(c.cardText)).length
  };
  return OUT(JSON.stringify({ count: uniq.length, diag, items: uniq }));
})()
```

- `cardText`에는 결과 배지(파기환송/상고기각/원고일부승/청구기각 등)와 **인용 N**(이 판례를 인용한 판례 수)·**조회 N**이 포함된다. 인용 수가 큰 대법원 판례는 선례성이 높다는 신호이므로 triage에서 가중한다.
- **cardText 오염 자가진단**(`diag`): (ㄱ) `distinct`가 1이면(서로 다른 rank의 `cardText`가 전부 동일) 컨테이너가 리스트 전체에 안착한 것이다. (ㄴ) `noView`가 `count`와 같으면(모든 `cardText`에 "조회 N"이 없고 제목·사건명뿐) 컨테이너가 카드 안쪽 제목블록에 안착한 것이다. 둘 다 아래 "셀렉터가 안 맞을 때"의 JS 프로브로 카드 앵커의 속성명과 조상 class를 확인해 셀렉터를 조정한다(`read_page`는 class·data-* 속성을 보여 주지 않는다).
- `docId` → `url`(`/case/{법원}/{사건번호}`)은 4단계 본문 navigate에 그대로 쓴다. docId 사건번호에 `-1` 같은 접미가 붙은 것(예: `부산지방법원-2025가단49997-1`)은 접미까지 URL에 넣는다 — 접미를 빼면 열리지 않는다(2026-09-27 실측).
- 반환값은 `OUT n자`뿐이고 카드 JSON은 이어지는 `get_page_text`로 받는다(0장 — 세 호출을 batch 하나로).

### 1-3. 페이지 순회

- 결과는 **10건/페이지**, 결과 리스트 하단 페이지네이션은 `« ‹ [번호 5개] › »`다('…' 없음). ‹·›는 이전·다음 5페이지 묶음, «·»는 첫·마지막 페이지다(aria-label 첫 페이지로 이동·이전으로 이동·다음으로 이동·마지막 페이지로 이동, 2026-09-27 실측). 번호를 누르면 같은 패널이 다음 10건(rank 11~20 …)으로 갱신된다. **»는 누르지 않는다** — 마지막 페이지로 건너뛰어 중간 페이지가 빠진다(실측: 2,803건에서 281페이지로).
- 다음 페이지: 아래 JS로 번호 버튼을 누른다 — 스크롤·스크린샷이 필요 없다(2026-09-26 실측: 3을 누르자 21~30위로 갱신). 현재 묶음에 N이 없으면 JS가 ›(다음으로 이동)를 눌러 묶음을 넘기고 `{advanced:true}`를 돌려준다 — ›는 다음 묶음의 첫 페이지로 바로 이동하고(실측: 5 → 6, rank 51~60), 같은 batch에서 `wait` 2초 뒤 같은 JS를 다시 보내 N을 누른다. 현재 페이지 번호는 `button[aria-current=page]`다. `{lastBlock:true}`면 N이 마지막 페이지를 넘은 것이므로 순회를 끝낸다. `no-pagination`이면 결과가 한 페이지뿐이거나 패널이 닫힌 것이다(카드 수로 가린다).

```javascript
// N = 갈 페이지 번호(문자열) — 현재 페이지보다 큰 값만(뒤로 가기는 지원하지 않는다). 같은 batch에서 wait 2초 → 카드 추출 → get_page_text → 복구를 잇는다.
// ›(다음으로 이동)는 다음 5페이지 묶음, »(마지막 페이지로 이동)는 쓰지 않는다.
// 모달(role=dialog — 3장 따름 판례 "더보기")이 열려 있으면 모달 안 페이지네이션만 본다.
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const N = '2';
  const root = document.querySelector('[role=dialog]') || document;
  const isNum = b => /^\d+$/.test((b.textContent || '').trim());
  const next = root.querySelector(`button[aria-label='다음으로 이동']`);
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
- 페이지 간 누적은 각 페이지에서 받은 `items`를 대화 안에서 모아 수행한다(`window.__lboxCards`는 페이지 이동 시 갱신됨).

### 1-4. 필터 적용 (선택)

사건유형·법원·선고일 한정이 필요하면 결과 패널 상단의 필터 칩(**조건검색 · 사건유형 · 법원 · 주문유형 · 재판유형 · 선고일 · 키워드 알림**)을 클릭해 드롭다운에서 선택한다(예: 법원 → 대법원, 선고일 → 최근 N년). 필터는 `screenshot`으로 위치를 확인해 `computer`로 조작한다. 간단히는 검색어에 자연어로 녹여도 된다(예: "대법원 부당해고"). 필터는 검색(timeline 카드)마다 저장되어 같은 작업의 다음 재제출에 그대로 이어진다. 작업을 다시 열면(주소 재진입·새로고침) 필터 표시 없이 초기화되고, timeline 검색 카드를 누르면 그 검색의 필터가 복원된다(2026-09-27 실측).

### 셀렉터가 안 맞을 때

판례 카드(`a[data-track-props]` 중 `documentType:"precedent"`)가 0건이면 (ⓐ 패널이 닫힘 → timeline 검색 카드 클릭, ⓑ 판례 탭이 비활성 → 판례 탭 클릭, ⓒ UI 변경 → 아래 JS 프로브로 카드 앵커의 속성명과 조상 class를 확인해 셀렉터 조정 — `a[href^="/case/"]` 대체 경로) 순으로 점검한다. `read_page`는 역할·텍스트·href만 보여 주고 class·data-* 속성은 보여 주지 않으므로 프로브는 JS로 한다. 그래도 안 되면 사용자에게 알리고 중단한다.

```javascript
// 앵커 → 조상 5단계의 tag.class 전부 [data-*·href 속성명] — class를 자르지 않는다(카드를 가리는 class가 뒤쪽에 올 수 있다)
// 사이드바 작업 목록 링크도 a[data-track-props]라서 첫 앵커를 그냥 잡으면 안 된다(2026-09-27 실측) — documentType으로 거른다
(() => {
  const isCase = a => { try { return JSON.parse(a.getAttribute('data-track-props')).documentType === 'precedent'; } catch { return false; } };
  const a = [...document.querySelectorAll('a[data-track-props]')].find(isCase) || document.querySelector('a[href^="/case/"]');
  if (!a) return 'no-anchor';
  const out = []; let e = a;
  for (let i = 0; i < 5 && e; i++, e = e.parentElement)
    out.push(e.tagName + '.' + String(e.className).trim().split(/\s+/).join('.') + ' [' + e.getAttributeNames().filter(n => n.startsWith('data-') || n === 'href').join(',') + ']');
  return JSON.stringify(out);
})()
```

## 2. 판례 본문 추출 (`/case/{법원}/{사건번호}`)

> 본문 canonical URL은 `/case/{법원}/{사건번호}`다. 실측(2026-07): 구 `/precedent/{법원}/{사건번호}` 경로도 `/case/…`로 자동 리다이렉트되어 여전히 작동하나, 신규 구성은 `/case/`를 쓴다.

> **개편 핵심**: 본문은 이제 일반 DOM으로 렌더된다. 탭이 `visibilityState=hidden`이어도 `document.body.innerText`가 정상(전원합의체 장문 판결 실측 약 60,000자, 단락 전수 적재)이다. **개편 전의 `<script>` 페이로드 한국어 복원 방식은 폐기** — 페이지에 섞인 "최근 본 자료/추천 판례" 텍스트까지 끌어와 다른 사건 내용이 노이즈로 섞인다.

> **⚠ 단락이 2벌로 렌더된다 (필수 dedup)**: 본문 페이지는 같은 단락 DOM을 **두 번** 그린다(각 `data-node-id`가 정확히 2개씩 존재, 같은 `viewer-main-container` 아래). 그래서 셀렉터로 그냥 모으면 "주문"이 두 번, 판시사항도 두 번 나온다. **반드시 `data-node-id` 값 기준으로 dedup**(텍스트 기준 dedup은 동일 문장이 실제로 반복될 때 잘못 합쳐질 수 있으니 id 기준으로)한다. 아래 JS의 `sorted()` 헬퍼가 id-dedup을 포함한다. (참고: 2벌 렌더 때문에 `document.body.innerText`도 약 2배가 되므로, 본문 추출은 innerText가 아니라 이 셀렉터로 한다.)

본문 단락은 `data-node-id="lbox-paragraph-{prefix}-{n}"` 구조다. prefix별 의미:

| prefix | 내용 |
|---|---|
| `topheader-1` / `topheader-2`(/ `topheader-3`) | 법원(예: 대법원) / 판결 종류(판결·결정). **재판부명이 있는 판례는 `topheader-2`가 재판부명(예: 대법원 "제1부", 하급심 "제6민사부")이고 판결 종류는 `topheader-3`으로 밀린다**(2026-09-27 실측: 대법원 2026다202937 → 제1부/판결, 대법원 2012다89399 → 판결) — 판결 종류는 마지막 `topheader`에서 읽는다 |
| `before-*` | 사건정보 표(사건번호·당사자·**원심판결** 등). `before-1`이 전체 블록 |
| `issue-*` | **판시사항** |
| `summary-*` | **판결요지** |
| `main-*` | **본문**(주문·이유·의견). 전원합의체는 다수/반대/별개/보충의견 포함 |
| `judges-*` | 재판부(대법관·판사) |

**(1) 머리부 + 구조 추출** — [`navigate` → `wait` 3초 → 이 JS → `get_page_text` → (3-a) JS → `get_page_text` → 복구]를 batch 하나로 보낸다(0장):

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const txt = e => e ? (e.textContent || '').replace(/\s+/g, ' ').trim() : '';
  const sorted = pre => {                                    // id 기준 dedup(2벌 렌더 제거) + 번호순 정렬
    const pfx = 'lbox-paragraph-' + pre + '-', seen = new Set();
    return Array.from(document.querySelectorAll(`[data-node-id^="${pfx}"]`))
      .filter(e => { const id = e.getAttribute('data-node-id'); if (seen.has(id)) return false; seen.add(id); return true; })
      .sort((a, b) => parseInt(a.getAttribute('data-node-id').slice(pfx.length), 10)
                    - parseInt(b.getAttribute('data-node-id').slice(pfx.length), 10));
  };
  const join = pre => sorted(pre).map(txt).filter(Boolean).join('\n');
  const mains = sorted('main').map(txt).filter(Boolean);
  window.__lboxMain = mains;                                  // 본문 단락 배열(분할 접근용)
  const judges = sorted('judges').map(txt).filter(Boolean);
  const heads = sorted('topheader').map(txt).filter(Boolean);  // 법원 / (재판부) / 판결 종류
  const fullMain = mains.join('\n');
  return OUT(JSON.stringify({
    title: document.title.replace(/\s*[-|]\s*LBOX.*$/, ''),  // 법원·선고일·사건번호·[사건명] — 제목 끝은 ' | LBOX'(2026-09 실측)
    court: heads[0] || '',
    type:  heads.length > 1 ? heads[heads.length - 1] : '',   // 판결 종류 = 마지막 topheader
    bench: heads.length > 2 ? heads[1] : '',                  // 재판부명(있을 때만 topheader-2)
    caseInfo: txt(document.querySelector('[data-node-id="lbox-paragraph-before-1"]')).slice(0, 400), // 당사자·원심판결
    issue: join('issue'),       // 판시사항
    summary: join('summary'),   // 판결요지
    mainCount: mains.length,
    recoveredLen: fullMain.length,
    lastMain: (mains[mains.length - 1] || '').slice(-180),    // 결론부 확인용
    judges,
    loginWall: /로그인 후에 이용하실 수 있습니다/.test(document.body.innerText),   // 세션 만료·비로그인(리다이렉트 없음)
    notFound: /존재하지 않거나 삭제된 페이지/.test(document.body.innerText)        // 법원 표기 오류·미수록
  }));
})()
```

**적재(완전성) 판정**: `recoveredLen > 0` 이고 (`lastMain`이 "…주문과 같이 판결한다"·"보충의견을 밝힌다" 등 결론부로 끝나거나 `judges.length > 0`)면 정상이다. `mainCount`가 0이면 다음 순서로 가린다. ① `loginWall`이면 세션 만료 또는 비로그인이다(lbox는 리다이렉트하지 않고 같은 URL에서 본문 자리에 문구만 낸다) — 건너뛰지 말고 즉시 중단하고, 사용자에게 Chrome에서 lbox.kr 재로그인을 요청한다. ② `notFound`면 URL의 법원 표기 오류(SKILL 1단계 직조회 규칙으로 고쳐 1회 재시도)이거나 lbox 미수록이다. ③ 둘 다 아니면 렌더 미완이므로 `wait` 3초 뒤 다시 추출한다. "특정 한 건만 비면 건너뛰고 한 줄로 알림"은 이 셋을 가린 뒤에만 한다. **`innerText`가 0이라고 로그인 만료로 오판하지 말 것** — 다만 개편 후에는 보통 0이 아니다.

**(2) 본문 전문 발췌** — 장문 판결의 `main`은 수만 자라 전부 받으면 컨텍스트를 낭비하므로, `window.__lboxMain`에서 쟁점 키워드 인근만 내놓는다.

```javascript
// (2-a) 질의 키워드 인근 ±2500자 발췌. anchors = 질의 특화 키워드(1~3개)를 매 검색마다 갱신.
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const full = (window.__lboxMain || []).join('\n');
  const anchors = [/* 질의 특화: 예) '통상임금','고정성' */ '주문', '이유', '판단'];
  let i = -1; for (const k of anchors) { i = full.indexOf(k); if (i >= 0) break; }
  return OUT(JSON.stringify({ len: full.length, excerpt: i >= 0 ? full.slice(Math.max(0, i - 200), i + 2500) : full.slice(0, 3000) }));
})()
// (2-b) 더 필요하면 위 slice 범위를 옮기거나 넓혀 다시 실행(한 번에 12,000자 안팎까지)
```

- **질의 특화 키워드 갱신 필수**: `anchors`의 앞쪽 키워드는 매 검색마다 사용자 질의에서 뽑아 교체한다(예: 채권양도 질의 → `'채권양도'`,`'통지'`,`'대항요건'`). 공통어(`주문/이유/판단`)만 박아두지 말 것.
- 짧은 판례는 (1)의 결과만으로 충분할 수 있다. 길면 (2)로 쟁점 인근만 받는다.

## 3. 관련 판례 추적 (본문 페이지 우측 사이드바, href 아님 → data-track-props)

판례 본문 페이지의 우측 사이드바에 관련 자료가 구조화돼 있다. 링크는 앵커 `href`가 아니라 `data-track-click`/`data-track-props`를 쓰는 요소다.

**(3-a) 상·하위 판결(소송 진행 체인)** — 1심→2심→상고심. 관계 라벨이 텍스트에 포함된다.

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const seen = new Set(), items = [];
  Array.from(document.querySelectorAll('[data-track-click="upperLowerCaseItem"]')).forEach(el => {
    let tp = {}; try { tp = JSON.parse(el.getAttribute('data-track-props')) || {}; } catch (e) {}
    const docId = tp.docId || '';
    if (!docId || seen.has(docId)) return;                  // 사이드바도 2벌 렌더 → docId 기준 dedup
    seen.add(docId);
    const dash = docId.indexOf('-');
    const court = dash > 0 ? docId.slice(0, dash) : '';
    const caseNo = dash > 0 ? docId.slice(dash + 1) : '';
    items.push({
      docId, court, caseNo,
      label: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 70), // 예: "파기환송대법원 2012다89399"
      url: (court && caseNo) ? 'https://lbox.kr/case/' + encodeURIComponent(court) + '/' + encodeURIComponent(caseNo) : ''
    });
  });
  return OUT(JSON.stringify({ upperLower: items }));
})()
```

- `label`에 관계(원고승/원고패/파기환송/상고기각/원고일부승/확정 등) + 법원 + 사건번호가 들어 있다. 라벨은 체인의 관계 표시라 **환송 후 판결에도 자기 결과 대신 "파기환송"이 붙을 수 있다**(실측: 수원고등법원 2025나11716 — 주문 "…변경한다") — 흐름 표기의 각 심급 결론은 그 판결의 주문(2장 (1)의 `lastMain`, 또는 `window.__lboxMain`에서 "주문" 다음 단락 — 첫 `main` 단락은 "주문" 제목뿐이다)으로 적는다. **하급심 본문을 visit했다면 여기 나온 상급심 `url`을 반드시 navigate**해 결론을 확인한다(SKILL 4.5단계 (가)). 상급심 url이 열리지 않으면 SKILL 4.5단계 (가) ⑤.
- **사이드바는 본문보다 늦게 렌더될 수 있다**(실측: 본문 적재 완료 시점에 0건이었다가 직후 채워짐). (3-a) 결과가 0건이면 짧은 텀을 두고 **1회 재실행**한 뒤에야 "상·하위 판결 없음"으로 판정한다.

**(3-b) 인용된 판례 / 따름 판례 / 인용된 조문** — 사이드바의 접힘 섹션("인용된 판례 N", "따름 판례 N", "인용된 조문 N"). 펼쳐야 항목이 DOM에 들어온다.

1. 섹션 헤더를 펼친다. `screenshot` 좌표로 클릭하거나, JS로 화면에 보이는 사본의 헤더 button(`offsetParent !== null`이고 텍스트가 "따름 판례 N"·"인용된 판례 N")을 `click()`한다. `find` ref로 누르면 2벌 렌더된 숨은 사본에 걸려 펼쳐지지 않을 수 있다.
2. 인라인 항목을 아래 JS로 추출한다. 항목은 `[data-track-click=referenceCaseItem]`(인용된 판례)·`[data-track-click=followingCaseItem]`(따름 판례)이고, **props는 `{docId}`뿐이다 — documentType으로 거르면 0건**이다. docId 분리 규칙은 (3-a)와 같다. 핵심 대법원 선례만 골라 본문을 확인하거나 "참조 대법원 선례 정리" 표에 기록한다.

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const pick = k => {                                   // props는 {docId}뿐 — documentType으로 거르지 말 것
    const seen = new Set();
    return Array.from(document.querySelectorAll('[data-track-click=' + k + ']')).map(el => {
      let tp = {}; try { tp = JSON.parse(el.getAttribute('data-track-props')) || {}; } catch (e) {}
      return { docId: tp.docId || '', text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 90) };
    }).filter(x => x.docId && !seen.has(x.docId) && seen.add(x.docId));   // 2벌 렌더 dedup
  };
  const more = document.querySelector('[data-track-click=followingCasesMore]');
  return OUT(JSON.stringify({ cited: pick('referenceCaseItem'), following: pick('followingCaseItem'), followingMore: more ? more.textContent.trim() : null }));
})()
```

3. **따름 판례는 인라인에 최대 5건**(최신순 아님)이다. 전체가 필요하거나 최신 후속 판례를 확인하려면(정책 1.1-3·패키지 모드의 최신성) `followingCasesMore`("더보기 N건")를 클릭한다 → `computer` `zoom`(작은 영역, `scale` 0.3) 또는 `screenshot` 1회 → `wait` 3초 → 모달(role=dialog, "검색 결과 N개 | 관련도순", 10건/쪽)이 열린다. 백그라운드 탭에서는 화면을 한 번 그려야 모달이 붙는다(2026-09-27 실측: 그리지 않으면 20초가 지나도 role=dialog 없음, `zoom` 뒤 3초에 10건). 모달 안 항목이 0건이면 `wait` 3초 뒤 다시 추출한다. 여기서 1-2 카드 추출 JS를 그대로 실행한다(항목은 `resultSrp` 앵커, rank 없음). 다음 쪽은 1-3 JS로 넘긴다(모달이 열려 있으면 모달 안 페이지네이션을 누른다).
4. 최신성 확인이 목적이면 `orderByToggle`(누를 때마다 관련도순 ↔ 최신순)을 눌러 "최신순"으로 바꾸거나 "법원" 칩으로 대법원만 남긴 뒤 1쪽을 추출한다(실측: 2024다302217 — 인라인 5건에 없던 대법원 2026다201397이 모달에서만 보였다).
5. `Escape`로 모달을 닫는다. 같은 페이지에서 모달을 다시 열 때도 클릭 뒤 `zoom` 1회를 둔다.

**원심판결 직접 구성**: 사이드바 링크가 안 보여도, 본문 (1)의 `caseInfo`(`before-1`)에 "원심판결 ○○법원 …선고 ○○○○ 판결" 텍스트가 있으니 거기서 법원·사건번호를 읽어 `/case/{법원}/{사건번호}`로 직접 구성해 navigate할 수 있다. 원심 표기는 공식 판결문을 따라 약칭(대전지법·수원고법 등)인 경우가 많으므로, SKILL 1단계 직조회 규칙대로 정식 명칭으로 바꾸고 지원은 붙여 쓰고 원외재판부는 "재판부"를 빼서 구성한다. 사이드바 상·하위 판결의 docId가 있으면 그것을 먼저 쓴다(docId는 이미 lbox 표기다).
