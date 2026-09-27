# bigcase-case-search 추출 기술 reference

SKILL.md 본문이 지시하는 시점에 해당 장만 읽는다. 모든 셀렉터·JS·파라미터 값은 2026-07-31 라이브 검증본이다. bigcase는 검색·본문 모두 서버 렌더 + 일반 DOM이며, 별도 API 호출·페이로드 복원이 필요 없다.

> **클래스 안정성 규칙**: BEM식 클래스(`search-list-card`, `page-search-list__list-wrap`, `appealed-case-side__item`, `ai-similar-side__item`, `literature-case-side__card-wrap`, `pagination__number-item` 등)는 안정적이다. 반면 해시 접미사 클래스(`CaseParagraph_container__MdKLK`, `CaseContentInfo_container__po5LO`, `FilterItem_container__a_kTv` 등)는 배포 시 접미사가 바뀔 수 있으므로 **반드시 프리픽스 매칭**(`[class^="CaseParagraph_container"]`, `[class*="tp__title"]`)으로 잡는다.

## 0. 반환·호출 규약 — 결과는 `get_page_text`로, 한 페이지는 batch 한 번에

`javascript_tool`의 반환값은 약 1,000자에서 `[TRUNCATED]`로 잘린다(Claude Code·Cowork 실측). 카드 10건은 3~4천 자, 본문 머리부는 3천 자를 넘기 쉬워 그대로 반환하면 매번 잘린다. 그래서 이 문서의 추출 JS는 결과를 반환하지 않고 **페이지에 내놓는다**: `OUT()`이 body를 결과 텍스트만 담은 `<pre>`로 잠시 바꾸고 `OUT n자`만 반환하면, `get_page_text`가 그 텍스트를 잘림 없이 돌려준다.

1. **한 페이지는 batch 한 번에**: [`navigate`·클릭 → `computer` `wait` 2~3초 → 추출 JS → `get_page_text` → (같은 페이지의 다음 추출 JS → `get_page_text`) → 복구 JS]를 `browser_batch` 하나로 보낸다. 결과는 각 `get_page_text` 출력의 `Source element: <body>` 아래 JSON이다. batch 도구가 없으면 차례로 호출한다.
2. **복구 JS**: `if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; } 'restored'` — 떼어 둔 원래 body를 그대로 되돌리므로 페이지 상태·이벤트가 유지된다(2026-09-26 실측: lbox 작업 결과 패널·판례 본문, bigcase 검색 결과 — 복구 뒤 페이지 넘기기·사이드바 펼치기 정상). **복구 전에는 `computer` 클릭·`screenshot`을 하지 않는다**(빈 화면을 누르게 된다). 아래 JS는 모두 첫 줄에서 먼저 복구하므로, batch가 중간에 끊겨도 다음 JS가 되살린다.
3. 한 번에 내놓는 양은 12,000자 안팎까지로 한다(컨텍스트 절약). 더 필요하면 범위를 옮겨 다시 내놓는다 — `window.__bigcaseCards`·`window.__bigcaseSecs`에 저장해 둔 값에서 자르면 DOM을 다시 읽지 않아도 된다. 1,000자보다 확실히 작은 결과(탭 클릭 여부·건수 등)는 그대로 반환해도 된다.
4. `get_page_text`에 JSON이 아니라 원래 페이지 글이 나오면 내놓기가 안 된 것이다 — 추출 JS의 반환값(`OUT n자`인지 오류인지)을 보고 다시 보낸다. 페이지 전체를 `get_page_text`로 읽어 본문을 얻으려 하지 않는다(사이드바·메뉴가 섞인다).
5. **렌더 대기·재시도**: 이동·클릭 직후 곧바로 JS를 실행하면 렌더링과 충돌해 `Runtime.evaluate timed out`(45초)이 날 수 있어 batch 안에 `wait`를 먼저 둔다(2026-09-26 실측 — bigcase 판례 본문 2초, lbox 판례 본문 3초면 백그라운드 탭에서도 본문·사이드바까지 적재됐다). 추출 결과가 0건·빈값이면(렌더 미완) `wait` 3초 → 추출 묶음만 다시 보낸다(최대 3회). timeout이 나면 그 페이지만 이동과 추출을 따로 보낸다.
6. **`await`는 최상위에서**: JS 안에서 기다려야 하면 async IIFE로 감싸지 말고 최상위 `await`를 쓴다 — async IIFE는 결과 대신 `{}`가 돌아온다(실측). 긴 대기는 JS 반복문이 아니라 `computer` `wait`로 한다.

