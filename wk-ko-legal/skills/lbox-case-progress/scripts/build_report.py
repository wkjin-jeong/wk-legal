#!/usr/bin/env python3
"""
build_report.py

fetch_cases.js 가 반환한 정규화 JSON 을 받아 markdown 보고서를 작성한다.
직전 스냅샷과의 변경 사항을 diff.json 으로 받아 상단 요약에 반영하고,
최종 산출물을 메인 경로(--output)와 일자별 스냅샷(--snapshot-dir)에 저장한다.

사용 예:
    python3 build_report.py \\
        --cases /tmp/lbox_cases.json \\
        --diff /tmp/lbox_diff.json \\
        --output ~/Library/CloudStorage/Dropbox/AI/lbox-case-progress/lbox-case-progress.md \\
        --snapshot-dir ~/Library/CloudStorage/Dropbox/AI/lbox-case-progress/snapshots
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

KST = timezone(timedelta(hours=9))

# progress.type → 한글 표시명
# lbox 가 실제 사용하는 값은 "date"(기일), "send"(송달), "submit"(제출),
# "order"(명령), "etc"(기타) 등이다. 알려지지 않은 값은 그대로 표시.
TYPE_LABEL = {
    "date": "기일",
    "hearing": "기일",       # 일부 응답에서 별도로 쓰는 변형
    "submit": "제출서류",
    "send": "송달",
    "order": "명령",
    "doc": "문서",
    "etc": "기타",
}

# 기일로 취급할 type 집합 — ★ 표시 및 정렬 우선순위에 사용
HEARING_TYPES = {"date", "hearing"}


def _humanize_delta(seconds: float) -> str:
    """경과 초를 한국어로 자연스럽게 표기. 30분/시간/일/개월 단위로 절삭."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"약 {minutes}분"
    hours = minutes // 60
    if hours < 24:
        return f"약 {hours}시간"
    days = hours // 24
    if days < 60:
        return f"약 {days}일"
    months = days // 30
    return f"약 {months}개월"


def fmt_dt(ms: int | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """unix ms → KST 문자열. None 이면 빈 문자열."""
    if ms is None or ms == "":
        return ""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=KST).strftime(fmt)
    except (TypeError, ValueError, OSError):
        return ""


def fmt_date(ms: int | None) -> str:
    return fmt_dt(ms, "%Y-%m-%d")


def type_label(t: str) -> str:
    if not t:
        return "기타"
    return TYPE_LABEL.get(t, t)


def md_escape_pipe(s: str) -> str:
    """markdown 표 셀에 들어갈 때 | 와 줄바꿈을 이스케이프."""
    if s is None:
        return ""
    return str(s).replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()


def case_sort_key(c: dict[str, Any]) -> tuple[int, int]:
    """다음 기일 임박 순 정렬. 다음 기일 없으면 맨 뒤."""
    ne = c.get("nextEvent")
    if ne and ne.get("dateMs") is not None:
        return (0, int(ne["dateMs"]))
    # done 상태는 더 뒤로 — 보통 별도 섹션으로 빠지지만 안전망
    return (1 if not c.get("done") else 2, int(c.get("lastUpdatedMs") or 0) * -1)


def render_header(payload: dict[str, Any], diff: dict[str, Any] | None) -> str:
    cases = payload.get("cases") or []
    total = len(cases)
    in_progress = sum(1 for c in cases if not c.get("done"))
    done = total - in_progress

    fetched_at = payload.get("fetchedAt") or ""
    try:
        fetched_local_dt = datetime.fromisoformat(
            fetched_at.replace("Z", "+00:00")
        ).astimezone(KST)
        fetched_local = fetched_local_dt.strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        fetched_local_dt = None
        fetched_local = fetched_at

    now_kst = datetime.now(KST)
    now_str = now_kst.strftime("%Y-%m-%d %H:%M KST")

    # 보고서 작성 시각과 데이터 회수 시각 차이가 크면 다른 PC 에서 회수한 데이터로 간주
    # (멀티 PC 클라우드 동기화 환경에서 read-only 로 동작하는 경우)
    stale_note = ""
    if fetched_local_dt is not None:
        delta_seconds = (now_kst - fetched_local_dt).total_seconds()
        if delta_seconds >= 30 * 60:  # 30분 이상 차이면 명시
            stale_note = (
                f" (이 PC 가 lbox 에서 직접 회수한 것이 아니라, "
                f"동기화된 다른 출처에서 읽었습니다 — {_humanize_delta(delta_seconds)} 전 데이터)"
            )

    lines = [
        "# lbox 사건 경과 보고서",
        "",
        f"- 데이터 회수 시각: {fetched_local}{stale_note}",
        f"- 보고서 작성 시각: {now_str}",
        f"- 등록 사건 총 {total}건 (진행 {in_progress} · 종결 {done})",
    ]

    if diff and diff.get("previousAt"):
        try:
            prev_local = (
                datetime.fromisoformat(str(diff["previousAt"]).replace("Z", "+00:00"))
                .astimezone(KST)
                .strftime("%Y-%m-%d %H:%M KST")
            )
        except Exception:
            prev_local = diff["previousAt"]
        nc = len(diff.get("newCases") or [])
        ne_total = sum(len(v) for v in (diff.get("newEvents") or {}).values())
        cc = len(diff.get("closedCases") or [])
        sc = len(diff.get("scheduleChanges") or {})
        lines.append(f"- 직전 갱신: {prev_local}")
        lines.append(
            f"- 변경: 신규 사건 {nc}건 · 신규 이벤트 {ne_total}건 · 종결 {cc}건 · 기일변경 {sc}건"
        )
    return "\n".join(lines)


