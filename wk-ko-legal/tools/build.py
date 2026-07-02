#!/usr/bin/env python3
"""wk-ko-legal 플러그인 일괄 검증 + 패키징 (표준 라이브러리만 사용).

검증 항목:
  1. plugin.json 존재·필수 필드(name, version)
  2. skills/*/SKILL.md 존재 + frontmatter name == 폴더명
  3. SKILL.md 가 참조하는 references/*.md 실존 여부
  4. 구명칭(korean-*) 잔존 검사 — 허용 예외: law_api.py, .env.example,
     ko-law-api/SKILL.md 의 '~/.config/korean-law-api/.env' 경로 행
  5. SKILL.md·references/*.md 가 참조하는 shared/*.md 실존 여부
     (예: '../../shared/기본-문체-규칙.md', 'shared/판례-인용-정책.md')
패키징:
  evals/, __pycache__, .DS_Store, *.pyc, *.bak* 제외 후
  저장소 부모 폴더에 <name>.plugin (zip) 생성.

사용: python3 tools/build.py [--no-zip]
종료코드: 0 정상 / 1 검증 실패
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD_NAMES = ("korean-civil-litigation-drafting", "korean-legal-advisory-drafting",
             "korean-legal-writing-plan", "korean-law-api")
ALLOWED_OLD = {"law_api.py", ".env.example"}  # 런타임 호환용 구명칭 허용 파일
EXCLUDE_DIR = {"evals", "__pycache__"}
EXCLUDE_FILE = {".DS_Store"}
# shared/ 참조 패턴: "shared/<파일>.md" 또는 "../../shared/<파일>.md" (코드펜스·따옴표 무관)
SHARED_REF = re.compile(r"(?:\.\./\.\./)?shared/([\w가-힣.\-]+\.md)")


def fail(msgs: list[str]) -> None:
    for m in msgs:
        print(f"FAIL {m}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    errors: list[str] = []

    # 1) manifest
    mf = ROOT / ".claude-plugin" / "plugin.json"
    if not mf.is_file():
        fail(["plugin.json 없음"])
    meta = json.loads(mf.read_text(encoding="utf-8"))
    name = meta.get("name")
    if not name or not meta.get("version"):
        errors.append("plugin.json: name/version 누락")

    # 2) 스킬 구조 + frontmatter name
    skills = sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir())
    if not skills:
        errors.append("skills/ 비어 있음")
    for sk in skills:
        sm = sk / "SKILL.md"
        if not sm.is_file():
            errors.append(f"{sk.name}: SKILL.md 없음")
            continue
        text = sm.read_text(encoding="utf-8")
        m = re.search(r"^name:\s*(\S+)", text, re.M)
        if not m or m.group(1) != sk.name:
            errors.append(f"{sk.name}: frontmatter name 불일치 ({m.group(1) if m else '없음'})")
        # 비대화 감시: description 한도(공식 1024자) + 본문 줄수 경고(권장 500줄 미만)
        m_desc = re.search(r"^description:\s*(.+)$", text, re.M)
        if m_desc and len(m_desc.group(1).strip()) > 1024:
            errors.append(f"{sk.name}: description {len(m_desc.group(1).strip())}자 — 1024자 한도 초과")
        parts = text.split("---", 2)
        body = parts[2] if len(parts) >= 3 else text
        if body.count("\n") > 400:
            print(f"WARN {sk.name}: SKILL.md 본문 {body.count(chr(10))}줄 — 400줄 초과(권장 500줄 미만, references 계층화 검토)",
                  file=sys.stderr)
        # 3) references 참조 실존
        for ref in set(re.findall(r"references/([\w가-힣.\-]+\.md)", text)):
            if not (sk / "references" / ref).is_file():
                errors.append(f"{sk.name}: 참조 파일 없음 references/{ref}")

    # 4) 구명칭 잔존
    for f in ROOT.rglob("*"):
        if not f.is_file() or f.name in ALLOWED_OLD or f.suffix in {".pyc"}:
            continue
        if any(d in f.parts for d in EXCLUDE_DIR) or "tools" in f.parts:
            continue
        try:
            content = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(content.splitlines(), 1):
            for old in OLD_NAMES:
                if old in line and "~/.config/korean-law-api/.env" not in line:
                    errors.append(f"{f.relative_to(ROOT)}:{i}: 구명칭 잔존 '{old}'")

    # 5) shared/ 참조 실존 — 모든 SKILL.md·references/*.md 본문에서 shared 참조를 찾아
    #    shared/ 아래 실존 검사(없으면 FAIL).
    shared_dir = ROOT / "shared"
    md_targets: list[Path] = []
    for sk in skills:
        sm = sk / "SKILL.md"
        if sm.is_file():
            md_targets.append(sm)
        md_targets.extend(sorted((sk / "references").glob("*.md")))
    for md in md_targets:
        try:
            body = md.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for shared_name in set(SHARED_REF.findall(body)):
            if not (shared_dir / shared_name).is_file():
                errors.append(f"{md.relative_to(ROOT)}: shared 참조 파일 없음 shared/{shared_name}")

    if errors:
        fail(errors)
    print(f"OK 검증 통과 — 스킬 {len(skills)}개: {', '.join(s.name for s in skills)}")

    # 패키징
    if "--no-zip" in sys.argv:
        return
    out = ROOT.parent / f"{name}.plugin"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(ROOT.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(ROOT)
            if (any(d in rel.parts for d in EXCLUDE_DIR) or f.name in EXCLUDE_FILE
                    or f.suffix == ".pyc" or ".bak" in f.name):
                continue
            z.write(f, str(rel))
        count = len(z.namelist())
    print(f"OK 패키징 완료 — {out} ({count}개 파일)")


if __name__ == "__main__":
    main()