아래 JS는 모두 이 두 줄로 시작한다.

```javascript
if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(이전 내놓기가 남아 있으면)
const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
```

## 1. 검색 실행 · 결과 카드 추출 · 페이지 순회 · 필터

### 1-1. 검색 URL 구성

```
https://bigcase.ai/search/case?q={encodeURIComponent(검색어)}
```

선택 파라미터(전부 실측):

| 파라미터 | 값 | 비고 |
|---|---|---|
| `page` | 1부터 | 페이지 순회는 이 파라미터로 직접 이동 |
| `case_type` | 민사=1, 형사=5, 가사=8, 행정=9, 특허=10, 헌법=20 | 복수는 `_` 연결(예: `1_9`). "더보기" 속 항목(군사 등)은 UI 클릭으로 |
| `court` | 대법원=1000, 헌법재판소=5000 | 고등/지방 등 그룹 체크박스는 다수 코드로 전개되므로 URL 구성은 대법원·헌재만 권장, 그 외는 UI 클릭 |
| `decision_type` | 전체=0, 판결=1, 결정=2 | 재판 유형 라디오 |

- 정렬은 기본 **관련도순**, 문서범위 칩은 기본 **전문판례**를 그대로 쓴다(이들은 URL 파라미터가 아니다).
- **기간(선고일) 필터는 URL 수동 구성 금지** — 우측 "기간" 드롭다운(기본 라벨 "전체 기간")을 클릭해 "최근 1년/3년/5년/기간 직접 입력"을 선택한다(적용 시 `period_id`·`start_date`·`end_date`가 URL에 반영된다. 실측: 최근 3년=`period_id=2`). 드롭다운은 `computer` 클릭(스크린샷으로 위치 확인)이 확실하다.
- 필터 적용 여부는 결과 상단의 **적용 칩**(예: "결정 ×")과 URL 변화로 확인한다.

### 1-2. 결과 카드 추출 (판례 탭)

