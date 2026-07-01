---
name: lbox-case-progress
description: lbox.kr 에 등록된 사용자의 사건들의 진행 경과(기일·송달·결과·다음기일 등)를 Claude in Chrome 으로 회수해 Dropbox 동기화된 단일 markdown 보고서(`$LBOX_DIR/lbox-case-progress.md`, 기본 `~/Library/CloudStorage/Dropbox/AI/lbox-case-progress/lbox-case-progress.md`)로 갱신·관리한다. 사용자가 "사건 경과 업데이트", "사건 진행상황 갱신", "lbox 사건 업데이트", "내 사건들 어떻게 됐어", "오늘 새 기일 있어?", "lbox 동기화", "사건 보고서 만들어줘" 등 자신이 등록한 lbox 사건의 최신 진행상황 조회·동기화·보고서 작성을 요청하거나, 스케줄러에 의해 본 스킬이 자동 호출될 때 반드시 이 스킬을 사용한다. Chrome+lbox 로그인이 가능한 PC 에서는 fetch 모드로, 그렇지 않은 PC 에서는 클라우드 동기화된 데이터만 읽는 read-only 모드로 동작한다. 절대 기억에 의존해 사건 정보를 생성하지 말고, 실측 데이터(API 응답 또는 클라우드 동기화된 JSON)만 사용한다.
---

# lbox 사건 경과 자동 보고서

lbox.kr 의 "사건일정관리" 에 사용자가 등록한 모든 사건의 최신 진행 경과를, **여러 PC 사이에 클라우드 동기화되는 단일 markdown 보고서**로 정리·갱신한다.

본 스킬은 lbox 의 **내부 API 를 직접 호출**한다. DOM 스크래핑보다 결정론적이며, 페이지네이션 처리도 필요 없다.

> **2026.6 사이트 개편 반영**: 검색 기능은 크게 바뀌었지만 본 스킬의 핵심인 사건 목록 API(`/api/caseManage/caseEvents`)는 그대로 동작한다. 개편으로 바뀐 부분만 반영했다 — ① 사건일정관리 페이지가 `/v2/case-events/list`(폐지·404) → `/project?tab=case-schedule` 로 이동, ② 로그인 판정을 페이지가 아니라 API 응답(`ok:false`)으로, ③ `events` 오류 플래그 필드명이 `isCaseNotExist`/`isErrorOccurred` 로 변경(구 이름도 수용), ④ 개별 사건 딥링크 폐지로 `detailPageUrl` 을 사건일정 목록 페이지로 통일.

---

## 기본 가정 — 클라우드 동기화 설정

본 스킬은 사용자가 **사건 데이터를 보관할 단일 폴더**(이하 `$LBOX_DIR`) 를 **여러 PC 가 공유하는 클라우드 동기화 폴더**로 미리 설정해 두었다고 가정한다.

**기본값**: `$LBOX_DIR=~/Library/CloudStorage/Dropbox/AI/lbox-case-progress` (macOS Dropbox 의 표준 동기화 위치). 다른 PC 에서 경로가 다르면 환경변수 `LBOX_DIR` 로 오버라이드한다.

다른 클라우드 서비스로 교체할 때는 `$LBOX_DIR` 만 그 서비스의 동기화 폴더로 바꾸면 된다(iCloud Drive 의 `~/Library/Mobile Documents/com~apple~CloudDocs/...`, Google Drive 의 `~/Library/CloudStorage/GoogleDrive-.../...`, OneDrive 의 `~/Library/CloudStorage/OneDrive-.../...` 등).

이 설계 덕분에 한 PC(예: Chrome + lbox 로그인이 있는 사무실 PC)에서 데이터를 회수하면 자동으로 다른 PC(예: 집 PC, Cowork 세션)에서도 같은 보고서를 볼 수 있다.

---

## 두 가지 동작 모드

| 모드 | 조건 | 동작 |
|---|---|---|
| **Fetch 모드** | Chrome MCP 연결 가능 + lbox 로그인 세션 유효 | lbox API 호출 → 다운로드 → 보고서 갱신 |
| **Read-only 모드** | Chrome 미연결/미로그인, 또는 Claude in Chrome 이 원격 PC 에서 실행 중이라 다운로드 파일이 이 PC 에 존재하지 않음. 그러나 `$LBOX_DIR/lbox_cases.json` 이 클라우드 sync 로 도착해 있음 | API 호출을 건너뛰고 동기화된 JSON 으로 보고서만 재생성 |

