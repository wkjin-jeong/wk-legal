# lbox-case-search 추출 기술 reference

SKILL.md 본문이 지시하는 시점에 해당 장만 읽는다. 모든 셀렉터·JS는 2026.6 개편판 라이브 검증본이다. lbox는 Next.js(App Router) 기반이며, 본문은 서버 렌더되어 별도 검색/본문 API 호출 없이 DOM에 들어온다.

## 1. 검색 실행 · 결과 카드 추출 · 페이지 순회

### 1-1. 컴포저 "검색" 모드로 검색 실행

개편된 lbox에는 직접 검색 URL이 없다. 홈 컴포저에서 검색한다.

1. `navigate` → `https://lbox.kr/`.
2. `computer` `screenshot`으로 컴포저를 확인한다. 컴포저는 상단 가운데의 입력창(placeholder "내용을 입력하세요")이고, 그 아래 줄 오른쪽에 **모드 아이콘**이 있다: `[자동]`(라벨) · **돋보기(검색)** · 다이아(질의) · 펜(문서) · **↑(제출)**.
3. 입력창을 클릭하고 검색어를 `type` 한다.
4. **돋보기(검색) 아이콘**을 클릭한다. 선택되면 그 아이콘에 흰색 둥근 박스 하이라이트가 생긴다(불확실하면 `computer` `zoom`으로 아이콘 영역을 확대해 확인). "자동" 모드로도 검색형 질의는 검색으로 라우팅되지만, 결정적으로 하려면 **검색 모드를 명시 선택**한다.
5. **↑(제출)** 을 클릭한다. `/task/{id}`로 이동하고 결과 패널이 열린다.

> 좌표는 화면 크기마다 다르므로 매번 `screenshot`으로 확인한다. `find`("composer input", "submit button")로 ref를 잡아 `computer{ref}` 클릭해도 된다. 제출 클릭과 결과 추출 `javascript_tool` 호출은 **분리**하고 사이에 짧은 텀을 둔다(timeout 회피).

**결과 패널이 닫혀 있을 때**(작업을 다시 열었거나 패널을 접은 경우): timeline의 **"○○ 검색 결과 / 검색"** 카드를 클릭하면 패널이 다시 열린다. 패널이 열렸는지는 아래 카드 추출 JS의 `count > 0`으로 확인한다.

### 1-2. 결과 카드 추출 (판례 탭)

결과 패널 상단 탭에서 **판례** 탭이 기본 선택이다(필요 시 결정례·법령 등 다른 탭 클릭). 카드는 제목 앵커 `a[data-track-props]`이며, `data-track-props`는 `{"docId":"법원-사건번호","documentType":"precedent","rank":N}` 형태다(앵커에 실제 `href`는 없고 JS 내비게이션이라 `href`로는 못 잡는다 — 반드시 `data-track-props`).

```javascript
(() => {
  const cards = Array.from(document.querySelectorAll('a[data-track-props]')).map(a => {
    let tp; try { tp = JSON.parse(a.getAttribute('data-track-props')); } catch (e) { return null; }
    if (!tp || tp.documentType !== 'precedent') return null;
    const docId = tp.docId || '';
    const dash = docId.indexOf('-');                 // 법원명엔 '-'가 없으므로 첫 '-'로 분리
    const court = dash > 0 ? docId.slice(0, dash) : '';
    const caseNo = dash > 0 ? docId.slice(dash + 1) : '';
    // 카드 컨테이너(스니펫·결과배지·인용/조회수 포함). 실측(2026-07): 결과 리스트는 ul>div 구조로
    // li가 없어, li 폴백이 ul(리스트 전체)에 안착하면 cardText가 전 카드 동일값으로 오염된다.
    // 카드 1개 단위 컨테이너는 div.border-b-xs. 부모 워크 폴백은 2단계까지만(카드 div = 앵커의 2단계 부모).
    let card = a.closest('div.border-b-xs') || a.closest('li');
    if (!card) { card = a; for (let i = 0; i < 2 && card.parentElement; i++) card = card.parentElement; }
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
  return JSON.stringify({ count: uniq.length, items: uniq });
})()
```

- `cardText`에는 결과 배지(파기환송/상고기각/원고일부승/청구기각 등)와 **인용 N**(이 판례를 인용한 판례 수)·**조회 N**이 포함된다. 인용 수가 큰 대법원 판례는 선례성이 높다는 신호이므로 triage에서 가중한다.
- **cardText 오염 자가진단**: 서로 다른 rank의 `cardText`(배지·인용수·조회수)가 전부 동일하면 컨테이너가 카드가 아니라 리스트 전체에 안착한 것이다 — `read_page`로 카드 단위 컨테이너 클래스를 재확인해 셀렉터를 조정한다.
- `docId` → `url`(`/case/{법원}/{사건번호}`)은 4단계 본문 navigate에 그대로 쓴다.
- 결과가 커서 truncated되면 `window.__lboxCards`를 `JSON.stringify(window.__lboxCards.slice(0,5))`처럼 인덱스로 분할해 받는다.

