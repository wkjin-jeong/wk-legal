# bigcase-case-search 추출 기술 reference

SKILL.md 본문이 지시하는 시점에 해당 장만 읽는다. 모든 셀렉터·JS·파라미터 값은 2026-07-31 라이브 검증본이다. bigcase는 검색·본문 모두 서버 렌더 + 일반 DOM이며, 별도 API 호출·페이로드 복원이 필요 없다.

> **클래스 안정성 규칙**: BEM식 클래스(`search-list-card`, `page-search-list__list-wrap`, `appealed-case-side__item`, `ai-similar-side__item`, `literature-case-side__card-wrap`, `pagination__number-item` 등)는 안정적이다. 반면 해시 접미사 클래스(`CaseParagraph_container__MdKLK`, `CaseContentInfo_container__po5LO`, `FilterItem_container__a_kTv` 등)는 배포 시 접미사가 바뀔 수 있으므로 **반드시 프리픽스 매칭**(`[class^="CaseParagraph_container"]`, `[class*="tp__title"]`)으로 잡는다.

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

상단 탭(판례·법령·주해/주석서·논문·결정례·유권해석·행정심판례 등)에서 **판례**가 기본 선택이다. 카드 루트는 `.search-list-card`(BEM 하위 요소 `search-list-card__title` 등과 구분하기 위해 클래스에 `__`가 없는 것만 취한다). 카드에 `<a href>`가 없으므로 **제목 텍스트를 파싱**해 본문 URL을 구성한다.

```javascript
(() => {
  const roots = Array.from(document.querySelectorAll('.page-search-list__list-wrap .search-list-card'))
    .filter(c => !String(c.className).includes('__'));
  const items = roots.map((c, i) => {
    const t = q => { const e = c.querySelector(q); return e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : ''; };
    const title = t('.search-list-card__title');
    // "{법원} {YYYY. M. D.} 선고|자 {사건번호} 판결|결정 {사건명}" — 법원명에 공백 가능(예: 춘천지방법원 강릉지원)
    const m = title.match(/^(.+?)\s+(\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\.)\s*(선고|자)\s+(\S+)\s+(판결|결정)\s*(.*)$/);
    const court = m ? m[1] : '', caseNo = m ? m[4] : '';
    return {
      idx: i + 1, title, court, date: m ? m[2] : '', caseNo,
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
  return JSON.stringify({ count: items.length, total, items });
})()
```

- `url`이 빈 카드(정규식 불일치 — 병합 사건번호 등)는 `title`을 보고 수동 판단한다(`references/troubleshooting.md`).
- 결과가 커서 truncated되면 `JSON.stringify(window.__bigcaseCards.slice(0,5))`처럼 인덱스로 분할해 받는다.
- `badge`가 `-`인 카드도 있다(주문 정보 미분류). triage에서는 스니펫을 우선한다.

### 1-3. 페이지 순회

- 결과는 **10건/페이지**. `&page={N}`을 붙여 `navigate`하는 것이 가장 단순하고 확실하다(하단 `.page-case-search-list__pagination`의 번호도 실제 `<a>` 링크다).
- 페이지 간 누적은 각 페이지의 `items`를 대화 안에서 모아 수행한다(`window.__bigcaseCards`는 페이지 이동 시 사라진다).

## 2. 판례 본문 추출 (`/cases/{법원명}/{사건번호}`)

```
https://bigcase.ai/cases/{encodeURIComponent(법원명)}/{encodeURIComponent(사건번호)}
```

법원명의 공백("춘천지방법원 강릉지원")도 그대로 인코딩하면 정상 작동한다(실측).

> **본문은 전문이 한 번에 렌더된다**: 전원합의체 장문 판결(약 60,000자) 실측에서 가상화·지연 적재·중복 렌더 없이 전 섹션이 DOM에 적재됐다. lbox의 2벌 렌더 dedup 같은 처리가 필요 없다.

섹션은 `[class^="CaseParagraph_container"]` 단위이고, 각 섹션의 제목 요소는 `[class*="tp__title"]`이다. 섹션 제목(공백 제거 기준): `판시사항`, `재판요지`, `참조조문`, `참조판례`, `주문`, `이유` (대법원 공보 판례 기준). **하급심(미공보) 판례는 `주문`, `청구취지`, `이유` 등 축소 구성**이 보통이며 판시사항·재판요지가 없어도 비정상이 아니다.

**(1) 머리부 + 구조 추출** — navigate 후 짧은 텀을 두고 실행:

```javascript
(() => {
  const txt = e => e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '';
  const secs = Array.from(document.querySelectorAll('[class^="CaseParagraph_container"]')).map(s => ({
    title: txt(s.querySelector('[class*="tp__title"]')).replace(/\s/g, ''),   // "주 문"→"주문"
    text: (s.innerText || '').trim()
  }));
  window.__bigcaseSecs = secs;                                     // 분할 접근용
  const get = n => (secs.find(s => s.title === n) || {}).text || '';
  const reason = get('이유');
  return JSON.stringify({
    title: document.title.replace(/\s*-\s*판례검색.*$/, ''),        // "법원 선고일 선고 사건번호 판결 사건명"
    outcome: txt(document.querySelector('[class*="CaseContentInfo"]')).slice(0, 200), // 주문 결과·중요판례 표시 포함
    sections: secs.map(s => ({ title: s.title, len: s.text.length })),
    issue: get('판시사항').slice(0, 2000),
    summary: get('재판요지').slice(0, 3000),                        // lbox '판결요지'에 해당
    refCases: get('참조판례').slice(0, 1500),
    order: get('주문').slice(0, 500),
    reasonLen: reason.length,
    lastReason: reason.slice(-180).replace(/\s+/g, ' ')             // 결론부 확인용
  });
})()
```