Read-only 모드에서는 보고서 상단에 "이 PC 가 lbox 에서 직접 회수한 것이 아니라 동기화된 데이터" 임이 명시된다.

### 중요 — Claude in Chrome 이 원격 PC 에서 실행되는 경우

`Claude in Chrome` MCP 는 Chrome 확장 인스턴스에 연결되며, **그 Chrome 이 반드시 이 PC 에서 실행 중일 필요는 없다**. 예를 들어 사무실 PC 의 Chrome 에 확장을 설치하고 그것을 이 PC 의 Claude 가 원격으로 조작하는 구성이 가능하다. 이 경우:

- `fetch_cases.js` 의 다운로드는 **그 원격 PC 의 다운로드 폴더로** 떨어진다. 이 PC 의 `~/Downloads` 에는 아무 것도 생기지 않는다.
- 클라우드 sync 가 원격 PC 의 다운로드 폴더(또는 그 위치로 설정된 폴더) 와 이 PC 의 `$LBOX_DIR` 사이를 연결해 주어야 한다.
- 이 PC 에서 보았을 때는 **항상 Read-only 모드처럼 동작**하지만, 데이터 자체는 같은 사용자의 동일한 lbox 사건이므로 보고서 내용은 정상이다.

이 구성은 본 스킬의 1차 권장 운용 형태이며, "fetch 담당 PC 1대 + 나머지 PC 들은 read-only" 구도를 자연스럽게 실현한다.

---

## 핵심 데이터 소스

| 용도 | URL/경로 | 비고 |
|---|---|---|
| 사건 목록 API | `https://lbox.kr/api/caseManage/caseEvents` | GET, 같은 출처 쿠키. 모든 사건 + progress_list 일괄 반환 |
| 사용자가 보는 페이지 | `https://lbox.kr/project?tab=case-schedule` | (구 `/v2/case-events/list` 는 폐지·404) API 호출 컨텍스트 확보용. same-origin 이라 다른 lbox.kr 페이지여도 무방 |
| 회수된 JSON (primary) | `$LBOX_DIR/lbox_cases.json` | 클라우드 동기화 대상 — 진실의 원천 |
| 회수된 JSON (fallback) | `~/Downloads/lbox_cases.json` | Chrome 의 다운로드 폴더(설정에 따라 비어 있을 수 있음) |
| 통합 보고서 | `$LBOX_DIR/lbox-case-progress.md` | 매 실행 시 덮어쓰기 |
| 스냅샷 | `$LBOX_DIR/snapshots/YYYYMMDD_HHMMSS.json` | 변경 감지 기준 |

자세한 API 응답 스키마는 `references/api.md`, 출력 양식은 `references/output-template.md` 를 참고.

---

## 작업 흐름

### 0) 환경 변수 결정

```
LBOX_DIR = ${LBOX_DIR:-~/Library/CloudStorage/Dropbox/AI/lbox-case-progress}
```

이하 모든 경로는 이 변수를 기준으로 한다.

### 1) 사전 점검

- `mcp__Claude_in_Chrome__list_connected_browsers` 호출.
  - 결과가 **있으면** → Fetch 모드 후보. 단계 2 로.
  - 결과가 **없으면** → Read-only 모드로 분기. 단계 5 로 점프.
- `$LBOX_DIR` 와 `$LBOX_DIR/snapshots` 디렉터리가 없으면 생성:
  ```bash
  mkdir -p "$LBOX_DIR/snapshots"
  ```
- **원격 Chrome 여부 점검(선택)**: `list_connected_browsers` 의 결과 항목에 `isLocal` 이 false 면 그 Chrome 은 원격 PC 에서 실행 중이다. 이 경우 fetch_cases.js 의 다운로드가 이 PC 의 `~/Downloads` 가 아니라 **원격 PC 의 다운로드 폴더** 로 떨어진다는 점을 유의한다(클라우드 sync 가 `$LBOX_DIR` 로 그 파일을 가져와야 한다). 동작 자체는 변경되지 않는다 — `pick_source.py` 가 자동으로 fallback 부재를 흡수한다.

### 2) lbox 탭 확보 (Fetch 모드)

