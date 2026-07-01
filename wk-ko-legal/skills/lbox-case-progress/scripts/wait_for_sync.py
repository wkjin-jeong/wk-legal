#!/usr/bin/env python3
"""
wait_for_sync.py

원격 PC 에서 다운로드한 lbox_cases.json 이 Dropbox(또는 다른 클라우드)
동기화를 통해 이 PC 의 USER_DIR(기본 $LBOX_DIR) 에 도착할 때까지 폴링한다.

완료 판정:
  (A) `--target` 의 mtime 이 baseline 보다 새로워졌고,
  (B) 그 파일을 파싱했을 때 fetchedAt 이 baseline 보다 새로움.
  (B) 가 부재해도 mtime 변화 + JSON 파싱 성공이면 완료로 인정한다.

타임아웃:
  주어진 시간 안에 변화가 없으면 `ok:true, synced:false` 를 stdout 으로
  내보내고 exit 0 으로 종료한다. (스킬 워크플로우가 그 상태에서
  read-only 로 그대로 진행할 수 있도록.)

사용 예:
    python3 wait_for_sync.py \\
        --target ~/Library/CloudStorage/Dropbox/AI/lbox-case-progress/lbox_cases.json \\
        --baseline-mtime 1778744336.5 \\
        --baseline-fetched-at 2026-05-14T07:38:56.503Z \\
        --interval 2 --timeout 60

baseline 인자가 둘 다 생략되면 "현재 파일이 없거나 막 도착함" 으로 가정해
한 번만 검사하고 즉시 결과를 반환한다.

표준 출력 (한 줄 JSON):
    {"ok": true, "synced": true, "elapsedSeconds": 12.4,
     "newMtime": 1778744430.1, "newFetchedAt": "..."}
    {"ok": true, "synced": false, "elapsedSeconds": 60.1, "reason": "timeout"}
    {"ok": false, "error": "..."}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

KST = timezone(timedelta(hours=9))


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    """ISO-8601 문자열을 datetime 으로. None/실패 시 None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def read_fetched_at(path: Path) -> Optional[datetime]:
    """`fetchedAt` 만 안전하게 읽는다. JSON 파싱이 깨지면 None."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return parse_iso(data.get("fetchedAt"))


def check_done(
    target: Path,
    baseline_mtime: Optional[float],
    baseline_fetched_at: Optional[datetime],
) -> Tuple[bool, Optional[float], Optional[datetime]]:
    """완료 판정 + 현재 상태 반환. (done, mtime, fetched_at)."""
    if not target.is_file():
        return False, None, None

    try:
        st = target.stat()
    except OSError:
        return False, None, None

    mtime = st.st_mtime

    # baseline 보다 새로워졌는지 (없으면 0 으로 간주)
    if baseline_mtime is not None and mtime <= baseline_mtime:
        return False, mtime, None  # mtime 변화 없음 — 아직

    # 파일 파싱 시도 — partial write 자동 회피
    fetched_at = read_fetched_at(target)
    if fetched_at is None:
        return False, mtime, None  # 아직 쓰기 중일 수 있음

    # baseline fetched_at 보다 새로워졌는지
    if baseline_fetched_at is not None and fetched_at <= baseline_fetched_at:
        # 같은 데이터가 다시 sync 된 케이스 — 아직 아님
        return False, mtime, fetched_at

    return True, mtime, fetched_at


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="대기 대상 파일 경로")
    parser.add_argument(
        "--baseline-mtime",
        type=float,
        default=None,
        help="다운로드 트리거 직전의 mtime (없으면 None)",
    )
    parser.add_argument(
        "--baseline-fetched-at",
        default=None,
        help="다운로드 트리거 직전 파일의 fetchedAt (없으면 None)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("LBOX_SYNC_WAIT_INTERVAL", "2")),
        help="폴링 주기(초). 기본 2초. 환경변수 LBOX_SYNC_WAIT_INTERVAL 로도 설정.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("LBOX_SYNC_WAIT_TIMEOUT", "60")),
        help="최대 대기 시간(초). 기본 60초. 환경변수 LBOX_SYNC_WAIT_TIMEOUT 로도 설정.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="폴링 진행 메시지를 stderr 에 출력",
    )
    args = parser.parse_args(argv)

    target = Path(os.path.expanduser(args.target)).resolve()
    baseline_fetched_at = parse_iso(args.baseline_fetched_at)
    interval = max(0.5, float(args.interval))
    timeout = max(0.0, float(args.timeout))

    started = time.monotonic()
    last_progress = started

    while True:
        done, mtime, fetched_at = check_done(target, args.baseline_mtime, baseline_fetched_at)
        elapsed = time.monotonic() - started

        if done:
            out = {
                "ok": True,
                "synced": True,
                "elapsedSeconds": round(elapsed, 2),
                "newMtime": mtime,
                "newFetchedAt": fetched_at.isoformat() if fetched_at else None,
            }
            if args.progress:
                print(
                    f"[sync] 완료 — {elapsed:.1f}초 경과",
                    file=sys.stderr,
                )
            print(json.dumps(out, ensure_ascii=False))
            return 0

        if elapsed >= timeout:
            out = {
                "ok": True,
                "synced": False,
                "elapsedSeconds": round(elapsed, 2),
                "reason": "timeout",
                "currentMtime": mtime,
                "currentFetchedAt": fetched_at.isoformat() if fetched_at else None,
            }
            if args.progress:
                print(
                    f"[sync] 타임아웃 — {elapsed:.1f}초 안에 변화 없음. read-only 로 진행.",
                    file=sys.stderr,
                )
            print(json.dumps(out, ensure_ascii=False))
            return 0

        if args.progress and (time.monotonic() - last_progress) >= 10:
            print(
                f"[sync] 대기 중… {elapsed:.0f}초 경과 (timeout {int(timeout)}초)",
                file=sys.stderr,
            )
            last_progress = time.monotonic()

        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
