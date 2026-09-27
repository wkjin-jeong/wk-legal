# bigcase-case-search 추출 기술 reference

SKILL.md 본문이 지시하는 시점에 해당 장만 읽는다. 모든 셀렉터·JS·파라미터 값은 2026-07-31 라이브 검증본이다(1-4, 2장 (1)의 판정 필드, 3-a의 `unavailable`, 4장은 2026-09-27). bigcase는 검색·본문 모두 서버 렌더 + 일반 DOM이며(판례 한정 — 비판례 상세는 PDF, 4장), 별도 API 호출·페이로드 복원이 필요 없다.

> **클래스 안정성 규칙**: BEM식 클래스(`search-list-card`, `page-search-list__list-wrap`, `appealed-case-side__item`, `ai-similar-side__item`, `literature-case-side__card-wrap`, `pagination__number-item` 등)는 안정적이다. 반면 해시 접미사 클래스(`CaseParagraph_container__MdKLK`, `CaseContentInfo_container__po5LO`, `FilterItem_container__a_kTv` 등)는 배포 시 접미사가 바뀔 수 있으므로 **반드시 프리픽스 매칭**(`[class^="CaseParagraph_container"]`, `[class*="tp__title"]`)으로 잡는다.

## 0. 반환·호출 규약 — 결과는 `get_page_text`로, 한 페이지는 batch 한 번에

`javascript_tool`의 반환값은 약 1,000자에서 `[TRUNCATED]`로 잘린다(Claude Code·Cowork 실측). 카드 10건은 3~4천 자, 본문 머리부는 3천 자를 넘기 쉬워 그대로 반환하면 매번 잘린다. 그래서 이 문서의 추출 JS는 결과를 반환하지 않고 **페이지에 내놓는다**: `OUT()`이 body를 결과 텍스트만 담은 `<pre>`로 잠시 바꾸고 `OUT n자`만 반환하면, `get_page_text`가 그 텍스트를 잘림 없이 돌려준다.

1. **한 페이지는 batch 한 번에**: [`navigate`·클릭 → `computer` `wait` 2~3초 → 추출 JS → `get_page_text` → (같은 페이지의 다음 추출 JS → `get_page_text`) → 복구 JS]를 `browser_batch` 하나로 보낸다. 결과는 각 `get_page_text` 출력의 `Source element: <body>` 아래 JSON이다. batch 도구가 없으면 차례로 호출한다.
2. **복구 JS**: `if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; } 'restored'` — 떼어 둔 원래 body를 그대로 되돌리므로 페이지 상태·이벤트가 유지된다(2026-09-26 실측: lbox 작업 결과 패널·판례 본문, bigcase 검색 결과 — 복구 뒤 페이지 넘기기·사이드바 펼치기 정상). **복구 전에는 `computer` 클릭·`screenshot`을 하지 않는다**(빈 화면을 누르게 된다). 아래 JS는 모두 첫 줄에서 먼저 복구하므로, batch가 중간에 끊겨도 다음 JS가 되살린다.
3. 한 번에 내놓는 양은 12,000자 안팎까지로 한다(컨텍스트 절약). 더 필요하면 범위를 옮겨 다시 내놓는다 — `window.__bigcaseCards`·`window.__bigcaseSecs`에 저장해 둔 값에서 자르면 DOM을 다시 읽지 않아도 된다. 1,000자보다 확실히 작은 결과(탭 클릭 여부·건수 등)는 그대로 반환해도 된다.
4. `get_page_text`에 JSON이 아니라 원래 페이지 글이 나오면 내놓기가 안 된 것이다 — 추출 JS의 반환값(`OUT n자`인지 오류인지)을 보고 다시 보낸다. 페이지 전체를 `get_page_text`로 읽어 본문을 얻으려 하지 않는다(사이드바·메뉴가 섞인다).
5. **렌더 대기·재시도**: 이동·클릭 직후 곧바로 JS를 실행하면 렌더링과 충돌해 `Runtime.evaluate timed out`(45초)이 날 수 있어 batch 안에 `wait`를 먼저 둔다(2026-09-26 실측 — bigcase 판례 본문 2초, lbox 판례 본문 3초면 백그라운드 탭에서도 본문·사이드바까지 적재됐다. 비판례 PDF 상세는 백그라운드 탭에서 적재되지 않는다 — 4장의 `screenshot` 유도). 추출 결과가 0건·빈값이면(렌더 미완) `wait` 3초 → 추출 묶음만 다시 보낸다(최대 3회). timeout이 나면 그 페이지만 이동과 추출을 따로 보낸다.
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
- **기간(선고일) 필터는 URL 수동 구성 금지** — 우측 "기간" 드롭다운(기본 라벨 "전체 기간")을 클릭해 "최근 1년/3년/5년/기간 직접 입력"을 선택한다(적용 시 `period_id`·`start_date`·`end_date`가 URL에 반영된다. 실측: 최근 3년=`period_id=2`). "기간 직접 입력"은 두 입력칸에 숫자 6자리(YYMMDD — 칸에는 YY.MM.DD로 표시)를 넣고 그 아래 "검색"을 누른다(`period_id=4`, 시작일 당일 포함 — 실측 2026-09-27: 26.07.09 시작에 2026. 7. 9. 선고 2025두34214 포함). 드롭다운은 `computer` 클릭(스크린샷으로 위치 확인)이 확실하다.
- 필터 적용 여부는 결과 상단의 **적용 칩**(예: "결정 ×")과 URL 변화로 확인한다.