- `mcp__Claude_in_Chrome__tabs_context_mcp` 로 현재 탭 그룹 확인.
- 기존 탭이 lbox.kr 도메인이 아니라면 `mcp__Claude_in_Chrome__navigate` 로 `https://lbox.kr/project?tab=case-schedule` (사건일정관리) 로 이동. 이미 lbox.kr 페이지면 그대로 둬도 된다 — API 는 same-origin 이라 어느 lbox.kr 페이지에서나 호출된다.
- **로그인 판정은 페이지 렌더가 아니라 3단계 API 응답으로 한다.** 개편으로 구 경로 `/v2/case-events/list` 는 404 가 되어, "사용자명이 안 보이면 강등" 식의 페이지 기반 판정은 신뢰할 수 없다(로그인이 멀쩡해도 오판 가능). 곧바로 단계 3 으로 진행하고, `fetch_cases.js` 가 `ok:false`(상태 401/302 또는 비 JSON)를 반환하면 그때 **Read-only 모드로 강등**한다(기존 보고서를 덮어쓰지 않음). 사용자에게 한 줄로 안내한다(예: "lbox 로그인이 끊겨 동기화된 데이터로 보고서만 갱신합니다").

### 3) baseline 기록 + API 호출 + 다운로드 트리거

다운로드 트리거 **직전에** 현재 USER_DIR 파일의 baseline(mtime, fetchedAt) 을 한 줄로 기록해 둔다. sync 도착 판정의 기준이 된다.

```bash
BASELINE_MTIME=$(stat -f %m "$LBOX_DIR/lbox_cases.json" 2>/dev/null || stat -c %Y "$LBOX_DIR/lbox_cases.json" 2>/dev/null || echo "")
BASELINE_FETCHED_AT=$(python3 -c "import json,sys; print(json.load(open('$LBOX_DIR/lbox_cases.json'))['fetchedAt'])" 2>/dev/null || echo "")
```

그 다음 `scripts/fetch_cases.js` 를 `mcp__Claude_in_Chrome__javascript_tool` 로 주입해 실행한다. 스크립트는:

- `/api/caseManage/caseEvents` 호출 → 정규화 JSON 구성
- Blob + `<a download>` 트리거로 사용자 컴퓨터에 `lbox_cases.json` 다운로드 (Chrome 의 다운로드 폴더 설정을 따른다)
- 짧은 요약만 반환:
  ```json
  { "ok": true, "fetchedAt": "...", "totalCount": 29,
    "doneCount": 2, "withNextCount": 11, "downloadFilename": "lbox_cases.json",
    "downloadTriggered": true }
  ```

스크립트가 `ok: false` 를 반환하면 **Read-only 모드로 강등**한다(로그인 만료·세션 끊김의 권위 있는 신호).

### 4) 클라우드 동기화 대기 (`isLocal === false` 인 경우)

`list_connected_browsers` 결과의 `isLocal` 가 **true** 이면 다운로드가 이 PC 에 직접 떨어졌으므로 대기 불필요. **false** 이면 원격 PC 가 다운로드를 받았고, Dropbox/iCloud/Google Drive 등 sync 클라이언트가 `$LBOX_DIR` 로 전달할 때까지 대기한다.

```bash
python3 scripts/wait_for_sync.py \
  --target "$LBOX_DIR/lbox_cases.json" \
  ${BASELINE_MTIME:+--baseline-mtime "$BASELINE_MTIME"} \
  ${BASELINE_FETCHED_AT:+--baseline-fetched-at "$BASELINE_FETCHED_AT"} \
  --interval "${LBOX_SYNC_WAIT_INTERVAL:-2}" \
  --timeout "${LBOX_SYNC_WAIT_TIMEOUT:-60}" \
  --progress
```

폴링 로직:
- 매 `--interval` 초마다 `$LBOX_DIR/lbox_cases.json` 의 mtime 검사
- mtime 이 baseline 보다 새로움 → JSON 파싱 시도 → `fetchedAt` 이 baseline 보다 새로움 → **완료**
- 파싱 실패(partial write 중) 또는 fetchedAt 미변경 → 다음 폴 라운드
- `--timeout` 초 안에 변화 없으면 → `synced:false` 와 함께 종료(스킬은 그 상태로 read-only 진행)

환경변수 튜닝:
- `LBOX_SYNC_WAIT_INTERVAL` (기본 2초) — Dropbox 환경에서는 1~3초가 적당
- `LBOX_SYNC_WAIT_TIMEOUT` (기본 60초) — LAN sync 활성 환경은 30초로, 야간 wake-up 환경은 120초로 조정

표준 출력 결과를 파싱하여 `synced` 가 true 이면 단계 5 로, false 이면 사용자에게 안내 후 마찬가지로 단계 5 로 (가장 최신 파일이 어떤 것이든 사용).

### 5) 데이터 회수 (Fetch/Read-only 공통)

`scripts/pick_source.py` 로 어느 파일을 사용할지 결정한다:

