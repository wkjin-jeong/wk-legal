// fetch_cases.js
//
// lbox.kr 의 내부 API(`/api/caseManage/caseEvents`) 를 호출해 사용자가
// 등록한 모든 사건의 진행 정보를 가져온 뒤,
//   (a) 결과 JSON 을 사용자 컴퓨터로 다운로드(Blob + a[download]) 한다
//   (b) Chrome 의 javascript_tool 에는 짧은 요약만 반환한다
//
// 270KB 가량의 JSON 을 그대로 반환하면 도구 응답이 절단되기 때문에,
// 본 스크립트는 항상 다운로드를 통해 파일로 저장하고,
// 외부 워크플로우(Python 스크립트들)가 그 파일을 읽도록 한다.
//
// 다운로드 경로:
//   Chrome 의 기본 다운로드 폴더(보통 ~/Downloads) 로 저장된다.
//   사용자가 Chrome 의 다운로드 폴더 설정을 클라우드 동기화 폴더로
//   지정해 두었다면 그 폴더로 직접 저장된다.
//
// 사용 방법:
//   mcp__Claude_in_Chrome__javascript_tool 로 본 파일 내용을 그대로 주입한다.
//   탭은 lbox.kr 도메인이고 로그인된 상태여야 한다.
//   (API 는 same-origin 호출이라 lbox.kr 의 아무 페이지에서나 동작한다.)
//
// 반환 (JSON 문자열):
//   성공: { ok: true, fetchedAt, totalCount, doneCount, withNextCount, downloadFilename }
//   실패: { ok: false, error: { kind, status?, contentType?, message? } }
//   ※ 로그인 만료/세션 끊김은 여기서 ok:false(상태 401/302 또는 비 JSON)로 나타난다.
//      스킬은 페이지 렌더가 아니라 이 반환값을 로그인 판정의 근거로 삼는다.

(async () => {
  const FILENAME = 'lbox_cases.json';

  const result = {
    ok: false,
    fetchedAt: new Date().toISOString(),
    totalCount: 0,
    cases: []
  };

  let listJson;
  try {
    const r = await fetch('/api/caseManage/caseEvents', { credentials: 'include' });
    const ct = r.headers.get('content-type') || '';
    if (!r.ok || !ct.includes('json')) {
      return JSON.stringify({
        ok: false,
        error: { kind: 'auth_or_endpoint', status: r.status, contentType: ct }
      });
    }
    listJson = await r.json();
  } catch (e) {
    return JSON.stringify({
      ok: false,
      error: { kind: 'fetch_failed', message: String(e && e.message) }
    });
  }

  if (!Array.isArray(listJson)) {
    return JSON.stringify({
      ok: false,
      error: { kind: 'unexpected_shape', shape: typeof listJson }
    });
  }

  // ── 정규화 ────────────────────────────────────────────────
  const toMsOrNull = (v) => {
    if (v == null || v === '') return null;
    const n = typeof v === 'number' ? v : Number(v);
    return Number.isFinite(n) ? n : null;
  };

  const normalizeProgress = (p) => {
    if (!p || typeof p !== 'object') return null;
    return {
      type: p.type ?? '',
      dateMs: toMsOrNull(p.date),
      content: p.content ?? '',
      result: p.result ?? '',
      location: p.location ?? '',
      forCalendar: !!p.forCalendar,
      scheduleModified: !!p.scheduleModified
    };
  };

  for (const c of listJson) {
    const ev = c.events || {};
    const progressList = Array.isArray(ev.progress_list) ? ev.progress_list : [];
    const events = progressList
      .map(normalizeProgress)
      .filter(Boolean)
      // 최신 → 과거, dateMs null 은 맨 뒤
      .sort((a, b) => {
        if (a.dateMs == null && b.dateMs == null) return 0;
        if (a.dateMs == null) return 1;
        if (b.dateMs == null) return -1;
        return b.dateMs - a.dateMs;
      });

    const ne = c.nextEvent || ev.nextEvent || null;
    const nextEvent = ne
      ? {
          type: ne.type ?? '',
          dateMs: toMsOrNull(ne.date),
          content: ne.content ?? '',
          result: ne.result ?? '',
          location: ne.location ?? '',
          scheduleModified: !!ne.scheduleModified
        }
      : null;

    result.cases.push({
      id: c.id,
      court: c.court ?? ev.court ?? '',
      caseNo: c.caseNo ?? ev.caseno ?? '',
      caseName: c.casename ?? ev.casename ?? '',
      party: c.requestName || c.name || ev.name || '',
      done: !!c.done,
      // 2026.6 개편: 개별 사건 딥링크(/user/calendar/manage?id=…)가 폐지되고
      // /project?tab=case-schedule 로 리다이렉트되며 id 가 버려진다. 새 UI 는
      // 사건 행을 JS 로 열어 안정적인 per-case URL 을 노출하지 않으므로,
      // 모든 사건은 사건일정 목록 페이지로 통일한다(죽은 링크 방지).
      detailPageUrl: 'https://lbox.kr/project?tab=case-schedule',
      lastUpdatedMs: toMsOrNull(ev.updated_date),
      receptionMs: toMsOrNull(ev.reception_date),
      // 2026.6 개편: 오류 플래그 필드명이 is* 접두로 변경(isCaseNotExist/isErrorOccurred).
      // 구 필드명(caseNotExist/errorOccurred)도 함께 수용해 하위 호환을 유지한다.
      errorFlag: !!(ev.isCaseNotExist || ev.isErrorOccurred || ev.caseNotExist || ev.errorOccurred),
      errorMessage: ev.errmsg || null,
      nextEvent,
      events
    });
  }

  result.ok = true;
  result.totalCount = result.cases.length;

  // ── 다운로드 트리거 ───────────────────────────────────────
  let downloadTriggered = false;
  try {
    const json = JSON.stringify(result);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = FILENAME;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(url);
      a.remove();
    }, 1500);
    downloadTriggered = true;
  } catch (e) {
    return JSON.stringify({
      ok: false,
      error: { kind: 'download_failed', message: String(e && e.message) }
    });
  }

  // 요약만 반환 — 본문은 다운로드된 파일에 있다
  return JSON.stringify({
    ok: true,
    fetchedAt: result.fetchedAt,
    totalCount: result.totalCount,
    doneCount: result.cases.filter((c) => c.done).length,
    withNextCount: result.cases.filter((c) => c.nextEvent).length,
    downloadFilename: FILENAME,
    downloadTriggered
  });
})()