### 1-3. 페이지 순회

- 결과는 **10건/페이지**. 결과 리스트를 아래로 스크롤하면 하단에 **번호 페이지네이션(`1 2 3 4 5 … »`, 이전/다음 화살표)** 이 보인다.
- 다음 페이지: `computer` `scroll`(결과 리스트 위, 예 `[화면중앙x, 550]`, down)로 페이지네이션을 노출시킨 뒤 원하는 번호 버튼을 클릭한다. 클릭하면 같은 패널이 다음 10건(rank 11~20 …)으로 갱신되므로 1-2의 카드 추출 JS를 다시 실행한다.
- 페이지 간 누적은 각 페이지에서 받은 `items`를 대화 안에서 모아 수행한다(`window.__lboxCards`는 페이지 이동 시 갱신됨).

### 1-4. 필터 적용 (선택)

사건유형·법원·선고일 한정이 필요하면 결과 패널 상단의 필터 칩(**조건검색 · 사건유형 · 법원 · 주문유형 · 재판유형 · 선고일 · 키워드 알림**)을 클릭해 드롭다운에서 선택한다(예: 법원 → 대법원, 선고일 → 최근 N년). 필터는 `screenshot`으로 위치를 확인해 `computer`로 조작한다. 간단히는 검색어에 자연어로 녹여도 된다(예: "대법원 부당해고").

### 셀렉터가 안 맞을 때

`a[data-track-props]`가 0건이면 (ⓐ 패널이 닫힘 → "검색 결과" 카드 클릭, ⓑ 판례 탭이 비활성 → 판례 탭 클릭, ⓒ UI 변경 → `read_page`로 결과 영역 구조 확인 후 셀렉터 조정) 순으로 점검한다. 그래도 안 되면 사용자에게 알리고 중단한다.

## 2. 판례 본문 추출 (`/case/{법원}/{사건번호}`)

> 본문 canonical URL은 `/case/{법원}/{사건번호}`다. 실측(2026-07): 구 `/case/{법원}/{사건번호}` 경로도 `/case/…`로 자동 리다이렉트되어 여전히 작동하나, 신규 구성은 `/case/`를 쓴다.

> **개편 핵심**: 본문은 이제 일반 DOM으로 렌더된다. 탭이 `visibilityState=hidden`이어도 `document.body.innerText`가 정상(전원합의체 장문 판결 실측 약 60,000자, 단락 전수 적재)이다. **개편 전의 `<script>` 페이로드 한국어 복원 방식은 폐기** — 페이지에 섞인 "최근 본 자료/추천 판례" 텍스트까지 끌어와 다른 사건 내용이 노이즈로 섞인다.

> **⚠ 단락이 2벌로 렌더된다 (필수 dedup)**: 본문 페이지는 같은 단락 DOM을 **두 번** 그린다(각 `data-node-id`가 정확히 2개씩 존재, 같은 `viewer-main-container` 아래). 그래서 셀렉터로 그냥 모으면 "주문"이 두 번, 판시사항도 두 번 나온다. **반드시 `data-node-id` 값 기준으로 dedup**(텍스트 기준 dedup은 동일 문장이 실제로 반복될 때 잘못 합쳐질 수 있으니 id 기준으로)한다. 아래 JS의 `sorted()` 헬퍼가 id-dedup을 포함한다. (참고: 2벌 렌더 때문에 `document.body.innerText`도 약 2배가 되므로, 본문 추출은 innerText가 아니라 이 셀렉터로 한다.)

본문 단락은 `data-node-id="lbox-paragraph-{prefix}-{n}"` 구조다. prefix별 의미:

| prefix | 내용 |
|---|---|
| `topheader-1` / `topheader-2` | 법원(예: 대법원) / 판결 종류(판결·결정). **하급심은 `topheader-2`에 판결 종류 대신 재판부명(예: "제6민사부")이 올 수 있다** — 판결 종류 판정 근거로 쓰지 말 것 |
| `before-*` | 사건정보 표(사건번호·당사자·**원심판결** 등). `before-1`이 전체 블록 |
| `issue-*` | **판시사항** |
| `summary-*` | **판결요지** |
| `main-*` | **본문**(주문·이유·의견). 전원합의체는 다수/반대/별개/보충의견 포함 |
| `judges-*` | 재판부(대법관·판사) |

**(1) 머리부 + 구조 추출** — navigate 후 짧은 텀을 두고 실행:

```javascript
(() => {
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
  const fullMain = mains.join('\n');
  return JSON.stringify({
    title: document.title.replace(/\s*-\s*LBOX.*$/, ''),     // 법원·선고일·사건번호·[사건명]
    court: txt(document.querySelector('[data-node-id="lbox-paragraph-topheader-1"]')),
    type:  txt(document.querySelector('[data-node-id="lbox-paragraph-topheader-2"]')),
    caseInfo: txt(document.querySelector('[data-node-id="lbox-paragraph-before-1"]')).slice(0, 400), // 당사자·원심판결
    issue: join('issue'),       // 판시사항
    summary: join('summary'),   // 판결요지
    mainCount: mains.length,
    recoveredLen: fullMain.length,
    lastMain: (mains[mains.length - 1] || '').slice(-180),    // 결론부 확인용
    judges
  });
})()
```

**적재(완전성) 판정**: `recoveredLen > 0` 이고 (`lastMain`이 "…주문과 같이 판결한다"·"보충의견을 밝힌다" 등 결론부로 끝나거나 `judges.length > 0`)면 정상이다. `recoveredLen`이 거의 0이고 `caseInfo`도 비며 URL이 로그인 페이지로 바뀐 경우에만 재로그인을 의심한다(특정 한 건만 비면 건너뛰고 한 줄로 알림). **`innerText`가 0이라고 로그인 만료로 오판하지 말 것** — 다만 개편 후에는 보통 0이 아니다.

**(2) 본문 전문 발췌** — `main`이 길어(장문 판결 수만 자) 한 번에 반환하면 truncated되므로, `window.__lboxMain`을 인덱스/키워드로 분할해 받는다.

```javascript
// (2-a) 질의 키워드 인근 ±2500자 발췌. anchors = 질의 특화 키워드(1~3개)를 매 검색마다 갱신.
(() => {
  const full = (window.__lboxMain || []).join('\n');
  const anchors = [/* 질의 특화: 예) '통상임금','고정성' */ '주문', '이유', '판단'];
  let i = -1; for (const k of anchors) { i = full.indexOf(k); if (i >= 0) break; }
  return JSON.stringify({ len: full.length, excerpt: i >= 0 ? full.slice(Math.max(0, i - 200), i + 2500) : full.slice(0, 3000) });
})()
// (2-b) 더 필요하면 범위를 옮겨가며: window.__lboxMain.join('\n').slice(START, END)
```

- **질의 특화 키워드 갱신 필수**: `anchors`의 앞쪽 키워드는 매 검색마다 사용자 질의에서 뽑아 교체한다(예: 채권양도 질의 → `'채권양도'`,`'통지'`,`'대항요건'`). 공통어(`주문/이유/판단`)만 박아두지 말 것.
- 짧은 판례는 (1)의 결과만으로 충분할 수 있다. 길면 (2)로 쟁점 인근만 받는다.

## 3. 관련 판례 추적 (본문 페이지 우측 사이드바, href 아님 → data-track-props)

판례 본문 페이지의 우측 사이드바에 관련 자료가 구조화돼 있다. 링크는 앵커 `href`가 아니라 `data-track-click`/`data-track-props`를 쓰는 요소다.

**(3-a) 상·하위 판결(소송 진행 체인)** — 1심→2심→상고심. 관계 라벨이 텍스트에 포함된다.

```javascript
(() => {
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
  return JSON.stringify({ upperLower: items });
})()
```

- `label`에 관계(원고승/원고패/파기환송/상고기각/원고일부승/확정 등) + 법원 + 사건번호가 들어 있다. **하급심 본문을 visit했다면 여기 나온 상급심 `url`을 반드시 navigate**해 결론을 확인한다(SKILL 4.5단계 (가)).
- **사이드바는 본문보다 늦게 렌더될 수 있다**(실측: 본문 적재 완료 시점에 0건이었다가 직후 채워짐). (3-a) 결과가 0건이면 짧은 텀을 두고 **1회 재실행**한 뒤에야 "상·하위 판결 없음"으로 판정한다.

**(3-b) 인용된 판례 / 따름 판례 / 인용된 조문** — 사이드바의 접힘 섹션("인용된 판례 N", "따름 판례 N", "인용된 조문 N"). 펼쳐야 항목이 DOM에 들어온다.

1. `computer` `screenshot`으로 사이드바에서 해당 섹션 헤더(예 "인용된 판례 25")를 찾아 클릭해 펼친다.
2. 펼친 뒤 그 섹션 내부의 항목을 `data-track-props`(documentType `precedent`)로 추출한다 — 셀렉터·docId 분리 규칙은 (3-a)와 동일(`a[data-track-props]` 또는 `[data-track-click]` 항목). 핵심 대법원 선례만 골라 `url`로 본문을 확인하거나 "참조 대법원 선례 정리" 표에 기록한다.

**원심판결 직접 구성**: 사이드바 링크가 안 보여도, 본문 (1)의 `caseInfo`(`before-1`)에 "원심판결 ○○법원 …선고 ○○○○ 판결" 텍스트가 있으니 거기서 법원·사건번호를 읽어 `/case/{법원}/{사건번호}`로 직접 구성해 navigate할 수 있다.