**적재(완전성) 판정**: `sections`에 `이유`가 있고 `lastReason`이 결론부("…주문과 같이 판결한다", "…의견을 밝힌다" 등)로 끝나면 정상. `sections`가 0개면 렌더 미완(짧은 텀 후 1회 재시도) 또는 구독·로그인 문제(`references/troubleshooting.md`)를 순서대로 점검한다.

**(2) 이유 전문 발췌** — `이유`가 길면(장문 판결 수만 자) 한 번에 반환 시 truncated되므로 `window.__bigcaseSecs`에서 키워드 인근만 받는다.

```javascript
// 질의 특화 키워드 인근 ±2500자 발췌. anchors 앞쪽은 매 검색마다 질의에서 뽑아 교체(공통어만 두지 말 것).
(() => {
  const full = ((window.__bigcaseSecs || []).find(s => s.title === '이유') || {}).text || '';
  const anchors = [/* 질의 특화: 예) '통상임금','고정성' */ '주문', '판단'];
  let i = -1; for (const k of anchors) { i = full.indexOf(k); if (i >= 0) break; }
  return JSON.stringify({ len: full.length, excerpt: i >= 0 ? full.slice(Math.max(0, i - 200), i + 2500) : full.slice(0, 3000) });
})()
// 더 필요하면 범위를 옮겨가며 .slice(START, END)
```

짧은 판례는 (1)의 결과만으로 충분할 수 있다.

## 3. 관련 판례 추적 (사이드바 + 본문 링크 — 전부 실제 `<a href>`)

lbox와 달리 관련 자료 링크가 모두 실제 `href`이므로 추출이 단순하다.

**(3-a) 상하급심 판례 체인** — 사이드바 "상하급심 판례 N". 현재 사건에는 `clicked` 클래스가 붙는다. **관계 라벨(파기환송/상고기각 등)은 제공되지 않으므로** 관계·결론은 각 본문의 주문에서 확인한다.

```javascript
(() => {
  const items = Array.from(document.querySelectorAll('a.appealed-case-side__item')).map(a => ({
    text: (a.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 90),  // "법원 선고일 선고 사건번호 …"
    path: (() => { try { return decodeURIComponent(new URL(a.href).pathname); } catch (e) { return ''; } })(),
    isCurrent: a.className.includes('clicked')
  }));
  return JSON.stringify({ chain: items });                         // path 앞에 https://bigcase.ai 붙여 navigate
})()
```

- 하급심 본문을 visit했다면 여기 나온 상급심을 **반드시 navigate**해 주문·이유를 확인한다(SKILL 4.5단계 (가)).
- 체인이 0건이면 짧은 텀 후 1회 재실행한 뒤에야 "상하급심 없음"으로 판정한다(사이드바 지연 렌더 대비).

**(3-b) 참조판례·이유 내 인용 판례** — 본문 섹션 안의 `/cases/` 링크. 참조조문·이유 속 법령 링크는 `/law/{법령명}/{조문}` 형태이므로 법령 확인이 필요하면 ko-law-api로 검증해 인용한다(bigcase 법령 페이지를 인용 출처로 쓰지 않는다).

```javascript
(() => {
  const path = a => { try { return decodeURIComponent(new URL(a.href).pathname); } catch (e) { return null; } };
  const secs = Array.from(document.querySelectorAll('[class^="CaseParagraph_container"]'));
  const cases = [...new Set(secs.flatMap(s => Array.from(s.querySelectorAll('a'))).map(path)
    .filter(p => p && p.startsWith('/cases/')))];
  return JSON.stringify({ citedCases: cases.slice(0, 40) });
})()
```

**(3-c) AI 유사판례 · 관련 논문** — 사이드바.

```javascript
(() => {
  const txt = e => e ? (e.innerText || '').replace(/\s+/g, ' ').trim() : '';
  const path = a => { try { return decodeURIComponent(new URL(a.href).pathname); } catch (e) { return null; } };
  const similar = Array.from(document.querySelectorAll('.ai-similar-side__item')).map(e => ({
    title: txt(e.querySelector('.ai-similar-side__item-title')).slice(0, 90),
    path: (Array.from(e.querySelectorAll('a')).map(path).filter(Boolean)[0]) || (e.tagName === 'A' ? path(e) : '')
  }));
  const papers = Array.from(document.querySelectorAll('.literature-case-side__card-wrap')).slice(0, 10)
    .map(e => txt(e.querySelector('.literature-case-side__card-title')).slice(0, 90));
  return JSON.stringify({ similar, paperTotal: document.querySelectorAll('.literature-case-side__card-wrap').length, papersTop: papers });
})()
```

- **AI 유사판례**는 사실관계가 닮은 사례 발굴(사례형 보강)에 쓴다. 검색 결과가 빈약할 때 High 판례의 유사판례를 따라가면 직접 사례를 찾는 경우가 있다.
- **관련 논문**은 제목만 수집해 보고서의 참고 목록으로 기재할 수 있다(선택). 논문 내용을 판례 법리처럼 인용하지 않는다.