### 1-2. 결과 카드 추출 (판례 탭)

상단 탭(판례·법령·주해/주석서·논문·결정례·유권해석·행정심판례 등)에서 **판례**가 기본 선택이다. 결정례·유권해석·행정심판례 탭 자료를 인용 후보로 쓸 때 인용 메타(기관·일자·번호)는 카드·h1 라벨이 아니라 원문 머리에서 확인한다(4장). 카드 루트는 `.search-list-card`(BEM 하위 요소 `search-list-card__title` 등과 구분하기 위해 클래스에 `__`가 없는 것만 취한다). 판례 탭 카드에는 `<a href>`가 없으므로(비판례 탭은 1-4 — href 있음) **제목 텍스트를 파싱**해 본문 URL을 구성한다.

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

### 1-4. 비판례 탭 — 행정심판례·유권해석·결정례

정책 1.1 특성 라우팅으로 결정례·유권해석·행정심판례를 찾을 때 쓴다. 상단 탭 `<a>`에는 href가 없으므로 탭을 누르지 말고 아래 URL로 직접 `navigate`한다(`q`·`page` 공통, 10건/페이지).

| 탭 | 검색 URL | 상세 URL |
|---|---|---|
| 행정심판례 | `/search/administrative-appeal?q={q}&page=N` | `/administrative-appeal/{id}` |
| 유권해석 | `/search/regulation-interpretation?q={q}&page=N` | `/regulation-interpretation/{id}` |
| 결정례 | `/search/decision-case?q={q}&page=N` | `/decision-case/{id}` |