상단 탭(판례·법령·주해/주석서·논문·결정례·유권해석·행정심판례 등)에서 **판례**가 기본 선택이다. 결정례·유권해석·행정심판례 탭 자료를 인용 후보로 쓸 때 인용 메타(기관·일자·번호)는 카드·h1 라벨이 아니라 원문 머리에서 확인한다(4장). 카드 루트는 `.search-list-card`(BEM 하위 요소 `search-list-card__title` 등과 구분하기 위해 클래스에 `__`가 없는 것만 취한다). 카드에 `<a href>`가 없으므로 **제목 텍스트를 파싱**해 본문 URL을 구성한다.

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const roots = Array.from(document.querySelectorAll('.page-search-list__list-wrap .search-list-card'))
    .filter(c => !String(c.className).includes('__'));
  const items = roots.map((c, i) => {
    const t = q => { const e = c.querySelector(q); return e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : ''; };
    const title = t('.search-list-card__title');
    // "{법원} {YYYY. M. D.} 선고|자 {사건번호} [전원합의체] 판결|결정 {사건명}" — 법원명에 공백 가능(예: 춘천지방법원 강릉지원).
    // "전원합의체" 허용을 빼먹으면 전합 카드(핵심 선례일 가능성 높음)의 URL이 빈값이 된다(실측 결함 수정).
    const m = title.match(/^(.+?)\s+(\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.)\s*(선고|자)\s+(\S+)\s+((?:전원합의체\s+)?(?:판결|결정))\s*(.*)$/);
    const court = m ? m[1] : '';
    const caseNoFull = m ? m[4] : '';
    // 병합·본소/반소 사건("2023가단300454,2024가단298404", "2024가단212740(본소),…")은
    // 첫 사건번호(괄호 제거)만으로 접근한다 — 병합 전체 판결문이 그대로 로드됨(실측).
    const caseNo = caseNoFull.split(',')[0].replace(/\(.*$/, '');
    return {
      idx: i + 1, title, court, date: m ? m[2] : '', caseNo, caseNoFull,
      kind: m ? m[5] : '', caseName: m ? m[6] : '',
      snippet: t('.search-list-card__body-wrap').slice(0, 300),   // 검색어 하이라이트 포함 발췌
      badge: t('.search-list-card__footer-wrap').slice(0, 60),    // 주문 결과 배지(파기환송/원고패/기각/'-' 등)
      url: (court && caseNo) ? 'https://bigcase.ai/cases/' + encodeURIComponent(court) + '/' + encodeURIComponent(caseNo) : ''
    };
  });
  window.__bigcaseCards = items;                                   // 분할 접근용(현재 페이지 한정)
  const total = Array.from(document.querySelectorAll('span,div,p'))
    .map(e => e.childElementCount === 0 ? (e.innerText || '').trim() : '')
    .find(s => /건의 검색 결과/.test(s)) || '';
  return OUT(JSON.stringify({ count: items.length, total, items }));
})()
```

- `url`이 빈 카드(정규식 불일치)는 `title`을 보고 수동 판단한다(`references/troubleshooting.md`). 병합 사건·전원합의체는 위 JS가 이미 처리한다.
- 반환값은 `OUT n자`뿐이고 카드 JSON은 이어지는 `get_page_text`로 받는다(0장 — 세 호출을 batch 하나로. 10건 약 4천 자 실측).
- `badge`가 `-`인 카드도 있다(주문 정보 미분류). triage에서는 스니펫을 우선한다.

### 1-3. 페이지 순회

- 결과는 **10건/페이지**. `&page={N}`을 붙여 `navigate`하는 것이 가장 단순하고 확실하다(하단 `.page-case-search-list__pagination`의 번호도 실제 `<a>` 링크다) — [`navigate`(&page=N) → `wait` 2초 → 1-2 카드 JS → `get_page_text` → 복구]를 batch 하나로 보낸다(0장).
- 페이지 간 누적은 각 페이지의 `items`를 대화 안에서 모아 수행한다(`window.__bigcaseCards`는 페이지 이동 시 사라진다).

## 2. 판례 본문 추출 (`/cases/{법원명}/{사건번호}`)

```
https://bigcase.ai/cases/{encodeURIComponent(법원명)}/{encodeURIComponent(사건번호)}
```

법원명의 공백("춘천지방법원 강릉지원")도 그대로 인코딩하면 정상 작동한다(실측).

> **본문은 전문이 한 번에 렌더된다**: 전원합의체 장문 판결(약 60,000자) 실측에서 가상화·지연 적재·중복 렌더 없이 전 섹션이 DOM에 적재됐다. lbox의 2벌 렌더 dedup 같은 처리가 필요 없다.

섹션은 `[class^="CaseParagraph_container"]` 단위이고, 각 섹션의 제목 요소는 `[class*="tp__title"]`이다. 섹션 제목(공백 제거 기준): `판시사항`, `재판요지`, `참조조문`, `참조판례`, `주문`, `이유` (대법원 공보 판례 기준). **하급심(미공보) 판례는 `주문`, `청구취지`, `이유` 등 축소 구성**이 보통이며 판시사항·재판요지가 없어도 비정상이 아니다.

**(1) 머리부 + 구조 추출** — [`navigate` → `wait` 2초 → 이 JS → `get_page_text` → (3-a) 상하급심 JS → `get_page_text` → 복구]를 batch 하나로 보낸다(0장):

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const txt = e => e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '';
  const secs = Array.from(document.querySelectorAll('[class^="CaseParagraph_container"]')).map(s => ({
    title: txt(s.querySelector('[class*="tp__title"]')).replace(/\s/g, ''),   // "주 문"→"주문"
    text: (s.innerText || '').trim()
  }));
  window.__bigcaseSecs = secs;                                     // 분할 접근용
  const get = n => (secs.find(s => s.title === n) || {}).text || '';
  const reason = get('이유');
  return OUT(JSON.stringify({
    title: document.title.replace(/\s*-\s*판례검색.*$/, ''),        // "법원 선고일 선고 사건번호 판결 사건명"
    outcome: txt(document.querySelector('[class*="CaseContentInfo"]')).slice(0, 200), // 주문 결과·중요판례 표시 포함
    sections: secs.map(s => ({ title: s.title, len: s.text.length })),
    issue: get('판시사항').slice(0, 2000),
    summary: get('재판요지').slice(0, 3000),                        // lbox '판결요지'에 해당
    refCases: get('참조판례').slice(0, 1500),
    order: get('주문').slice(0, 500),
    reasonLen: reason.length,
    lastReason: reason.slice(-180).replace(/\s+/g, ' ')             // 결론부 확인용
  }));
})()
```

**적재(완전성) 판정**: `sections`에 `이유`가 있고 `lastReason`이 결론부("…주문과 같이 판결한다", "…의견을 밝힌다" 등)로 끝나면 정상. `sections`가 0개면 렌더 미완(짧은 텀 후 1회 재시도) 또는 구독·로그인 문제(`references/troubleshooting.md`)를 순서대로 점검한다.