```bash
python3 scripts/pick_source.py \
  --primary "$LBOX_DIR/lbox_cases.json" \
  --fallback ~/Downloads/lbox_cases.json \
  --verbose
```

선택 규칙:
- 둘 다 존재하면 mtime 이 더 큰 쪽
- 동률이면 `$LBOX_DIR`(primary) 우선 — 클라우드가 진실의 원천이라는 원칙 반영
- 파일명이 정확히 `lbox_cases.json` 이 아닌 동기화 충돌 파일(`(1)`, `conflict`, `충돌된 사본` 등)은 자동 무시
- 양쪽 모두 없으면 종료 코드 2 → 사용자에게 "회수된 데이터가 없습니다. Chrome+lbox 로그인 후 다시 시도하세요" 안내

선택 결과 JSON 에서 `chosen` 경로를 다음 단계의 입력으로 사용한다.

### 6) 변경 감지 (diff)

가장 최근 스냅샷을 찾아 비교:

```bash
LATEST_SNAP=$(ls -t "$LBOX_DIR/snapshots/"*.json 2>/dev/null | head -1)

python3 scripts/diff_report.py \
  --current "$CHOSEN" \
  ${LATEST_SNAP:+--previous "$LATEST_SNAP"} \
  --output /tmp/lbox_diff.json
```

스냅샷이 없으면 `--previous` 인자를 생략한다 → "최초 실행" 으로 처리.

### 7) markdown 보고서 생성 + 스냅샷 저장

```bash
python3 scripts/build_report.py \
  --cases "$CHOSEN" \
  --diff /tmp/lbox_diff.json \
  --output "$LBOX_DIR/lbox-case-progress.md" \
  --snapshot-dir "$LBOX_DIR/snapshots"
```

스크립트는 (a) 메인 보고서를 갱신하고, (b) `$CHOSEN` 의 내용을 `$LBOX_DIR/snapshots/YYYYMMDD_HHMMSS.json` 으로 보관한다. 보고서 상단에는:
- **데이터 회수 시각** (lbox API 호출 시각, payload 의 `fetchedAt`)
- **보고서 작성 시각** (지금 이 PC 의 시각)

두 시각의 차이가 30분 이상이면 "동기화된 다른 출처에서 읽었습니다" 표시가 자동 추가된다.

### 8) 사용자 안내

마지막에 결과 경로를 링크로 제공한다:

```
[보고서 보기](computer:///Users/wkjin/Library/CloudStorage/Dropbox/AI/lbox-case-progress/lbox-case-progress.md)
```

요약 한두 문장:
- 총 사건 수 (진행/종결 구분)
- 변경 요약 (신규 N, 종결 N, 신규 이벤트 N, 기일변경 N)
- 가장 임박한 다음 기일 (있으면)

---

## 환경별 동작 차이 안내

| 항목 | Claude CLI | Cowork |
|---|---|---|
| `~/Downloads` 접근 | bash 가 사용자 셸 직접 실행 — 직접 접근 | bash 샌드박스에 마운트되지 않음 — `pick_source.py` 가 자동으로 fallback=None 처리 |
| `$LBOX_DIR` 접근 | 직접 접근 | `~/claude/...` 아래라면 마운트 통해 접근. 다른 경로면 사용자가 mount 또는 심볼릭 링크 설정 필요 |
| `mcp__Claude_in_Chrome__*` | 사용자가 Chrome 확장 설치·MCP 설정 시 가능 | 기본 제공 |
| `computer-use` MCP | 별도 설치 필요 (보통 없음) | 기본 제공 |

**핵심**: `pick_source.py` 가 fallback 경로의 접근 실패를 OSError 로 흡수해 None 으로 처리하므로, Cowork 에서 `~/Downloads` 가 안 보여도 워크플로우는 그대로 동작한다(`$LBOX_DIR` 의 동기화된 파일이 사용됨).

---

## 멀티 PC 운용 가이드

세 가지 대표 구성이 있다. 어느 것이든 본 스킬은 동일하게 동작한다.

**구성 A — 단일 PC**: 한 PC 에서 Chrome 도 켜고 보고서도 본다. Fetch 모드만 사용.

**구성 B — fetch 1대 + read-only 여러 대 (각 PC 에 Chrome MCP 별도)**: 사무실 PC 가 fetch 담당, 노트북·집 PC 는 read-only. 각 PC 가 자기 Chrome 을 가짐.

