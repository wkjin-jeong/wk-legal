#!/usr/bin/env python3
"""
pick_source.py

두 후보 경로(클라우드 동기화 USER_DIR 과 로컬 ~/Downloads)에서
lbox_cases.json 을 찾아 mtime 비교 후 더 최근 파일을 선택한다.

선택 규칙:
- 둘 다 존재 → mtime 이 더 큰 쪽 선택
- mtime 이 동률 → primary(USER_DIR) 우선
- 한쪽만 존재 → 그쪽 선택
- 양쪽 모두 없음 → 실패 코드(2) 반환

클라우드 동기화 충돌 파일(`(conflict).json`, `(1).json`, "충돌된 사본" 등)은
정확히 `lbox_cases.json` 이름이 아닌 경우 모두 무시한다.

사용 예:
    python3 pick_source.py \\
        --primary ~/Library/CloudStorage/Dropbox/AI/lbox-case-progress/lbox_cases.json \\
        --fallback ~/Downloads/lbox_cases.json

표준 출력으로 JSON 한 줄을 출력한다:
    {"ok": true, "chosen": "/Users/.../lbox/lbox_cases.json",
     "reason": "primary_only|fallback_only|primary_newer|fallback_newer|tie_prefer_primary",
     "mtime": 1778739679.4}

`--fallback` 이 환경상 접근 불가능(Cowork 의 ~/Downloads 등)해도 OSError 를
조용히 흡수해 fallback 을 None 으로 취급한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

KST = timezone(timedelta(hours=9))

# 본 스킬이 인정하는 파일명 — 동기화 클라이언트가 만든 변형명은 모두 제외
CANONICAL_NAME = "lbox_cases.json"


def stat_or_none(path: Path) -> Optional[os.stat_result]:
    """파일이 존재하고 정규 파일이면 stat 결과를, 아니면 None 반환.

    Cowork 의 bash 샌드박스에서 ~/Downloads 같은 마운트되지 않은 경로를
    가리키면 OSError 가 날 수 있는데, 그 경우도 None 으로 처리해
    호출 측 로직이 단순해지도록 한다.
    """
    try:
        if path.name != CANONICAL_NAME:
            # 사용자가 정확한 파일을 지정해 줘야 충돌 파일을 안 건드림
            return None
        if path.is_file():
            return path.stat()
    except OSError:
        return None
    return None


def fmt_mtime(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=KST).strftime("%Y-%m-%d %H:%M:%S KST")


def pick(
    primary: Path, fallback: Optional[Path]
) -> Tuple[Optional[Path], str, Optional[float]]:
    primary_stat = stat_or_none(primary)
    fallback_stat = stat_or_none(fallback) if fallback is not None else None

    if primary_stat is None and fallback_stat is None:
        return None, "neither_found", None

    if primary_stat is None:
        return fallback, "fallback_only", fallback_stat.st_mtime  # type: ignore[union-attr]

    if fallback_stat is None:
        return primary, "primary_only", primary_stat.st_mtime

    p_mt = primary_stat.st_mtime
    f_mt = fallback_stat.st_mtime

    if p_mt > f_mt:
        return primary, "primary_newer", p_mt
    if f_mt > p_mt:
        return fallback, "fallback_newer", f_mt
    # 동률 — 사용자 지정 폴더(primary) 우선
    return primary, "tie_prefer_primary", p_mt


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--primary",
        required=True,
        help="사용자 지정 폴더의 lbox_cases.json 절대경로 (보통 클라우드 동기화 폴더)",
    )
    parser.add_argument(
        "--fallback",
        default=None,
        help="대체 후보 경로(보통 ~/Downloads/lbox_cases.json). 환경상 접근 불가하면 생략 가능.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="선택 사유와 양쪽 mtime 을 사람이 읽을 수 있는 형태로 stderr 에 함께 출력",
    )
    args = parser.parse_args(argv)

    primary = Path(os.path.expanduser(args.primary)).resolve()
    fallback = (
        Path(os.path.expanduser(args.fallback)).resolve()
        if args.fallback
        else None
    )

    chosen, reason, mtime = pick(primary, fallback)

    if args.verbose:
        print(
            f"primary  : {primary} "
            f"({fmt_mtime(primary.stat().st_mtime) if primary.is_file() and primary.name == CANONICAL_NAME else 'N/A'})",
            file=sys.stderr,
        )
        if fallback:
            try:
                fb_str = (
                    fmt_mtime(fallback.stat().st_mtime)
                    if fallback.is_file() and fallback.name == CANONICAL_NAME
                    else "N/A"
                )
            except OSError:
                fb_str = "N/A (inaccessible)"
            print(f"fallback : {fallback} ({fb_str})", file=sys.stderr)
        print(f"chosen   : {chosen} ({reason})", file=sys.stderr)

    if chosen is None:
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "chosen": str(chosen),
                "reason": reason,
                "mtime": mtime,
                "mtimeIso": datetime.fromtimestamp(mtime, tz=KST).isoformat()
                if mtime is not None
                else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