카드 래퍼 클래스가 탭마다 다르고(`page-public-data-`·`page-regulation-interpretation-`·`page-decision-case-search-list__list-wrap`), 제목이 '{기관} {번호} {제목}' 꼴이며, '건의 검색 결과' 문구가 없어 1-2 JS로는 0건이 된다. 대신 카드 제목이 실제 링크(`a.search-list-card__title[href]`)다. [`navigate` → `wait` 2초 → 이 JS → `get_page_text` → 복구]를 batch 하나로 보낸다(0장).

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const roots = Array.from(document.querySelectorAll('.search-list-card')).filter(c => !String(c.className).includes('__'));   // 래퍼 무관
  const seen = new Set(), items = [];
  for (const c of roots) {
    const t = q => { const e = c.querySelector(q); return e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : ''; };
    const a = c.querySelector('a.search-list-card__title[href]') || c.querySelector('a[href]');
    const path = a ? new URL(a.href).pathname : '';                  // pathname만(쿼리 금지 — [BLOCKED] 방지)
    const title = t('.search-list-card__title'), footer = t('.search-list-card__footer-wrap').slice(0, 80);   // footer: '{일자} {기관}'
    const key = title + '|' + footer; if (seen.has(key)) continue; seen.add(key);   // 같은 자료의 중복 노출 제거 — 먼저 나온 href
    items.push({ idx: items.length + 1, title, footer, snippet: t('.search-list-card__body-wrap').slice(0, 300), url: path ? 'https://bigcase.ai' + path : '' });
  }
  window.__bigcaseCards = items;
  return OUT(JSON.stringify({ tab: location.pathname, raw: roots.length, count: items.length, dup: roots.length - items.length, items }));
})()
```

- 같은 자료를 두 id로 노출하기도 한다(실측: 행정심판례 서행심 2011-649 → 466051·6377, 결정례 제2024-205-156호 → 264669·270534). 위 JS가 제목+footer로 걸러 먼저 나온 것만 남긴다.
- 제목·footer의 기관명은 bigcase의 제공 기관 분류일 수 있다 — 인용 메타는 4장에서 원문으로 확인한다. 총 건수 문구가 없으므로 `total`은 두지 않는다.
- 페이지 순회: `raw`(중복 제거 전 카드 수)가 10 미만이 될 때까지 `&page=N`을 올리되 최대 5페이지. 중복 제거 뒤의 `count`로 판정하지 않는다(중복이 섞인 꽉 찬 페이지를 마지막으로 오판한다).
- 상세는 PDF 본문이다 — 4장 절차로 읽는다. 법제처 법령해석례는 ko-law-api `expc`가 정본이다.

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
  const bodyText = document.body.innerText;                        // OUT이 body를 바꾸기 전에 판정 신호를 계산
  const all = secs.map(s => s.text).join('\n');
  const imgs = Array.from(document.querySelectorAll('[class^="CaseParagraph_container"] img'));
  return OUT(JSON.stringify({
    title: document.title.replace(/\s*-\s*판례검색.*$/, ''),        // "법원 선고일 선고 사건번호 판결 사건명"
    outcome: txt(document.querySelector('[class*="CaseContentInfo"]')).slice(0, 200), // 주문 결과·중요판례 표시 포함
    sections: secs.map(s => ({ title: s.title, len: s.text.length })),
    issue: get('판시사항').slice(0, 2000),
    summary: get('재판요지').slice(0, 3000),                        // lbox '판결요지'에 해당
    refCases: get('참조판례').slice(0, 1500),
    order: get('주문').slice(0, 500),
    reasonLen: reason.length,
    lastReason: reason.slice(-180).replace(/\s+/g, ' '),            // 결론부 확인용
    concl: /주문과\s*같이\s*(판결|결정)한다/.test(reason),            // 결론부 문구가 이유 안에 있음(전원합의체는 뒤에 소수의견이 붙는다)
    paywall: !!document.querySelector('[class*="case-detail-restriction"]') || bodyText.includes('회원에게만 공개되는 판례'),   // 비로그인·구독 미적용 — 이유 절단본
    collecting: bodyText.includes('수집 중입니다'),                   // 사이트 미수록
    viewer: !!document.querySelector('.rpv-core__viewer'),          // PDF 뷰어 — 비판례 상세(4장)
    images: imgs.length,                                            // 본문 인라인 그림(상표·도형 등) — innerText에는 빈 자리로 남는다
    imageSecs: [...new Set(imgs.map(i => txt(i.closest('[class^="CaseParagraph_container"]').querySelector('[class*="tp__title"]')).replace(/\s/g, '')))].slice(0, 10),
    annex: Array.from(document.querySelectorAll('.case-detail-content__attached .attach-item__title')).map(txt).slice(0, 10),   // 별지 항목(섹션 밖, 접힌 상태에서도 제목은 있음)
    annexOmitted: /[\[(]\[?별\s?지[^\n]{0,20}생략\]?[\])]/.test(all),   // '[별지 생략]'·'[[별 지] 관계 법령: 생략]'
    annexRef: /(^|[^가-힣])별\s?지/.test(all)                         // 본문이 별지를 참조
  }));
})()
```

**적재(완전성) 판정** — 다음 순서로 본다.