**(2) 이유 전문 발췌** — 장문 판결의 `이유`는 수만 자라 전부 받으면 컨텍스트를 낭비하므로, `window.__bigcaseSecs`에서 쟁점 키워드 인근만 내놓는다.

```javascript
// 질의 특화 키워드 인근 ±2500자 발췌. anchors 앞쪽은 매 검색마다 질의에서 뽑아 교체(공통어만 두지 말 것).
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const full = ((window.__bigcaseSecs || []).find(s => s.title === '이유') || {}).text || '';
  const anchors = [/* 질의 특화: 예) '통상임금','고정성' */ '주문', '판단'];
  let i = -1; for (const k of anchors) { i = full.indexOf(k); if (i >= 0) break; }
  return OUT(JSON.stringify({ len: full.length, excerpt: i >= 0 ? full.slice(Math.max(0, i - 200), i + 2500) : full.slice(0, 3000) }));
})()
// 더 필요하면 위 slice 범위를 옮기거나 넓혀 다시 실행(한 번에 12,000자 안팎까지)
```

짧은 판례는 (1)의 결과만으로 충분할 수 있다.

## 3. 관련 판례 추적 (사이드바 + 본문 링크 — 전부 실제 `<a href>`)

lbox와 달리 관련 자료 링크가 모두 실제 `href`이므로 추출이 단순하다.

**(3-a) 상하급심 판례 체인** — 사이드바 "상하급심 판례 N". 현재 사건에는 `clicked` 클래스가 붙는다. **관계 라벨(파기환송/상고기각 등)은 제공되지 않으므로** 관계·결론은 각 본문의 주문에서 확인한다.

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const items = Array.from(document.querySelectorAll('a.appealed-case-side__item')).map(a => ({
    text: (a.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 90),  // "법원 선고일 선고 사건번호 …"
    path: (() => { try { return decodeURIComponent(new URL(a.href).pathname); } catch (e) { return ''; } })(),
    isCurrent: a.className.includes('clicked')
  }));
  return OUT(JSON.stringify({ chain: items }));                    // path 앞에 https://bigcase.ai 붙여 navigate
})()
```

- 하급심 본문을 visit했다면 여기 나온 상급심을 **반드시 navigate**해 주문·이유를 확인한다(SKILL 4.5단계 (가)).
- 체인이 0건이면 짧은 텀 후 1회 재실행한 뒤에야 "상하급심 없음"으로 판정한다(사이드바 지연 렌더 대비).

**(3-b) 참조판례·이유 내 인용 판례** — 본문 섹션 안의 `/cases/` 링크. 참조조문·이유 속 법령 링크는 `/law/{법령명}/{조문}` 형태이므로 법령 확인이 필요하면 ko-law-api로 검증해 인용한다(bigcase 법령 페이지를 인용 출처로 쓰지 않는다).

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const path = a => { try { return decodeURIComponent(new URL(a.href).pathname); } catch (e) { return null; } };
  const secs = Array.from(document.querySelectorAll('[class^="CaseParagraph_container"]'));
  const cases = [...new Set(secs.flatMap(s => Array.from(s.querySelectorAll('a'))).map(path)
    .filter(p => p && p.startsWith('/cases/')))];
  return OUT(JSON.stringify({ citedCases: cases.slice(0, 40) }));
})()
```

