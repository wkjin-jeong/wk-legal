#!/usr/bin/env python3
"""ko-evidence-analysis evidence.py 회귀 검사 — 합성 픽스처만 쓴다(가공 인물·번호, 실물 기록 없음).

임시 폴더에 쪽 단위 변환본(4키 front-matter + _index.md)을 만들고 서브커맨드를 돌려
2026-09-26 검수에서 재현한 결함(E1~E7)이 다시 생기지 않는지 확인한다.

사용: python3 tools/evidence_regress.py [-v]
종료코드: 0 전부 통과 / 1 실패 있음
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EV = Path(__file__).resolve().parent.parent / "skills" / "ko-evidence-analysis" / "scripts" / "evidence.py"
VERBOSE = "-v" in sys.argv
FAILS: list[str] = []


def run(root: Path, *args: str) -> tuple[int, str, str]:
    p = subprocess.run([sys.executable, str(EV), *args], cwd=root, capture_output=True, text=True)
    if VERBOSE:
        print(f"  $ evidence.py {' '.join(args)} → {p.returncode}\n    {p.stdout.strip()[:300]}")
    return p.returncode, p.stdout, p.stderr


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {detail}"))
    if not ok:
        FAILS.append(name)


def record(folder: Path, pages: list[tuple], bom: bool = False, front_matter: bool = True) -> None:
    """pages: [(printed_page, heading, 본문)]"""
    (folder / "pages").mkdir(parents=True)
    for i, (pp, hd, body) in enumerate(pages, 1):
        fm = (f"---\npdf_page: {i}\nprinted_page: {'null' if pp is None else pp}\nheading: {hd}\nnotes: null\n---\n"
              if front_matter else "")
        (folder / "pages" / f"page_{i:03d}.md").write_text(("﻿" if bom else "") + fm + f"# {hd}\n{body}\n",
                                                          encoding="utf-8")
    (folder / "_index.md").write_text(
        f"- 원본: {folder.name}.pdf ({len(pages)}쪽)\n- 변환: {len(pages)}쪽 / 누락: 없음\n- 판독불가·불명확 표식: 0개\n\n"
        "| 시작pdf | 끝pdf | 인쇄면 | 제목 | 파일 | 비고 |\n|---|---|---|---|---|---|\n", encoding="utf-8")


EVLIST_HEAD = ("| 순번 | 작성 | 쪽수(수) | 쪽수(증) | 증거명칭 | 성명 | 참조사항 등 | 신청기일 | 증거의견 기일 | 증거의견 내용 "
               "| 증거결정 기일 | 증거결정 내용 | 증거조사기일 | 비고 |\n" + "|---" * 14 + "|\n")


def evrow(no: str, who: str, pg: str, name: str, person: str = "") -> str:
    return f"| {no} | {who} | {pg} | | {name} | {person} |" + " |" * 8 + "\n"


def build(root: Path) -> None:
    b = root / "변환본"
    record(b / "계약서", [
        (1, "부동산매매계약서", "| 구분 | 금액 |\n|---|---|\n| 계약금 | 10,000,000 |\n| 중도금 | 40,000,000 |\n"
                          "| 잔금 | 50,000,000 |\n| 합계 | 100,000,000 |"),
        (2, "부동산매매계약서", "매도인은 잔금 수령과 동시에 소유권이전등기에 필요한 서류를 교부한다.\n\n"
                          "차용인 홍길동은 위 금액을 약속한 날짜에 갚기로 약속합니다."),
        (3, "부동산매매계약서", "특약사항: 잔금 지급일은 당사자가 협의하여 정한다."),
    ])
    record(b / "내역서", [(1, "공사비 내역서", "| 항목 | 금액 |\n|---|---|\n| 자재비 | 100 |\n| 인건비 | 200 |\n"
                                        "| 경비 | 300 |\n|  | 600 |")])
    record(b / "가림", [(1, "사실확인서", "작성자 [이름가림 홍길동 010-1234-5678]은 다음과 같이 확인합니다.")])
    record(b / "규격밖", [(1, "진술서", "front-matter가 없는 쪽")], front_matter=False)
    record(b / "BOM", [(1, "진술서", "BOM이 붙은 쪽 파일")], bom=True)
    record(b / "증거목록", [(None, "증거목록", EVLIST_HEAD + evrow("1", "검사", "1", "수사보고") +
                                          evrow("2", "〃", "3", "진술조서", "홍길동") +
                                          "| | | 별권 1 | | | |" + " |" * 8 + "\n" +
                                          evrow("3", "〃", "1", "피의자신문조서", "임꺽정") +
                                          evrow("3-1", "〃", "2", "사진"))])
    record(b / "본권", [(1, "수사보고", "수사보고 본문"), (2, "수사보고", "첨부"),
                       (3, "진술조서", "진술조서 본문"), (4, "진술조서", "계속")])
    record(b / "별권1", [(1, "피의자신문조서", "피의자신문조서 본문"), (2, "사진", "[사진: 현장]")])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="evreg_"))
    try:
        root, out = tmp / "case", tmp / "case" / "기록분석"
        root.mkdir()
        build(root)
        A = ("--root", str(root))
        O = ("--out", str(out))

        # E6 — 규격 밖·BOM
        rc, so, _ = run(root, "gate", *A, "변환본/규격밖")
        g = json.loads(so)["records"][0]
        check("E6 gate: front-matter 없는 폴더는 오류(exit 3·등급 없음)", rc == 3 and g["등급"] is None and g["오류"], so[:200])
        rc, _, _ = run(root, "index", *A, *O, "변환본/규격밖")
        check("E6 index: 규격 밖 폴더를 색인하지 않음", rc == 3)
        rc, so, _ = run(root, "gate", *A, "변환본/BOM")
        check("E6 gate: BOM이 붙은 4키 쪽은 A등급", rc == 0 and json.loads(so)["records"][0]["등급"] == "A", so[:200])

        # E7 — 경고에 가림 내용이 나가지 않음
        rc, so, se = run(root, "gate", *A, "변환본/가림")
        check("E7 gate: 고정 표식 밖 표기는 종류·건수만", "이름가림" in so and "홍길동" not in so + se and "010" not in so + se,
              so[:200])

        # 색인
        rc, so, _ = run(root, "index", *A, *O, "변환본/계약서", "변환본/내역서", "변환본/본권", "변환본/별권1")
        tot = json.loads(so.strip().splitlines()[-1]).get("색인_전체", {})
        check("index: 합성 record 4건", rc == 0)
        check("S5 index: 약식 판정용 색인_전체(record·쪽·글자수)", tot.get("record") == 4 and tot.get("쪽") == 10
              and tot.get("글자수", 0) > 0, so)

        # E2 — sum
        rc, so, _ = run(root, "sum", *A, "--record", "변환본/계약서", "--pages", "1", "--col", "금액", "--check")
        s = json.loads(so)
        check("E2 sum: 라벨 있는 말미 데이터 행(잔금)을 빼지 않음", s["합계"] == 100000000 and s["건수"] == 3
              and s.get("말미행_합계추정") == 0, so)
        rc, so, _ = run(root, "sum", *A, "--record", "변환본/내역서", "--pages", "1", "--col", "금액")
        s = json.loads(so)
        check("E2 sum: 라벨 없는 말미 합계 행은 뺌", s["합계"] == 600 and s["건수"] == 3 and s.get("말미행_합계추정") == 1, so)

        # E3 — verify
        card = out / "_작업" / "카드" / "계약서" / "1-3.md"
        card.parent.mkdir(parents=True, exist_ok=True)

        def verify(line: str) -> tuple[int, dict]:
            card.write_text(f"---\nrecord_id: 계약서\npdf: [1, 3]\n---\n- {line}\n", encoding="utf-8")
            rc, so, _ = run(root, "verify", *A, *O, str(card))
            return rc, json.loads(so)

        rc, v = verify('"차용인 홍길동은 위 금액을"{{p:2}}')
        check("E3 verify: 바른 인용은 통과", rc == 0 and v["불일치"] == 0, str(v))
        rc, v = verify('"교부한다. … 차용인 홍길동은"{{p:2}}')
        check("E3 verify: 순서대로인 중략 인용은 통과", rc == 0, str(v))
        rc, v = verify('"서류를 교부한다. … 특약사항: 잔금"{{p:2-3}}')
        check("E3 verify: 쪽 경계를 넘는 인용은 통과", rc == 0, str(v))
        rc, v = verify('"갚기로 약속합니다…차용인 홍길동은"{{p:2}}')
        check("E3 verify: 조각 순서가 뒤집힌 인용은 실패", rc == 3 and v["불일치"] == 1, str(v))
        rc, v = verify('"매도인은 잔금 수령과 동시에"{{p:2-999}}')
        check("E3 verify: 없는 쪽을 포함한 인용 좌표는 실패", rc == 3 and v["불일치"] == 1, str(v))
        rc, v = verify('"홍…금…다"{{p:2}}')
        check("E3 verify: 너무 짧은 조각으로 된 중략 인용은 실패", rc == 3 and v["불일치"] == 1, str(v))
        rc, v = verify('"……"{{p:2}}')
        check("E3 verify: 중략 부호만 있는 인용은 실패", rc == 3 and v["불일치"] == 1, str(v))
        card.unlink()

        # E5 — evlist
        rc, so, _ = run(root, "evlist", *A, *O, "--record", "변환본/증거목록", "--list-id", "L1", "--pages", "1")
        rows = [json.loads(x) for x in (out / "_작업" / "증거목록_L1.jsonl").read_text(encoding="utf-8").splitlines()]
        r3 = next((r for r in rows if r["순번표기"] == "3"), {})
        r31 = next((r for r in rows if r["순번표기"] == "3-1"), {})
        check("E5 evlist: 권 전환 줄 뒤의 ditto는 앞 행 값", r3.get("작성") == "검사" and r3.get("권") == "별권1", str(r3))
        check("E5 evlist: 순번 '3-1'은 순번 3·가지 1", r31.get("순번") == 3 and r31.get("순번가지") == 1, str(r31))

        # E4 — 권 표기(공백) 정규화와 불일치 감지
        mp = out / "_작업" / "사건세트.json"
        m = json.loads(mp.read_text(encoding="utf-8"))
        for r in m["records"]:
            if r["record_id"] in ("본권", "별권1"):
                r["권"] = "본권" if r["record_id"] == "본권" else "별권 1"  # 문서대로 띄어 씀
                r["printed_page_뜻"] = "기록 면수"
        mp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, so, _ = run(root, "assign", *O, "--list-id", "L1")
        asg = [json.loads(x) for x in (out / "_작업" / "배정_L1.jsonl").read_text(encoding="utf-8").splitlines()]
        vol = [x for x in asg if x["record_id"] == "별권1"]
        check("E4 assign: '별권 1'(띄어 씀)도 목록의 '별권1'과 맞춤",
              rc == 0 and [(x["순번"], x.get("순번가지"), x["상태"]) for x in vol] == [(3, None, "확정"), (3, 1, "확정")],
              so + str(vol))
        for r in m["records"]:
            if r["record_id"] == "별권1":
                r["권"] = "별권 9"
        mp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, so, _ = run(root, "assign", *O, "--list-id", "L1")
        check("E4 assign: 목록에 없는 권이면 exit 3과 권_불일치", rc == 3 and "권_불일치" in so, so)
        rc, so, _ = run(root, "coverage", *O, "--list-id", "L1", "--no-write")
        check("coverage: 목록→기록 실행", rc in (0, 3) and "보유" in so, so)

        # S4 — cardfill: 기계 필드는 스크립트가 채운다
        for r in m["records"]:
            if r["record_id"] == "별권1":
                r["권"] = "별권 1"
        mp.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        run(root, "assign", *O, "--list-id", "L1")
        kdir = out / "_작업" / "카드"

        def write_card(rid: str, rng: str, body: str) -> Path:
            f = kdir / rid / f"{rng}.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"---\n문서종류: 범용 폴백\n문서성격: {{공문서: 불명, 처분문서: false, 형태: 사본(스캔)}}\n제목: 합성\n---\n{body}\n",
                         encoding="utf-8")
            return f

        def fm(f: Path) -> str:
            return f.read_text(encoding="utf-8").split("\n---\n")[0]

        c1 = write_card("계약서", "1-3", '- "차용인 홍길동은 위 금액을"{{p:2}}')
        c2 = write_card("별권1", "1-2", "- 요지 {{p:1}}")
        c3 = write_card("본권", "1-4", "- 요지 {{p:3}}")
        rc, so, _ = run(root, "cardfill", *A, *O, str(c1), str(c2), str(c3))
        f1, f2, f3 = fm(c1), fm(c2), fm(c3)
        check("S4 cardfill: 민사 카드에 record_id·pdf·출처sha256·좌표·표식을 채우고 모델 필드는 보존",
              rc == 0 and "record_id: 계약서" in f1 and "pdf: [1, 3]" in f1 and "문건내쪽: [1, 3]" in f1
              and "제목: 합성" in f1 and "문서성격: {공문서: 불명" in f1
              and len(__import__("re").findall(r"출처sha256: [0-9a-f]{64}", f1)) == 1, so + f1)
        check("S4 cardfill: 형사 카드의 권·면수·증거ID(가지 순번 포함)",
              "좌표: {권: 별권 1, 면수: [1, 2]}" in f2
              and "증거ID: [{목록ID: L1, 순번: 3, 상태: 확정}, {목록ID: L1, 순번: 3, 가지: 1, 상태: 확정}]" in f2
              and "경계불일치: false" in f2, f2)
        check("S4 cardfill: 순번 경계를 넘는 카드는 경계불일치", "경계불일치: true" in f3, f3)
        rc, so, _ = run(root, "verify", *A, *O, str(c1))
        check("S4 verify: cardfill한 카드의 인용 통과", rc == 0, so)
        sha_before = __import__("re").search(r"출처sha256: (\w+)", f1).group(1)
        pg = root / "변환본" / "계약서" / "pages" / "page_002.md"
        pg.write_text(pg.read_text(encoding="utf-8") + "\n추가된 문장\n", encoding="utf-8")
        run(root, "index", *A, *O, "변환본/계약서")
        run(root, "cardfill", *A, *O, str(c1))
        rc, so, _ = run(root, "coverage", *O, "--no-write")
        n_stale = json.loads(so)["누락·변경"]
        check("S4 cardfill: 기존 출처sha256은 덮어쓰지 않아 coverage가 원문 변경을 잡음", sha_before in fm(c1) and rc == 3, so)
        run(root, "cardfill", *A, *O, "--resha", str(c1))
        rc, so, _ = run(root, "coverage", *O, "--no-write")
        check("S4 cardfill --resha: 원문을 다시 확인한 카드의 sha 갱신 → 원문 변경 1건 해소",
              sha_before not in fm(c1) and json.loads(so)["누락·변경"] == n_stale - 1, so)
        for c in (c1, c2, c3):
            c.unlink()

        # E1 — import 제자리 --replace 거부, 외부 원천 재반입은 교체
        rc, _, se = run(root, "import", *A, *O, "--src", str(root / "변환본" / "계약서"), "--dest", "변환본", "--replace")
        check("E1 import: 원천=대상 --replace 거부(변환본 보존)",
              rc == 2 and (root / "변환본" / "계약서" / "pages" / "page_001.md").exists(), se)
        ext = tmp / "외부" / "계약서"
        shutil.copytree(root / "변환본" / "계약서", ext)
        rc, _, se = run(root, "import", *A, *O, "--src", str(ext), "--dest", "변환본", "--replace")
        left = [p.name for p in (root / "변환본").iterdir() if p.name.startswith(".")]
        check("E1 import: 외부 원천 --replace는 교체하고 임시 폴더를 남기지 않음",
              rc == 0 and (root / "변환본" / "계약서" / "pages").is_dir() and not left, se + str(left))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'실패 ' + str(len(FAILS)) + '건' if FAILS else '전부 통과'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