def render_change_summary(
    cases: list[dict[str, Any]], diff: dict[str, Any] | None
) -> str:
    if diff is None or not diff.get("previousAt"):
        return "## 변경 요약\n\n> 최초 실행입니다. 변경 감지를 생략합니다."

    new_cases = diff.get("newCases") or []
    closed_cases = diff.get("closedCases") or []
    new_events = diff.get("newEvents") or {}
    schedule_changes = diff.get("scheduleChanges") or {}

    if not (new_cases or closed_cases or new_events or schedule_changes):
        return "## 변경 요약\n\n변경 사항 없음"

    by_id = {str(c["id"]): c for c in cases}

    rows: list[tuple[str, str, str]] = []
    # 신규 이벤트
    for cid, events in new_events.items():
        c = by_id.get(str(cid))
        if not c:
            continue
        bullets = []
        for e in events:
            dt = fmt_date(e.get("dateMs"))
            bullets.append(
                f"{dt} {type_label(e.get('type', ''))} {e.get('content', '')}".strip()
            )
        rows.append((c["caseNo"], c["caseName"], "; ".join(bullets)))
    # 기일변경
    for cid, msg in schedule_changes.items():
        c = by_id.get(str(cid))
        if not c:
            continue
        rows.append((c["caseNo"], c["caseName"], f"기일변경 — {msg}"))
    # 신규 사건
    for c in new_cases:
        rows.append(("(신규)", f"{c.get('caseNo', '')} {c.get('caseName', '')}".strip(), "새 사건으로 등록됨"))
    # 종결 사건
    for c in closed_cases:
        rows.append(("(종결)", f"{c.get('caseNo', '')} {c.get('caseName', '')}".strip(), "이전 보고서 대비 사라짐(종결/삭제)"))

    out = ["## 변경 요약", "", "| 사건번호 | 사건명 | 변경 내용 |", "|---|---|---|"]
    for case_no, case_name, change in rows:
        out.append(
            f"| {md_escape_pipe(case_no)} | {md_escape_pipe(case_name)} | {md_escape_pipe(change)} |"
        )
    return "\n".join(out)


def render_case_table(cases: list[dict[str, Any]], section_title: str, done_section: bool) -> str:
    if not cases:
        return ""
    out = [f"## {section_title}", ""]
    if done_section:
        out.extend([
            "| # | 당사자 | 법원 | 사건번호 | 사건명 | 결과 | 결과일 |",
            "|---|---|---|---|---|---|---|",
        ])
    else:
        out.extend([
            "| # | 당사자 | 법원 | 사건번호 | 사건명 | 다음 기일 | 최종 갱신 |",
            "|---|---|---|---|---|---|---|",
        ])
    for i, c in enumerate(cases, start=1):
        if done_section:
            # 종결 사건은 가장 마지막 이벤트를 결과로 표시
            last = c["events"][0] if c.get("events") else {}
            result = last.get("content") or "종결"
            result_date = fmt_date(last.get("dateMs"))
            row = [
                str(i),
                c.get("party", ""),
                c.get("court", ""),
                c.get("caseNo", ""),
                c.get("caseName", ""),
                result,
                result_date,
            ]
        else:
            ne = c.get("nextEvent") or {}
            ne_str = ""
            if ne and ne.get("dateMs") is not None:
                ne_str = f"{fmt_dt(ne.get('dateMs'))} {ne.get('content', '')}".strip()
            row = [
                str(i),
                c.get("party", ""),
                c.get("court", ""),
                c.get("caseNo", ""),
                c.get("caseName", ""),
                ne_str,
                fmt_date(c.get("lastUpdatedMs")),
            ]
        out.append("| " + " | ".join(md_escape_pipe(v) for v in row) + " |")
    return "\n".join(out)


def _location_is_redundant(location: str, content: str) -> bool:
    """location 이 content 안에 이미 들어 있으면 중복 표기로 간주."""
    loc = (location or "").strip()
    if not loc:
        return True
    return loc in (content or "")