1. `paywall` true → 로그인 세션 만료(또는 구독 미적용)다. 이유가 절단본이므로 인용하지 말고 중단한 뒤 bigcase.ai 재로그인을 안내한다(`references/troubleshooting.md`).
2. `collecting` true → 사이트 미수록이다(상급심이면 SKILL 4.5단계 (가) ⑤의 본문 부재 예외).
3. `viewer` true → PDF 본문이다(재결·유권해석·결정례 — 4장). 구독 문제가 아니다.
4. `sections` 0개 → 렌더 미완이다. `wait` 3초 뒤 재시도한다.
5. `이유`가 있고 `lastReason`이 결론부("…주문과 같이 판결한다", "…의견을 밝힌다" 등)로 끝나거나 `concl`이 true면 정상이다 — 전원합의체는 결론부 뒤에 별개·반대·보충의견이 붙어 "…논거를 보충한다" 등으로 끝난다(실측 2016다24284). 이유를 간략히 표시한 제1심 판결(민사소송법 제208조 제3항 — 무변론·자백간주·공시송달)은 "청구의 표시 … 민사소송법 제208조 제3항 제3호."처럼 짧게 끝나도 정상이다. 결론부 뒤의 "[별지 생략]" 등 생략 꼬리도 정상으로 보되, 이런 판결은 bigcase에서 별지를 얻을 수 없다(`annexOmitted`). 결론부가 아니면 절단을 의심하고, 재시도 뒤에도 같으면 인용을 보류한다.

`images`가 1 이상이면 `imageSecs` 섹션 글의 빈 자리(‘’ 등)는 그림이 빠진 것이다 — 그 문장을 인용하려면 원문 그림을 확인(해당 섹션을 띄워 `screenshot`/`zoom`)하거나 그림 표지를 단다. `annex`는 별지 항목 제목이다. 별지는 대개 스캔 그림이라 글로 받을 수 없다 — 복구 뒤 JS로 `const f = document.querySelector('.case-detail-content__attached .attach-item__face'); f.scrollIntoView({block:'center'}); f.click();`을 실행하고(`annex`가 여러 개면 `document.querySelectorAll('.case-detail-content__attached .attach-item__face')[n]`으로 `annex[n]` 항목을 누른다 — 항목마다 따로 펼쳐짐) `wait` 2초 뒤 `img.attach-item__content-image`를 `screenshot`/`zoom`으로 읽는다(실측 2026-09-27 인천지방법원 2023가단239014 — 백그라운드 탭에서도 그림이 적재됨). `annex`가 비었는데 `annexOmitted`나 `annexRef`가 참이면 bigcase 원문에 별지가 없는 것이다(생략·미수록, 실측 2023나31881) — 이미지 없는 본문으로 도면·별지 내용을 추정해 쓰지 않는다. '원심 판시 별지'처럼 다른 판결의 별지를 가리키는 참조도 `annexRef`에 걸린다(2011도12482) — 그 별지는 해당 판결에서 찾는다.

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
  const items = Array.from(document.querySelectorAll('a.appealed-case-side__item')).map(a => {
    const unavailable = a.className.includes('disabled');          // bigcase 원문 미보유 — href가 현재 사건을 가리킨다
    return {
      text: (a.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 90),  // "법원 선고일 선고 사건번호 …"(미보유는 "{법원} null" 꼴)
      path: unavailable ? '' : (() => { try { return decodeURIComponent(new URL(a.href).pathname); } catch (e) { return ''; } })(),
      isCurrent: a.className.includes('clicked'),
      unavailable
    };
  });
  return OUT(JSON.stringify({ chain: items }));   // unavailable 항목은 navigate하지 않는다. 나머지는 path 앞에 https://bigcase.ai 붙여 navigate
})()
```

- 하급심 본문을 visit했다면 여기 나온 상급심을 **반드시 navigate**해 주문·이유를 확인한다(SKILL 4.5단계 (가)).
- `unavailable: true` 항목(텍스트가 "{법원} null" 꼴)은 bigcase에 원문이 없다 — 판례DB(`case_db.py search --case-no`·원격 `search_cases`)나 lbox 사건번호 URL 직행(정책 1.1-5)으로 확인하고, 거기에도 없으면 SKILL 4.5단계 (가) ⑤의 본문 부재 예외를 따른다.
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

**(1) PDF 본문 받기** — 상세 페이지(`/administrative-appeal/{id}`·`/regulation-interpretation/{id}`·`/decision-case/{id}`)는 react-pdf-viewer(`.rpv-core__viewer`)가 PDF를 띄운다. 2장 JS는 섹션 0개·발췌 빈값을 돌려주지만 구독 문제가 아니다. 텍스트층은 보이는 쪽만 렌더되므로(실측: 10쪽 중 973자) `get_page_text`·스크롤로 읽지 않고 사이트 전역 `window.pdfjsLib`로 전문을 받는다. 백그라운드 탭에서는 `screenshot`을 찍기 전까지 PDF가 적재되지 않는다(실측 2026-09-27: 486 — `screenshot` 없이 `wait` 3초 뒤 `NO_PDF`, 찍은 뒤 정상).

batch 하나: [`navigate` → `computer` `screenshot`(scale 0.3 — 적재 유도) → `wait` 3초 → JS-A → JS-B → `get_page_text` → (JS-C → `get_page_text`) → 복구].

- JS-A의 `pdf`가 비면(JS-B가 `NO_PDF` — 이어진 `get_page_text`는 페이지 글이므로 버린다) [`screenshot` → `wait` 3초 → JS-A → JS-B → `get_page_text` → 복구]를 다시 보낸다(최대 2회). 그래도 없으면 그 건을 건너뛰고 한 줄로 알린다.
- JS-B가 `NO_PDFJS`면 사이트 전역이 없어진 것이다 — 사용자에게 알리고 중단한다.

JS-A(머리·PDF 확인) — 결과가 짧으므로 내놓지 않고 그대로 반환한다(body를 바꾸면 뷰어가 떨어져 나가 재시도 `screenshot`이 PDF를 적재시키지 못한다):

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const txt = e => e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '';
  const pdf = performance.getEntriesByType('resource').map(e => e.name).find(n => /\.pdf(\?|$)/.test(n));
  window.__bigcasePdfUrl = pdf || '';
  return JSON.stringify({ h1: txt(document.querySelector('h1')).slice(0, 150),
    meta: (document.body.innerText.match(/(의결일|회신일|재결일|결정일)[^\n]{0,40}/) || [''])[0],
    viewer: !!document.querySelector('.rpv-core__viewer'), pdfjs: !!window.pdfjsLib, pdf: pdf ? new URL(pdf).pathname : '' });
})()
```

