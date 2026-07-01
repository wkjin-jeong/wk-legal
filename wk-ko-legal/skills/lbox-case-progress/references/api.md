# lbox 내부 API 참고 문서

본 스킬이 사용하는 lbox.kr 내부 API 의 엔드포인트와 응답 스키마를 정리한다. 모든 호출은 `credentials: 'include'` 로 같은 출처의 로그인 세션을 사용한다.

> **2026.6 개편 메모**: 목록 API(`/api/caseManage/caseEvents`) 자체와 대부분의 필드는 그대로다. 바뀐 것은 `events` 의 오류 플래그 필드명(`isCaseNotExist`/`isErrorOccurred`)과, 더 이상 개별 사건 딥링크가 제공되지 않는다는 점이다.

---

## 1. 목록 API (필수)

### `GET /api/caseManage/caseEvents`

사용자가 등록한 모든 사건을 한 번에 반환한다(페이지네이션 없음).

**응답: 배열**. 각 사건 객체의 주요 필드:

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | number | 사건 고유 ID — 상세 URL/API 의 `{id}` 에 해당 |
| `parent` | number? | 병합/분리된 모(母)사건 ID. 없으면 null |
| `hasMemo` | bool | 사용자가 작성한 메모 존재 여부 |
| `lastAlarmDate` | number? | 마지막 알람 일시(unix ms) |
| `userId` | number | 등록 사용자 ID |
| `court` | string | 법원명 (예: "서울중앙지방법원") |
| `caseNo` | string | 사건번호 (예: "2026가합12345") |
| `casename` | string | 사건명 (예: "대여금") |
| `requestName` | string | 등록 당시 입력한 의뢰인/당사자명 |
| `name` | string | 표시용 당사자명(보통 `requestName` 과 동일) |
| `done` | bool | 사건 종결 여부 |
| `nextEvent` | object? | 다음 기일 정보. 아래 [nextEvent 스키마](#nextevent-스키마) 참고 |
| `events` | object | 사건 전체 진행 정보. 아래 [events 스키마](#events-스키마) 참고 |
| `progressList` | array | (목록 API 한정) 간략 진행 목록 |
| `sentEvents` | array | 사용자에게 알람으로 발송된 이벤트 |
| `calendarMemoInfos` | array | 캘린더 메모 |
| `auction` | bool | 경매 사건 여부 |
| `auctionCase` | object? | 경매 사건일 때만 채워짐 |

### `events` 스키마

`events` 객체는 lbox 가 법원 시스템에서 크롤한 원본 데이터다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `court`, `caseno`, `name`, `casename` | string | 사건 기본 정보(중복) |
| `errmsg` | string? | 크롤 실패 시 메시지 |
| `isCaseNotExist` | bool | 법원 시스템에서 사건이 존재하지 않을 때 true. (2026.6 개편 전 이름: `caseNotExist`) |
| `isErrorOccurred` | bool | 크롤 중 오류 발생 여부. (2026.6 개편 전 이름: `errorOccurred`) |
| `reception_date` | number | 접수일 (unix ms) |
| `updated_date` | number | 최종 갱신 일시 (unix ms) |
| `confirmation_date` | string | 확정일 |
| `case_result` | string | 사건 결과 (선고·확정 시) |
| `is_criminal` | bool | 형사사건 여부 |
| `progress_list` | array | **진행이력 핵심 배열**. [progress 항목 스키마](#progress-항목-스키마) 참고 |
| `info_list` | array | `{title, text}` 쌍의 일반 정보 |
| `etc_info_tables` | array | `{title, columns, rows}` 형태의 부가 표 |
| `balance_tables` | array | 잔액·금액 관련 표 |
| `lawyer_list` | array | 변호사/대리인 정보 |
| `merged_or_splited_case_list` | array | 병합/분리된 사건 |
| `uplo_case_list` / `related_case_list` | array | 상소·관련 사건 |
| `nextEvent` | object? | 다음 기일 |
| `comingDate` | number? | 다음 기일 일자(unix ms) |
| `docId` | string | 법원 문서 ID |

> `fetch_cases.js` 의 `errorFlag` 는 신·구 필드명을 모두 수용한다: `!!(ev.isCaseNotExist || ev.isErrorOccurred || ev.caseNotExist || ev.errorOccurred)`.

### `progress` 항목 스키마

`events.progress_list[i]` 의 각 항목:

| 필드 | 타입 | 설명 |
|---|---|---|
| `type` | string | 진행 종류. 실측치: `"date"`(기일), `"send"`(송달), `"order"`(명령), `"submit"`(제출서류), `"doc"`(문서), `"etc"`(기타). 일부 응답에서 `"hearing"` 변형도 관찰됨 |
| `date` | string | unix ms (문자열로 올 수도 있음 — 변환 시 주의) |
| `result` | string | 결과 (예: "속행", "선고") |
| `content` | string | 본문 (예: "변론기일", "답변서 송달") |
| `location` | string | 법정/조정실 위치 |
| `forCalendar` | bool | 캘린더 표시 대상 여부 |
| `scheduleModified` | bool | 기일 변경 이력 여부 |
| `discovered_at` | number? | 크롤 발견 시각 |

### `nextEvent` 스키마

`progress` 항목과 동일한 구조. 가장 가까운 미래 기일을 단일 객체로 미리 뽑아 둔 것.
단, 정규화 산출물(fetch_cases.js)의 `nextEvent`에는 `scheduleModified`(bool) 필드가 추가되어 있다.

---

## 2. 상세 API (선택)

### `GET /api/v2/case-manages/{id}`

특정 사건의 정제된 상세를 반환. **기본 보고서 작성에는 불필요**하지만, "상세까지" 같은 요청이 있을 때 보강용으로 호출. (2026.6 개편 이후 미검증 — 사용 전 응답 확인 권장.)

**응답:**

```jsonc
{
  "status": 200,
  "code": "SUCCESS",
  "data": {
    "id": 364626,
    "userId": 12672,
    "court": "...", "caseNo": "...", "caseName": "...", "requestName": "...",
    "alias": null,
    "done": false,
    "hasCaseResult": false,
    "crawledAt": "...", "upcomingAt": "...",
    "createdAt": "...", "updatedAt": "...", "contentChangedAt": "...",
    "displayName": "...",          // UI 표시용 이름
    "badgeType": "...",            // 진행/종결 등 배지 종류
    "upcomingContent": "...",      // 임박 기일 요약
    "caseResultDate": null,
    "caseResultContent": null,
    "processedInfoList": [...],    // 정제된 info_list
    "processedEtcInfoTables": [...], // 정제된 etc_info_tables
    "processedProgresses": [...],  // 정제된 progress_list — 보고서에 그대로 쓰기 좋음
    "myCaseDetailInfo": { /* events 와 동일 구조 */ },
    "auctionCaseDetailInfo": {...},
    "calendarMemoInfos": [...]
  }
}
```

상세 API 의 `processedProgresses` 는 목록 API 의 `events.progress_list` 와 동일한 데이터를 더 일관된 형식으로 가공해 둔 것이다. 두 곳 어디서 가져와도 무방하다.

---

## 3. 인증·세션

- 모든 API 는 같은 출처(same-origin) 의 로그인 쿠키를 사용한다.
- Claude in Chrome 의 `javascript_tool` 로 `fetch(path, {credentials: 'include'})` 호출 시 자동으로 쿠키가 전송된다.
- 로그인이 끊긴 경우 `/api/...` 호출이 302 또는 401 을 반환하거나 HTML 로그인 페이지를 응답한다 — `r.ok` 와 `Content-Type` 으로 감지 가능. **이 API 기반 판정이 로그인 여부의 권위 있는 신호**이며, 페이지 렌더(404 등)에 의존하지 않는다.

---

## 4. 날짜 처리

- 모든 시각 필드는 **unix milliseconds**. Python 변환:
  ```python
  from datetime import datetime, timezone, timedelta
  KST = timezone(timedelta(hours=9))
  dt = datetime.fromtimestamp(int(ms) / 1000, tz=KST)
  ```
- 일부 필드(`progress.date` 등)는 문자열로 올 수 있으니 `int(str_or_num)` 로 안전 변환.
- 빈 문자열·`null` 처리: `confirmation_date` 가 `""` 이거나 `lastAlarmDate` 가 `null` 인 경우가 흔하다.

---

## 5. 정규화 규칙

스킬이 산출하는 정규화 JSON 의 사건 한 건은 아래 형태를 따른다.

```jsonc
{
  "id": 356877,                    // number
  "court": "광주지방법원",
  "caseNo": "2026라5207",
  "caseName": "채권압류 및 추심명령",
  "party": "김영보",                 // requestName 우선, 없으면 name
  "done": false,
  "detailPageUrl": "https://lbox.kr/project?tab=case-schedule",  // 개편 후 개별 사건 딥링크 미노출 → 사건일정 목록 페이지로 통일
  "lastUpdatedMs": 1778739679412,
  "receptionMs": 1775174400000,
  "errorFlag": false,               // events.isCaseNotExist || events.isErrorOccurred (구 이름 caseNotExist/errorOccurred 도 수용)
  "nextEvent": {
    "type": "hearing",
    "dateMs": 1779238800000,
    "content": "변론기일",
    "result": "",
    "location": "..."
  } | null,
  "events": [                       // events.progress_list 정렬·정규화
    {
      "type": "etc",
      "dateMs": 1775174400000,
      "content": "사건접수",
      "result": "",
      "location": ""
    },
    ...
  ]
}
```
