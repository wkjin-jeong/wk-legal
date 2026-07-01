#!/usr/bin/env python3
"""
diff_report.py

현재 정규화 JSON(fetch_cases.js 결과)과 직전 스냅샷을 비교하여
신규 사건/신규 이벤트/종결 사건/기일변경을 추출한다.

사용 예:
    python3 diff_report.py \\
        --current /tmp/lbox_cases.json \\
        --previous ~/Library/CloudStorage/Dropbox/AI/lbox-case-progress/snapshots/2026-05-13_083000.json \\
        --output /tmp/lbox_diff.json

직전 스냅샷이 없으면 ok=true 와 함께 빈 diff 를 출력하고 종료한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))


def _event_key(e: dict[str, Any]) -> tuple:
    return (e.get("dateMs"), e.get("type") or "", e.get("content") or "")


def diff_cases(curr: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "previousAt": prev.get("fetchedAt") if prev else None,
        "currentAt": curr.get("fetchedAt"),
        "newCases": [],
        "closedCases": [],
        "newEvents": {},
        "scheduleChanges": {},
    }
    if prev is None:
        return out

    curr_by_id = {str(c["id"]): c for c in (curr.get("cases") or [])}
    prev_by_id = {str(c["id"]): c for c in (prev.get("cases") or [])}

    for cid, c in curr_by_id.items():
        if cid not in prev_by_id:
            out["newCases"].append(
                {
                    "id": c.get("id"),
                    "caseNo": c.get("caseNo"),
                    "caseName": c.get("caseName"),
                    "party": c.get("party"),
                }
            )
    for cid, c in prev_by_id.items():
        if cid not in curr_by_id:
            out["closedCases"].append(
                {
                    "id": c.get("id"),
                    "caseNo": c.get("caseNo"),
                    "caseName": c.get("caseName"),
                    "party": c.get("party"),
                }
            )

    # 신규 이벤트 / 기일변경 (양쪽에 모두 존재하는 사건만 비교)
    for cid, c in curr_by_id.items():
        prev_c = prev_by_id.get(cid)
        if not prev_c:
            continue

        prev_event_keys = {_event_key(e) for e in (prev_c.get("events") or [])}
        new_evs = [
            e for e in (c.get("events") or [])
            if _event_key(e) not in prev_event_keys
        ]
        if new_evs:
            out["newEvents"][cid] = new_evs

        # 기일변경
        prev_ne = prev_c.get("nextEvent") or {}
        curr_ne = c.get("nextEvent") or {}
        prev_dt = prev_ne.get("dateMs")
        curr_dt = curr_ne.get("dateMs")
        if prev_dt != curr_dt and (prev_dt or curr_dt):
            def _fmt(ms):
                if ms is None:
                    return "없음"
                return datetime.fromtimestamp(int(ms) / 1000, tz=KST).strftime("%Y-%m-%d %H:%M")
            out["scheduleChanges"][cid] = f"다음 기일 {_fmt(prev_dt)} → {_fmt(curr_dt)}"

    return out


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--current", required=True, type=Path)
    p.add_argument("--previous", type=Path, default=None)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args(argv)

    if not args.current.exists():
        print(f"ERROR: current file not found: {args.current}", file=sys.stderr)
        return 2
    try:
        with args.current.open(encoding="utf-8") as f:
            curr = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        # 동기화 도중의 partial write 등 — 크래시 대신 명시적 실패
        print(f"ERROR: cannot read current JSON ({args.current}): {e}", file=sys.stderr)
        return 2

    prev = None
    if args.previous and args.previous.exists():
        try:
            with args.previous.open(encoding="utf-8") as f:
                prev = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            # 스냅샷 손상 시 diff 만 생략하고 진행 (최초 실행과 동일 취급)
            print(f"WARN: previous snapshot unreadable, skipping diff: {e}", file=sys.stderr)
            prev = None

    diff = diff_cases(curr, prev)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(diff, f, ensure_ascii=False, indent=2)

    summary = {
        "previousAt": diff.get("previousAt"),
        "currentAt": diff.get("currentAt"),
        "newCases": len(diff.get("newCases") or []),
        "closedCases": len(diff.get("closedCases") or []),
        "newEventsCases": len(diff.get("newEvents") or {}),
        "newEventsTotal": sum(len(v) for v in (diff.get("newEvents") or {}).values()),
        "scheduleChanges": len(diff.get("scheduleChanges") or {}),
        "output": str(args.output),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