JS-B(PDF 전문) — 최상위 `await`를 쓴다(async IIFE 금지 — 0장 6.). `javascript_tool`은 호출마다 최상위 `const`를 이어받지 않으므로 0장 두 줄을 그대로 둔다(없으면 `OUT is not defined`):

```javascript
if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
let r = 'NO_PDF';
if (!window.pdfjsLib) r = 'NO_PDFJS';
else if (window.__bigcasePdfUrl) {                                    // getDocument(undefined)는 부르지 않는다('Invalid parameter' 오류)
  const doc = await pdfjsLib.getDocument(window.__bigcasePdfUrl).promise;
  let t = '';
  for (let p = 1; p <= doc.numPages; p++) { const c = await (await doc.getPage(p)).getTextContent(); t += c.items.map(i => i.str).join(' ').replace(/\s+/g, ' ') + '\n'; }   // 쪽마다 한 줄, 공백 정리
  window.__bigcasePdfText = t;
  r = OUT(JSON.stringify({ pages: doc.numPages, len: t.length, head: t.slice(0, 400), tail: t.slice(-300) }));   // head: 인용 메타(2), tail: 완전성
}
r
```

JS-C(발췌) — 2장 (2)와 같은 방식으로 `window.__bigcasePdfText`에서 anchor ±2500자를 내놓는다. PDF 글은 '결 론'처럼 띄어 나오므로 anchor는 정규식으로 쓴다:

```javascript
(() => {
  if (window.__origBody) { document.body = window.__origBody; window.__origBody = null; }   // 복구(0장)
  const OUT = s => { if (!window.__origBody) window.__origBody = document.body; const b = document.createElement('body'), p = document.createElement('pre'); p.textContent = s; b.appendChild(p); document.body = b; return 'OUT ' + s.length + '자'; };
  const full = window.__bigcasePdfText || '';
  // 질의 특화 키워드를 앞에 둔다. 기본: 유권해석 '회답', 재결·결정례 판단부('나. 판 단'·'나. 판단 내용' → '4. 판단' → '5. 결론')
  const base = location.pathname.startsWith('/regulation-interpretation') ? [/회\s*답/]
    : [/[가-하]\.\s*판\s*단(?=\s)/, /\d\.\s*판\s*단(?=\s)/, /\d\.\s*결\s*론/];   // 맨 '판단'·'결론'은 당사자 주장 속 낱말에 먼저 걸린다(실측)
  const anchors = [/* 질의 특화: 예) /감\s*경/ */ ...base];
  let i = -1; for (const a of anchors) { i = full.search(a); if (i >= 0) break; }
  return OUT(JSON.stringify({ len: full.length, excerpt: i >= 0 ? full.slice(Math.max(0, i - 200), i + 2500) : full.slice(0, 3000) }));
})()
```

