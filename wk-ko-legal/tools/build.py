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
  6. 드리프트 린트 — 개정 뒤 일부 파일에 남기 쉬운 낡은 표현(DRIFT_PATTERNS). 대상: SKILL.md,
     references/*.md, shared/*.md, README.md (CHANGELOG·tools·evals·스크립트 제외)
  7. 절 포인터 실존 — '`references/…md` 2장'·'`shared/…md` 1.1-5'·'06 3장'이 가리키는
     번호의 제목(## 2. / ### 1.1 / ### 1-3.)이 대상 문서에 있는지
  8. 스킬 수 표기 — plugin.json·마켓플레이스·README 두 곳의 '(N skills)'·'스킬 N종'이 실제 스킬 수와 같은지
패키징:
  evals/, __pycache__, .DS_Store, .env, *.pyc, *.bak* 제외 후
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
EXCLUDE_FILE = {".DS_Store", ".env", ".law_api.env"}  # 실제 인증키 파일 — 배포 zip에 포함 금지
# shared/ 참조 패턴: "shared/<파일>.md" 또는 "../../shared/<파일>.md" (코드펜스·따옴표 무관)
SHARED_REF = re.compile(r"(?:\.\./\.\./)?shared/([\w가-힣.\-]+\.md)")
# 드리프트 린트: (정규식, 사유, 예외 — 같은 줄에 이 정규식이 있으면 허용(금지 규정을 설명하는 줄 등))
DRIFT_PATTERNS: list[tuple[str, str, str | None]] = [
    (r"lbox(?:에서|로) (?:재)?확인", "판례 확인을 lbox 단독 지정 — 판례-인용-정책 1.1(판례DB 우선)", r"류 기존 문구"),
    (r"라 할 것입니다", "'–라 할 것입니다' 권장 — 기본-문체-규칙 1.(위키 공통 §10 지양)", r"쓰지 않는다|지양|금지|린트"),
    (r"\d\. 자 ", "결정 표기 '. 자' — '.자'로(판례-인용-정책 5.)", None),
    (r"mcp__Claude_in_Chrome__", "낡은 Chrome 도구명 — 호스트 중립 표기로", None),
    (r"(?<![\w./])python (?:scripts/|\S*law_api\.py)", "'python' 실행 — python3로(Cowork VM에 python 없음)", None),
    (r"slice\(0,\s*5\)", "javascript_tool 분할 반환 — extraction.md 0장 반환 규약으로", None),
]
# SKILL.md 전용: 버전마다 바뀌는 행정규칙일련번호(13자리) 고정 예시 금지 — 자리표시(<행정규칙일련번호>)로
SKILL_ONLY_PATTERNS: list[tuple[str, str, str | None]] = [
    (r"(?<!\d)2[12]\d{11}(?!\d)", "행정규칙일련번호 고정 예시 — 자리표시로", None),
]
SECTION_REF = re.compile(r"`((?:\.\./\.\./)?(?:references|shared)/[\w가-힣.\-]+\.md)` ?(\d+(?:[.-]\d+)*)(?:장|\.)?")
SHORT_SECTION_REF = re.compile(r"(?<![\d.\w])(0\d) (\d+)장")      # SKILL.md의 '06 3장' 약식 포인터


def _heading(text: str, sec: str):
    return re.search(rf"^(#{{1,6}})\s+{re.escape(sec)}(?:[.\s)]|$)", text, re.M)


def has_section(text: str, sec: str) -> bool:
    """가리킨 번호의 제목(## 2. / ### 1.1 / ### 1-3.)이 있는지. 없으면 마지막 '.M'·'-M'을 상위 절 안의
    번호 목록 항목('M. …')으로 본다 — 예: '4.3' = 4장의 셋째 항목, '1.1-5' = 1.1절의 다섯째 항목."""
    if _heading(text, sec):
        return True
    m = re.match(r"(.+)[.-](\d+)$", sec)
    if not m:
        return False
    parent, item = m.groups()
    h = _heading(text, parent)
    if not h:
        return has_section(text, parent) if re.search(r"[.-]\d+$", parent) else False
    nxt = re.search(rf"^#{{1,{len(h.group(1))}}}\s", text[h.end():], re.M)
    span = text[h.end(): h.end() + nxt.start()] if nxt else text[h.end():]
    return bool(re.search(rf"^\s*(?:[-*]\s+)?{item}[.)]\s", span, re.M))


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

    # 6) 드리프트 린트 + 7) 절 포인터 실존
    lint_targets = md_targets + sorted(shared_dir.glob("*.md")) + [ROOT / "README.md"]
    for md in lint_targets:
        if not md.is_file():
            continue
        text = md.read_text(encoding="utf-8")
        rel = md.relative_to(ROOT)
        pats = DRIFT_PATTERNS + (SKILL_ONLY_PATTERNS if md.name == "SKILL.md" else [])
        for i, line in enumerate(text.splitlines(), 1):
            for pat, why, allow in pats:
                if re.search(pat, line) and not (allow and re.search(allow, line)):
                    errors.append(f"{rel}:{i}: 드리프트 — {why}")
        sk_dir = md.parent if md.name == "SKILL.md" else md.parent.parent
        for path, sec in set(SECTION_REF.findall(text)):
            ref_name = path.rsplit("/", 1)[1]
            tgt = shared_dir / ref_name if "shared/" in path else sk_dir / "references" / ref_name
            if tgt.is_file() and not has_section(tgt.read_text(encoding="utf-8"), sec):
                errors.append(f"{rel}: 끊긴 절 포인터 '{path} {sec}' — 대상 문서에 {sec} 제목 없음")
        if md.name == "SKILL.md":
            for num, sec in set(SHORT_SECTION_REF.findall(text)):
                refs = sorted((sk_dir / "references").glob(f"{num}-*.md"))
                if not refs or not has_section(refs[0].read_text(encoding="utf-8"), sec):
                    errors.append(f"{rel}: 끊긴 절 포인터 '{num} {sec}장'")

    # 8) 스킬 수 표기 — 스킬을 더하거나 뺄 때 배포 문서의 개수 표기가 남기 쉽다
    n_sk = sum(1 for sk in skills if (sk / "SKILL.md").is_file())
    for f in (mf, ROOT.parent / ".claude-plugin" / "marketplace.json", ROOT.parent / "README.md", ROOT / "README.md"):
        if not f.is_file():
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for a, b in re.findall(r"(\d+) skills|스킬 (\d+)종", line):
                if int(a or b) != n_sk:
                    errors.append(f"{f.relative_to(ROOT.parent)}:{i}: 스킬 수 표기 {a or b} ≠ 실제 {n_sk}")

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