**구성 C (권장) — Claude in Chrome MCP 가 원격 PC 에서 실행**: 사무실 PC 에 Chrome 확장 설치, 노트북에서 그 Chrome 을 원격 조작 + 보고서 보기. 노트북에서 fetch 를 트리거하면 다운로드 파일은 원격 사무실 PC 에 떨어지고, 클라우드 sync 가 그것을 노트북의 `$LBOX_DIR` 로 전달.

운용 규칙:

- **fetch 담당 PC 1대**(Chrome + lbox 로그인이 안정적인 PC)에만 스케줄러 등록을 권장한다. 다중 PC 동시 fetch 는 클라우드 sync 충돌을 유발할 수 있다.
- 나머지 PC(노트북·집 PC·Cowork 세션 등)는 Chrome 없이도 Read-only 모드로 동일 보고서를 본다.
- 사용자가 어떤 PC 에서 수동으로 "lbox 사건 경과 업데이트" 라고 말하면, 그 PC 가 fetch 가능하면 fetch 모드, 아니면 read-only 모드로 자동 분기된다.
- 구성 C 에서는 fetch 트리거 직후 클라우드 sync 가 따라잡을 시간(보통 5~30초)을 두면 `pick_source.py` 가 자연스럽게 가장 최신 파일을 선택한다.

---

## 출력 markdown 구조

표준 양식은 `references/output-template.md`. 핵심은:

1. 메타 상단: 데이터 회수 시각 / 보고서 작성 시각 / 사건 수 / 변경 요약 한 줄
2. 변경 요약 표: 직전 스냅샷 대비 신규·종결·신규이벤트·기일변경
3. 진행 중 사건 표 + 종결 사건 표 (있을 때만)
4. 사건별 상세: 사건 메타 + 이벤트 표(최신 → 과거)

---

## 엣지 케이스

- **로그인 미수행**: API 가 401/302 를 반환 → fetch_cases.js 가 `ok:false` 반환 → Read-only 모드 강등. (페이지가 404 여도 이 API 기반 판정이 권위 있는 신호다.)
- **사용자 사건 0건**: 빈 배열을 받으면 "현재 등록된 사건이 없습니다." 한 단락만 두는 짧은 보고서.
- **개별 사건의 `events` 가 비어 있음(`isCaseNotExist`, `isErrorOccurred`)**: 해당 사건은 기본 정보만 기록하고 헤더에 `⚠️ lbox 조회 실패` 플래그.
- **직전 스냅샷 부재**: diff 를 건너뛰고 "최초 실행입니다. 변경 감지를 생략합니다." 표기.
- **Chrome 확장 미연결**: Read-only 모드로 자동 전환. `$LBOX_DIR/lbox_cases.json` 이 있으면 그것으로 보고서 갱신.
- **양쪽 후보 모두 부재**: 사용자에게 "회수된 데이터가 없습니다. Chrome 에서 lbox 에 로그인한 PC 에서 한 번 실행해 주세요" 안내.
- **클라우드 동기화 충돌 파일**: `pick_source.py` 가 정확히 `lbox_cases.json` 인 파일만 인정 → 충돌 파일 자동 skip.

---

## 절대 하지 말 것

- 기억이나 추론으로 사건 정보·기일을 만들어내지 말 것. 항상 API 응답 또는 동기화된 JSON 만 사용한다.
- 사건의 개인정보(원고/피고 이름)는 보고서에 그대로 기록되어도 무방하지만, 보고서 외부(다른 채널·요약 등)로 임의 전달하지 말 것.
- 로그인이 안 된 상태에서 기존 보고서를 빈 데이터로 덮어쓰지 말 것 — 데이터 손실 위험.
- 멀티 PC 환경에서 두 PC 가 동시에 fetch 하지 말 것 — 클라우드 sync 충돌. 스케줄러는 1대에만.

---

## 보조 자료

- `references/api.md` — lbox 내부 API 엔드포인트와 응답 필드 상세
- `references/output-template.md` — markdown 산출물 표준 양식
- `scripts/fetch_cases.js` — Chrome 에 주입해 API 호출 + 다운로드 트리거 (요약만 반환)
- `scripts/pick_source.py` — `$LBOX_DIR` 와 `~/Downloads` 사이에서 더 최근 파일 선택 (동률 시 USER_DIR 우선, 충돌 파일 무시)
- `scripts/wait_for_sync.py` — 원격 Chrome 시나리오에서 Dropbox 등 클라우드 sync 가 따라잡을 때까지 mtime + fetchedAt 하이브리드 폴링
- `scripts/diff_report.py` — 직전 스냅샷과 비교해 변경 추출
- `scripts/build_report.py` — JSON → markdown 변환, 스냅샷 저장, 데이터 회수 시각 명시