완전성: 재결은 '주문과 같이 재결한다', 결정례는 결론·주문 단락('주문과 같이 의결한다' 등), 유권해석은 '회답' 이후 끝까지가 있으면 정상이다(JS-B `tail`로 본다). PDF 글 속 `<img …>` 자리는 그림(별표 등)이 빠진 것이다 — 그 부분을 인용하려면 뷰어 화면을 `screenshot`/`zoom`으로 확인한다.

**(2) 인용 메타** — 머리 정보는 첫 쪽 앞 300자 안에 있다(실측 4건 — JS-B `head`).

1. **재결(행정심판례)**: 머리 '[○○행정심판위원회사건 {사건번호}, {재결일}, {결과}]'의 위원회명을 재결 기관으로 쓴다(예: 439952 → 경기도행정심판위원회 · 2017. 10. 30. · 2017경기행심1480). 재결 기관(행정심판위원회)은 행정심판법 제6조가 처분청에 따라 정한다 — 감사원·국가정보원장 등의 처분은 그 행정청에 두는 위원회(제1항), 그 밖의 국가행정기관의 장과 시·도지사 등의 처분은 국민권익위원회에 두는 중앙행정심판위원회(제2항), 시·도 소속 행정청과 관할 시·군·자치구의 장 등의 처분은 시·도지사 소속 행정심판위원회(제3항), 대통령령으로 정하는 특별지방행정기관의 장의 처분은 직근 상급행정기관에 두는 위원회(제4항). 어느 경우에도 '국민권익위원회'는 재결 기관명이 아니다(제2항의 경우 재결 기관명은 '중앙행정심판위원회'). 머리에 위원회명이 없으면(예: 3299 '[사건 2014-05850, 2014. 12. 2.]') 사건번호 체계(경기행심·서행심 등)나 카드 라벨로 추정하지 않고 `[재결 기관 확인 필요]`를 달아 돌려준다.
2. **유권해석**: 원문 표제('법령해석 회신' 등)·소관부처·회신일을 원문대로 쓴다. 원문에 기관명 없이 과 이름만 있으면(예: 486 '소관부처 금융소비자정책과') 카드 기관명을 '(bigcase 분류: 금융위원회)'로 병기하고 `[발신기관 확인 필요]`를 단다. 카드·h1 제목의 번호(예: 220200)는 사이트 일련번호다 — **일련번호를 문서번호로 표기하지 않고** '(bigcase 일련번호: 220200)'로만 병기한다. 원문에 문서번호가 없으면 문서번호 칸은 비운다.
3. **결정례**: 원문 머리의 기관·의결(결정)일·의결(의안)번호(예: 제2020-1소위1-복02호)를 쓴다. 국민권익위원회 고충민원 의결에는 원문 주문의 처리 유형을 확인해 자료 성격을 붙인다(「부패방지 및 국민권익위원회의 설치와 운영에 관한 법률」): 시정권고·의견표명(제46조)과 제도개선 권고·의견표명(제47조)은 '법적 구속력 없음(관계 행정기관등의 장에게 존중 의무와 30일 내 처리결과 통보 의무, 같은 법 제50조 제1항)', 조정(제45조)은 '「민법」상 화해와 같은 효력(같은 조 제3항)'. 유형을 확인하지 못하면 성격을 단정하지 않고 `[처리 유형 확인 필요]`를 단다. 결정례 탭의 다른 기관 결정(감사원 심사결정·공정거래위원회 의결·국세청 심사결정·개인정보보호위원회 의결 등)은 구속력을 단정하지 않고 원문 머리의 기관과 결정 유형만 적는다.

최종 인용 형식은 서면 스킬(ko-administrative-drafting·ko-legal-advisory-drafting)이 정한다. 이 스킬은 위 메타와 자료 성격을 판례 패키지 항목으로 돌려준다.