**(3-c) AI 유사판례 · 관련 논문** — 사이드바.

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const txt = e => e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '';
  const path = a => { try { return decodeURIComponent(new URL(a.href).pathname); } catch (e) { return null; } };
  const similar = Array.from(document.querySelectorAll('.ai-similar-side__item')).map(e => ({
    title: txt(e.querySelector('.ai-similar-side__item-title')).slice(0, 90),
    path: (Array.from(e.querySelectorAll('a')).map(path).filter(Boolean)[0]) || (e.tagName === 'A' ? path(e) : '')
  }));
  const papers = Array.from(document.querySelectorAll('.literature-case-side__card-wrap')).slice(0, 10)
    .map(e => txt(e.querySelector('.literature-case-side__card-title')).slice(0, 90));
  return OUT(JSON.stringify({ similar, paperTotal: document.querySelectorAll('.literature-case-side__card-wrap').length, papersTop: papers }));
})()
```

- **AI 유사판례**는 사실관계가 닮은 사례 발굴(사례형 보강)에 쓴다. 검색 결과가 빈약할 때 High 판례의 유사판례를 따라가면 직접 사례를 찾는 경우가 있다.
- **관련 논문**은 제목만 수집해 보고서의 참고 목록으로 기재할 수 있다(선택). 논문 내용을 판례 법리처럼 인용하지 않는다.

## 4. 비판례 자료(재결·유권해석·결정례) — 인용 메타 확인

행정심판례·유권해석·결정례 탭의 자료를 인용 후보로 돌려줄 때는 인용 메타(기관·일자·번호·표제)를 **PDF 원문 머리**에서 확인한다. 카드 footer·상세 h1의 기관명(예: '국민권익위원회')은 bigcase의 제공 기관 분류일 수 있다(실측: 경기도 사건 2017경기행심1480의 h1이 '국민권익위원회 2017경기행심1480 …'). 원문과 대조하지 않은 카드 라벨을 재결 기관·발신기관으로 쓰지 않는다.

- **머리 읽기**: 상세 페이지(`/administrative-appeal/{id}`·`/regulation-interpretation/{id}`·`/decision-case/{id}`)는 PDF 뷰어이고, 머리 정보는 첫 쪽 앞 300자 안에 있다(실측 4건). 백그라운드 탭에서는 PDF가 적재되지 않을 수 있으므로 `computer` `screenshot` → `wait` 3초 뒤 첫 쪽 머리를 `zoom`으로 읽는다.

1. **재결(행정심판례)**: 머리 '[○○행정심판위원회사건 {사건번호}, {재결일}, {결과}]'의 위원회명을 재결 기관으로 쓴다(예: 439952 → 경기도행정심판위원회 · 2017. 10. 30. · 2017경기행심1480). 재결 기관(행정심판위원회)은 행정심판법 제6조가 처분청에 따라 정한다 — 감사원·국가정보원장 등의 처분은 그 행정청에 두는 위원회(제1항), 그 밖의 국가행정기관의 장과 시·도지사 등의 처분은 국민권익위원회에 두는 중앙행정심판위원회(제2항), 시·도 소속 행정청과 관할 시·군·자치구의 장 등의 처분은 시·도지사 소속 행정심판위원회(제3항), 대통령령으로 정하는 특별지방행정기관의 장의 처분은 직근 상급행정기관에 두는 위원회(제4항). 어느 경우에도 '국민권익위원회'는 재결 기관명이 아니다(제2항의 경우 재결 기관명은 '중앙행정심판위원회'). 머리에 위원회명이 없으면(예: 3299 '[사건 2014-05850, 2014. 12. 2.]') 사건번호 체계(경기행심·서행심 등)나 카드 라벨로 추정하지 않고 `[재결 기관 확인 필요]`를 달아 돌려준다.
2. **유권해석**: 원문 표제('법령해석 회신' 등)·소관부처·회신일을 원문대로 쓴다. 원문에 기관명 없이 과 이름만 있으면(예: 486 '소관부처 금융소비자정책과') 카드 기관명을 '(bigcase 분류: 금융위원회)'로 병기하고 `[발신기관 확인 필요]`를 단다. 카드·h1 제목의 번호(예: 220200)는 사이트 일련번호다 — **일련번호를 문서번호로 표기하지 않고** '(bigcase 일련번호: 220200)'로만 병기한다. 원문에 문서번호가 없으면 문서번호 칸은 비운다.
3. **결정례**: 원문 머리의 기관·의결(결정)일·의결(의안)번호(예: 제2020-1소위1-복02호)를 쓴다. 국민권익위원회 고충민원 의결에는 원문 주문의 처리 유형을 확인해 자료 성격을 붙인다(「부패방지 및 국민권익위원회의 설치와 운영에 관한 법률」): 시정권고·의견표명(제46조)과 제도개선 권고·의견표명(제47조)은 '법적 구속력 없음(관계 행정기관등의 장에게 존중 의무와 30일 내 처리결과 통보 의무, 같은 법 제50조 제1항)', 조정(제45조)은 '「민법」상 화해와 같은 효력(같은 조 제3항)'. 유형을 확인하지 못하면 성격을 단정하지 않고 `[처리 유형 확인 필요]`를 단다. 결정례 탭의 다른 기관 결정(감사원 심사결정·공정거래위원회 의결·국세청 심사결정·개인정보보호위원회 의결 등)은 구속력을 단정하지 않고 원문 머리의 기관과 결정 유형만 적는다.

최종 인용 형식은 서면 스킬(ko-administrative-drafting·ko-legal-advisory-drafting)이 정한다. 이 스킬은 위 메타와 자료 성격을 판례 패키지 항목으로 돌려준다.