def render_case_detail(idx: int, c: dict[str, Any]) -> str:
    case_no = c.get("caseNo", "")
    case_name = c.get("caseName", "")
    party = c.get("party", "")
    court = c.get("court", "")
    detail_url = c.get("detailPageUrl", "")
    done = c.get("done")
    reception = fmt_date(c.get("receptionMs"))

    header_suffix = ""
    if c.get("errorFlag"):
        header_suffix = " ⚠️ lbox 조회 실패"

    out = [f"### [{idx}] {case_no} {case_name} — {party}{header_suffix}"]
    bullets = [f"- 법원: {court}"]
    bullets.append(f"- 진행 상태: {'종결' if done else '진행 중'}")
    if reception:
        bullets.append(f"- 접수일: {reception}")
    ne = c.get("nextEvent") or {}
    if ne and ne.get("dateMs") is not None:
        ne_dt = fmt_dt(ne.get("dateMs"))
        content = ne.get("content", "") or ""
        loc_raw = ne.get("location", "") or ""
        loc = (
            ""
            if _location_is_redundant(loc_raw, content)
            else f" ({loc_raw.strip()})"
        )
        bullets.append(f"- 다음 기일: **{ne_dt}** {content}{loc}")
    if detail_url:
        bullets.append(f"- lbox 상세: {detail_url}")
    out.append("\n".join(bullets))

    if c.get("errorFlag"):
        msg = c.get("errorMessage") or "lbox 가 법원 시스템에서 이 사건 정보를 받아오지 못했습니다."
        out.append("")
        out.append(f"> {msg} lbox 홈페이지에서 직접 확인해 주세요.")
        return "\n".join(out)

    events = c.get("events") or []
    if not events:
        out.append("")
        out.append("> 등록된 진행 이벤트가 없습니다.")
        return "\n".join(out)

    out.extend(["", "| 일자 | 구분 | 내용 | 결과 | 장소 |", "|---|---|---|---|---|"])
    for e in events:
        date_str = fmt_date(e.get("dateMs")) or "(일자 미상)"
        type_str = type_label(e.get("type", ""))
        if e.get("forCalendar") and e.get("type") in HEARING_TYPES:
            type_str = "★ " + type_str
        result_str = e.get("result", "") or ""
        if e.get("scheduleModified") and "기일변경" not in result_str:
            result_str = (result_str + " (기일변경)").strip()
        content_str = e.get("content", "") or ""
        location_raw = e.get("location", "") or ""
        # content 에 이미 위치/시간이 박혀 있는 경우가 많아 중복 표기 회피
        location_str = (
            "" if _location_is_redundant(location_raw, content_str) else location_raw.strip()
        )
        out.append(
            "| "
            + " | ".join(
                md_escape_pipe(v)
                for v in [
                    date_str,
                    type_str,
                    content_str,
                    result_str,
                    location_str,
                ]
            )
            + " |"
        )
    return "\n".join(out)


def build_markdown(payload: dict[str, Any], diff: dict[str, Any] | None) -> str:
    cases = list(payload.get("cases") or [])
    if not cases:
        return (
            render_header(payload, diff)
            + "\n\n현재 lbox 사건일정관리에 등록된 사건이 없습니다.\n"
        )

    cases.sort(key=case_sort_key)
    in_progress = [c for c in cases if not c.get("done")]
    done_cases = [c for c in cases if c.get("done")]

    parts: list[str] = []
    parts.append(render_header(payload, diff))
    parts.append("")
    parts.append(render_change_summary(cases, diff))
    parts.append("")
    if in_progress:
        parts.append(render_case_table(in_progress, "진행 중 사건", done_section=False))
        parts.append("")
    if done_cases:
        parts.append(render_case_table(done_cases, "종결 사건", done_section=True))
        parts.append("")
    parts.append("## 사건별 상세")
    parts.append("")
    # 상세는 위 표와 동일 순서
    all_ordered = in_progress + done_cases
    for i, c in enumerate(all_ordered, start=1):
        parts.append(render_case_detail(i, c))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_snapshot(payload: dict[str, Any], snapshot_dir: Path) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    snap_path = snapshot_dir / f"{ts}.json"
    with snap_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return snap_path


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", required=True, type=Path)
    p.add_argument("--diff", type=Path, default=None)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--snapshot-dir", required=True, type=Path)
    args = p.parse_args(argv)

    payload = load_json(args.cases)
    if payload is None:
        print(f"ERROR: cases file not found: {args.cases}", file=sys.stderr)
        return 2

    if not payload.get("ok"):
        err = payload.get("error") or {}
        print(
            f"ERROR: fetch_cases.js returned ok=false. error={err}",
            file=sys.stderr,
        )
        return 3

    diff = load_json(args.diff)

    md = build_markdown(payload, diff)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        f.write(md)
    snap = save_snapshot(payload, args.snapshot_dir)

    summary = {
        "wrote": str(args.output),
        "snapshot": str(snap),
        "totalCases": len(payload.get("cases") or []),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
