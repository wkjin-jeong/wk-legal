#!/usr/bin/env python3
"""플러그인 일괄 검증 + 패키징 (표준 라이브러리만 사용). 기본 대상은 wk-ko-legal, 같은 저장소의 다른
플러그인은 --plugin <폴더>(예: wk-ko-evidence).

검증 항목:
  1. plugin.json 존재·필수 필드(name, version)
  2. skills/*/SKILL.md 존재 + frontmatter name == 폴더명
  3. SKILL.md 가 참조하는 references/*.md 실존 여부
  4. 구명칭(korean-*) 잔존 검사 — 허용 예외: law_api.py, .env.example,
     ko-law-api/SKILL.md 의 '~/.config/korean-law-api/.env' 경로 행
  5. SKILL.md·references/*.md 가 참조하는 shared/*.md 실존 여부
     (예: '../../shared/기본-문체-규칙.md', 'shared/판례-인용-정책.md') — 자기 shared/가 없는 플러그인은
     같은 저장소 다른 플러그인의 shared/에서 찾는다(함께 설치될 때 쓰는 파일이 남아 있는지)
  6. 드리프트 린트 — 개정 뒤 일부 파일에 남기 쉬운 낡은 표현(DRIFT_PATTERNS). 대상: SKILL.md,
     references/*.md, shared/*.md, README.md (CHANGELOG·tools·evals·스크립트 제외)
  7. 절 포인터 실존 — '`references/…md` 2장'·'`shared/…md` 1.1-5'·'06 3장'이 가리키는
     번호의 제목(## 2. / ### 1.1 / ### 1-3.)이 대상 문서에 있는지
  8. 스킬 수 표기 — plugin.json·마켓플레이스의 자기 항목·플러그인 README·저장소 README에서 자기 이름이 든 줄의
     '(N skills)'·'스킬 N종'이 실제 스킬 수와 같은지
  9. 마켓플레이스 등재 — 저장소 marketplace.json에 자기 항목이 있고 source가 이 폴더인지
패키징:
  evals/, __pycache__, .DS_Store, .env, *.pyc, *.bak*, 최상위 tools/·CHANGELOG.md(개발·이력용 — 런타임 불필요) 제외 후
  저장소 부모 폴더에 <name>.plugin (zip) 생성.

사용: python3 tools/build.py [--no-zip] [--plugin <폴더>]
종료코드: 0 정상 / 1 검증 실패
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 기본: 이 파일이 든 플러그인(wk-ko-legal)
if "--plugin" in sys.argv:  # 같은 저장소의 다른 플러그인 — 현재 폴더 기준, 없으면 저장소 루트 기준
    _i = sys.argv.index("--plugin")
    if _i + 1 >= len(sys.argv):
        sys.exit("ERROR --plugin 뒤에 플러그인 폴더가 필요하다")
    _cands = [Path.cwd() / sys.argv[_i + 1], ROOT.parent / sys.argv[_i + 1]]
    ROOT = next((c.resolve() for c in _cands if (c / ".claude-plugin" / "plugin.json").is_file()), _cands[0].resolve())
OLD_NAMES = ("korean-civil-litigation-drafting", "korean-legal-advisory-drafting",
             "korean-legal-writing-plan", "korean-law-api")
ALLOWED_OLD = {"law_api.py", ".env.example"}  # 런타임 호환용 구명칭 허용 파일
EXCLUDE_DIR = {"evals", "__pycache__"}
EXCLUDE_FILE = {".DS_Store", ".env", ".law_api.env"}  # 실제 인증키 파일 — 배포 zip에 포함 금지
EXCLUDE_TOP = {"tools", "CHANGELOG.md"}  # 플러그인 최상위의 개발·이력 파일 — zip에서만 제외(git 설치본에는 남는다)
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
    # 2.5.2 — 2026-09-27 기능 검증 P1 수정분의 재발 방지
    (r"target=law에서 가장 권장|`get --target law`는 이를 자동 처리|`get --target law`에서 이를 감지하면",
     "get --target law는 --promulgated가 없으면 언제나 eflaw로 대체한다(공포·시행 역전 — ko-law-api 5장)", None),
    (r"본문 조회 API가 (?:따로 )?없", "기준일 별표는 본문의 <별표단위>로 받는다 — get-asof --byl(ko-law-api 7장)", None),
    (r"(?:법률|령) 제0\d+호", "공포번호 앞자리 0 — 공식 번호로(API 원값의 0 채움)", r"→|앞자리 0"),
    (r"이자\s*→\s*비용\s*→\s*원본|원본·이자·비용의 변제 충당", "변제충당 순서 역순 — 민법 제479조 제1항(비용→이자→원본)", None),
    (r"제13조\(이사회 결의\)|제50조의2\(부수업무\)|제48조 — 이해상충",
     "자문 02 조문 제목 오기 — 여전법 제13조는 부대업무·부수업무는 제46조의2, 금융지주회사법 제48조는 자회사등의 행위제한", None),
    (r"시행령 제10조 제1항에 따른 전문투자자", "전문투자자 근거 오인용 — 자본시장법 제9조 제5항(영 제10조 제2·3항)", None),
    (r"국민권익위원회 [^\n]{0,20}\d{4}\. ?\d{1,2}\. ?\d{1,2}\.자 [^\n]{0,40}재결(?!례)",
     "재결 기관 오기 — 국민권익위원회는 재결 기관이 아니다(원문 머리에서 확인)", r"옮기지 않는다|추정하지 않는다|쓰지 않는다"),
    (r"행위시법 기준 법정형", "공소시효 — 경한 신법(2008도4376)·제249조 경과규정 누락", r"신법"),
    (r"증거능력 요건이 다르다", "피신조서 증거능력 — 2022. 1. 1. 이후 공소제기 사건은 검사·사경 작성 모두 내용 인정", None),
    (r"초적○+ 구속영장", "영장심사 표두에 구속적부심사 부호 '초적'", None),
    (r"혐의없음 의견으로 (?:\(|송치|불송치|불기소)",
     "수사권 조정 전 결어 — 경찰 '불송치 결정'·검찰 '불기소결정'", r"보다 이 정형이 우선|쓰지 않는다"),
    # 2.5.3 — P2 수정분의 재발 방지
    (r"\(부칙 확인 권고\)", "경과조치는 기준일 뒤 개정의 부칙 — get-asof '경과조치 확인'·--addenda(ko-law-api 5.1)", None),
    (r"현행본과 절대 혼용", "조문 문언이 현행과 같으면 통상 표기 가능 — get-asof --jo 대조(ko-law-api 5.4)", None),
    (r"신구법 조회", "ko-law-api에 신구법 조회 기능은 없다 — get-asof 두 기준일 --jo 대비", None),
    (r"별표시행일자.{0,20}(?:대조|비교)", "별표시행일자는 버전마다 모든 별표에 찍힌다 — 별표 개정 판별에 쓰지 않는다", r"아니다|찍힌"),
    (r"ToolSearch\(`read_case find_citing`\)", "원격 판례 MCP 로딩 — 다섯 도구 이름 모두(판례-인용-정책 1.1)", None),
    (r"수록 기준일 뒤", "최신성 기준일은 당일 포함 — 판례-인용-정책 1.1-3", None),
    (r"\d{4}\. \d{1,2}\. \d{1,2}\. 시행 개정 외국환거래법|○○일자", "자문 예문의 실재하지 않는 시행일·일자 표기 — 자리표시로(자문 05 3.4)", None),
    (r"1 2 3 4 5 … »|로그인 페이지로 (?:리다이렉트|바뀐)|chat\.lbox\.kr/api|documentType `precedent`\)로 추출",
     "lbox 화면·API 낡은 서술 — 페이지네이션 « ‹ 번호 › », 비로그인은 본문 자리 로그인 문구, route-api, data-track-click", None),
    (r"i < 2 && card\.parentElement|\|\| a\.parentElement\.parentElement;", "lbox 카드 폴백 2단계 — 카드는 앵커의 3단계 부모", None),
    (r"bookId \+ '\|' \+ tocId \+ '\|' \+ nodeId", "PDF형 실무서 카드 dedup 누락 — docId·page 포함 키로", None),
    (r"빅케이스Plus 시작하기|무제한 열람", "bigcase 페이월 증상 낡음 — '회원에게만 공개되는 판례'", None),
    (r"\['1', N\]", "lbox 옛 페이지 이동 JS(번호 버튼) — 6쪽 이후 실패. '다음으로 이동' 기준 JS로(extraction.md 1-3·1-4)", None),
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
    # 자기 shared/가 없는 플러그인(실험 플러그인 등)은 같은 저장소 다른 플러그인의 shared/를 가리킨다
    shared_dirs = [shared_dir] if shared_dir.is_dir() else sorted(
        p / "shared" for p in ROOT.parent.iterdir() if (p / ".claude-plugin" / "plugin.json").is_file() and (p / "shared").is_dir())

    def find_shared(fname: str) -> Path | None:
        return next((d / fname for d in shared_dirs if (d / fname).is_file()), None)

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
            if not find_shared(shared_name):
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
            tgt = (find_shared(ref_name) or shared_dir / ref_name) if "shared/" in path else sk_dir / "references" / ref_name
            if tgt.is_file() and not has_section(tgt.read_text(encoding="utf-8"), sec):
                errors.append(f"{rel}: 끊긴 절 포인터 '{path} {sec}' — 대상 문서에 {sec} 제목 없음")
        if md.name == "SKILL.md":
            for num, sec in set(SHORT_SECTION_REF.findall(text)):
                refs = sorted((sk_dir / "references").glob(f"{num}-*.md"))
                if not refs or not has_section(refs[0].read_text(encoding="utf-8"), sec):
                    errors.append(f"{rel}: 끊긴 절 포인터 '{num} {sec}장'")

    # 8) 스킬 수 표기 — 스킬을 더하거나 뺄 때 배포 문서의 개수 표기가 남기 쉽다
    #    저장소 README·마켓플레이스는 여러 플러그인을 담으므로 자기 이름이 든 줄·자기 항목만 본다
    n_sk = sum(1 for sk in skills if (sk / "SKILL.md").is_file())
    count_re = re.compile(r"(\d+) skills|스킬 (\d+)종")
    for f, must in ((mf, None), (ROOT / "README.md", None), (ROOT.parent / "README.md", f"`{name}`")):
        if not f.is_file():
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if must and must not in line:
                continue
            for a, b in count_re.findall(line):
                if int(a or b) != n_sk:
                    errors.append(f"{f.relative_to(ROOT.parent)}:{i}: 스킬 수 표기 {a or b} ≠ 실제 {n_sk}")
    # 9) 마켓플레이스 등재
    mkt = ROOT.parent / ".claude-plugin" / "marketplace.json"
    if mkt.is_file():
        entry = next((e for e in json.loads(mkt.read_text(encoding="utf-8")).get("plugins", []) if e.get("name") == name), None)
        if not entry:
            errors.append(f"marketplace.json: '{name}' 항목 없음")
        else:
            if entry.get("source") != f"./{ROOT.name}":
                errors.append(f"marketplace.json: '{name}' source {entry.get('source')!r} ≠ './{ROOT.name}'")
            for a, b in count_re.findall(entry.get("description", "")):
                if int(a or b) != n_sk:
                    errors.append(f"marketplace.json: '{name}' 스킬 수 표기 {a or b} ≠ 실제 {n_sk}")

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
            if (any(d in rel.parts for d in EXCLUDE_DIR) or f.name in EXCLUDE_FILE or rel.parts[0] in EXCLUDE_TOP
                    or f.suffix == ".pyc" or ".bak" in f.name):
                continue
            z.write(f, str(rel))
        count = len(z.namelist())
    print(f"OK 패키징 완료 — {out} ({count}개 파일)")


if __name__ == "__main__":
    main()
