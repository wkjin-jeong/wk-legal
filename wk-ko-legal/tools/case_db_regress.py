#!/usr/bin/env python3
"""shared/case_db.py(로컬 판례DB 읽기 전용 조회) 회귀 검사 — 위키가 없으면 건너뛴다.

    python3 tools/case_db_regress.py

기대값은 2026-09-25 판례DB 기준이다(재정·심판·병합·참조조문·인용 표기·원본 경로 사례는 2026-09-27 — 보유 판례가 바뀌면 사례를 다시 고른다). 위키에는 아무것도 쓰지 않는다 —
검사 전후로 색인 파일과 정규화 도구 폴더의 수정 시각을 비교한다.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "shared", "case_db.py")
WIKI = os.path.expanduser(os.environ.get("WK_LEGAL_WIKI_ROOT") or "~/LLM-wiki")
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def run(*args: str, env: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, SCRIPT, *args], env=env or ENV, cwd=cwd, capture_output=True, text=True)


def synth_wiki(t: str) -> str:
    """수집 2건(최대 2026-06-15) + 수행 1건(2026-08-20)의 최소 합성 색인 — 수집 최대 선고일을 따로 내는지 본다."""
    os.makedirs(os.path.join(t, "판례DB"))
    con = sqlite3.connect(os.path.join(t, "판례DB", "_색인.sqlite"))
    con.execute("CREATE TABLE cases(case_id TEXT PRIMARY KEY, date TEXT, text_status TEXT, file TEXT)")
    con.executemany("INSERT INTO cases VALUES (?, ?, ?, ?)", [
        ("대법원 2026다1", "2026-06-15", "official-html", "수집/민사/a.md"),
        ("대법원 2025다1", "2025-01-02", "official-html", "수집/민사/b.md"),
        ("서울고등법원 2026나1", "2026-08-20", "auto-extracted", "수행/민사/c.md")])
    con.commit(); con.close()
    return t


def stamp() -> tuple:
    db = os.path.join(WIKI, "판례DB", "_색인.sqlite")
    tools = os.path.join(WIKI, "판례DB", "_도구")
    return (os.path.getmtime(db), os.path.getmtime(tools),
            sorted(os.listdir(os.path.join(tools, "__pycache__"))) if os.path.isdir(os.path.join(tools, "__pycache__")) else [])


def main() -> None:
    if not os.path.isfile(os.path.join(WIKI, "판례DB", "_색인.sqlite")):
        print(f"SKIP — 판례DB 없음: {WIKI}")
        return
    before = stamp()

    r = run("info")
    check("info", r.returncode == 0 and "로컬 판례DB:" in r.stdout, r.stderr)
    r = run("read", "대법원 95다28625", "--max-chars", "500")
    check("인용 표기: 전원합의체·공식 사건번호(2자리 연도)",
          "대법원 1997. 8. 21. 선고 95다28625 전원합의체 판결" in r.stdout, r.stdout[:200])
    r = run("search", "--court", "대법원", "--decision-type", "결정", "--limit", "1")
    check("결정 표기 '.자'", r.returncode == 0 and ".자 " in r.stdout and ". 자 " not in r.stdout, r.stdout[:200])
    r = run("fulltext", '"자주점유의 추정" 타주점유', "--court", "대법원", "--sort", "cited", "--limit", "1")
    check("전문 검색·피인용순", r.returncode == 0 and "95다28625" in r.stdout, r.stdout[:200])
    r = run("fulltext", '상계 "자동채권"', "--court", "대법원", "--limit", "1")
    check("2자 용어 LIKE 보강", r.returncode == 0 and "0건" not in r.stdout, r.stdout[:200] + r.stderr[-200:])
    r = run("citing", "95다28625", "--limit", "2")
    check("인용 관계", r.returncode == 0 and "이 판례를 인용한 보유 판례" in r.stdout and "82다708" in r.stdout)
    r = run("read", "대법원 1999흐1", "--max-chars", "100")
    check("auto-extracted 표시·원본 경로 출력", "auto-extracted(lbox·bigcase 개별 취득분" in r.stdout and "원본: " in r.stdout
          and "URL: https://" in r.stdout, r.stdout[:300])
    r = run("read", "대법원 2021마6542", "--max-chars", "100")
    check("전원합의체 결정 표기", "2025. 7. 24.자 2021마6542 전원합의체 결정" in r.stdout, r.stdout[:200])
    r = run("search", "--decision-type", "재정")
    check("재정 표기 '.자'", "대법원 1979. 12. 7.자 79초70 재정" in r.stdout, r.stdout[:200])
    r = run("search", "--case-no", "2022느합3003")
    check("가사 심판 표기 '.자'", "의정부지방법원 2024. 1. 3.자 2022느합3003 심판" in r.stdout, r.stdout[:200])
    r = run("read", "대법원 2022다302497", "--max-chars", "100")
    check("병합 사건번호(같은 부호는 일련번호만)·원문 표제 출력",
          "대법원 2023. 4. 27. 선고 2022다302497, 302503 판결 |" in r.stdout
          and "원문 표제: 대법원 2023. 4. 27. 선고 2022다302497, 302503 판결" in r.stdout, r.stdout[:300])
    r = run("read", "대법원 2008다7772", "--max-chars", "50")
    check("병합 사건번호(원문 표제 '7772,7789')", "선고 2008다7772, 7789 판결 |" in r.stdout, r.stdout[:200])
    r = run("read", "대법원 1988다1516", "--max-chars", "50")
    check("병합 사건번호(부호가 섞인 경우)", "선고 88다1516, 1523, 88다카10029, 10036 판결 |" in r.stdout, r.stdout[:200])
    r = run("search", "--case-no", "2012전노2", "--court", "광주고등법원", "--limit", "5")
    check("병합 사건번호(부수 사건 전체 번호)", "선고 2012노12, 2012전노2 판결 |" in r.stdout, r.stdout[:200])
    r = run("read", "헌법재판소 2015헌마1177", "--max-chars", "50")
    check("헌법재판소 병합 결정은 주 번호만", "헌법재판소 2016. 4. 28. 선고 2015헌마1177 결정 |" in r.stdout, r.stdout[:200])
    r = run("read", "대법원 95다28625", "--max-chars", "50")
    check("단일 사건번호는 그대로", "선고 95다28625 전원합의체 판결 |" in r.stdout, r.stdout[:200])
    r = run("read", "부산고등법원 1962다16")
    check("법원이 다르면 다른 판례로 바꾸지 않음", r.returncode == 2 and "대구" not in r.stdout, r.stdout[:200] + r.stderr[-200:])
    r = run("fulltext", "")
    check("빈 검색어 → exit 2", r.returncode == 2 and "Traceback" not in r.stderr, r.stderr[-200:])
    r = run("read", "95다28625", "--max-chars", "50", "--root", WIKI)
    check("--root를 하위 명령 뒤에", r.returncode == 0, r.stderr[-200:])
    r = run("read", "대법원 99다99999")
    check("미보유 → exit 2", r.returncode == 2 and "판례DB에 없음" in r.stderr)
    r = run("search", "--case-no", "92다49218")
    check("사건번호 2자리 연도 입력 정규화", r.returncode == 0 and "92다49218" in r.stdout, r.stdout[:200])
    r = run("search", "--law-ref", "민법 750조", "--court", "대법원", "--limit", "1")
    check("참조조문 약식 입력('민법 750조')", r.returncode == 0 and "0건" not in r.stdout, r.stdout[:200])
    r2 = run("search", "--law-ref", "민법750조", "--court", "대법원", "--limit", "1")
    check("참조조문 붙여 쓴 입력('민법750조')", r2.returncode == 0 and r2.stdout == r.stdout, r2.stdout[:200])
    for q in ("상법 제335조의7", "상법 335조의7", "상법 제335.7조"):
        r = run("search", "--law-ref", q)
        check(f"가지조문 표기 혼재({q})", "2018다292975" in r.stdout and "2014다221258" in r.stdout, r.stdout[:200])
    r = run("search", "--keyword", "소멸시효")
    check("--keyword 적재 없음 NOTE", r.returncode == 0 and "keywords" in r.stderr, r.stderr[-200:])
    for q, want in (("대법원 2000. 1. 21. 선고 97다1013", "case_id=대법원 1997다1013"),
                    ("대법원 2000. 1. 21. 선고 97다1013 판결", "case_id=대법원 1997다1013"),
                    ("대법원 2019. 4. 10.자 2017마6337 결정", "case_id=대법원 2017마6337"),
                    ("대법원 2023. 4. 27. 선고 2022다302497, 302503 판결", "case_id=대법원 2022다302497"),
                    ("대법원 1997. 8. 21. 선고 95다28625 판결(공1997하, 2765)", "case_id=대법원 1995다28625"),
                    ("대법원 1997. 8. 21. 선고 95다28625 전원합의체 판결 등 참조", "case_id=대법원 1995다28625")):
        r = run("read", q, "--max-chars", "50")
        check(f"인용 표기 입력({q})", r.returncode == 0 and want in r.stdout, r.stdout[:200] + r.stderr[-200:])
    r = run("read", "대법원 1996. 4. 12. 선고 95다28625", "--max-chars", "10")
    check("인용 표기 선고일 불일치 NOTE", r.returncode == 0 and "입력 선고일 1996-04-12" in r.stderr, r.stderr[-200:])
    r = run("read", "대법원 2023다318857", "--max-chars", "10")
    check("원본 절대경로(수집/ — 판례DB 폴더 기준)", "원본: /" in r.stdout and "(있음)" in r.stdout, r.stdout[:400])
    r = run("read", "대법원 2019다247385", "--max-chars", "10")
    check("원본 official-xml → pdf 없음", "pdf 없음" in r.stdout, r.stdout[:400])
    r = run("fulltext", '"87도84" 변경하기로', "--en-banc")
    check("사건번호 구절이 더 긴 번호('87도840')에 걸리지 않음", r.returncode == 0 and "2016도21314" not in r.stdout,
          r.stdout[:200])
    r = run("fulltext", '"2010도10352" 변경하기로', "--en-banc")
    check("판례 변경 검색 참양성 유지", "2016도21314" in r.stdout, r.stdout[:200])
    r = run("fulltext", '"87도8"', "--limit", "1")
    check("사건번호 거름 뒤 부족하면 --limit 안내", "더 있을 수 있으니" in r.stderr, r.stderr[-200:])
    r = run("citing", "76다2418", "--limit", "2")
    check("미보유 판례의 인용 판례(미보유 표시)",
          r.returncode == 0 and r.stdout.startswith("case_id=대법원 1976다2418 (미보유") and "87다카1129" in r.stdout,
          r.stdout[:300] + r.stderr[-200:])
    r = run("citing", "2099다1")
    check("미보유·인용 없음 citing → exit 2", r.returncode == 2 and "판례DB에 없음" in r.stderr, r.stderr[-200:])
    r = run("info")
    check("info 수집 선고일(원격 비교 기준)", "수집 선고일" in r.stdout, r.stdout[-300:])
    r = run("--root", os.path.basename(WIKI), "info", cwd=os.path.dirname(WIKI))
    check("상대경로 --root", r.returncode == 0, r.stderr[-200:])
    r = run("info", env={**ENV, "WK_LEGAL_WIKI_ROOT": os.path.basename(WIKI)}, cwd=os.path.dirname(WIKI))
    check("상대경로 WK_LEGAL_WIKI_ROOT", r.returncode == 0, r.stderr[-200:])
    with tempfile.TemporaryDirectory() as t:
        r = run("--root", "nope", "info", cwd=t)
        check("상대경로 --root 실패 시 절대경로로 안내", r.returncode == 2 and os.path.join(os.path.realpath(t), "nope")
              in os.path.realpath(r.stderr.split("찾은 곳: ")[-1].split(" (")[0]), r.stderr[-200:])
    r = run("info", env={**ENV, "WK_LEGAL_WIKI_ROOT": "/nonexistent"})
    check("env 지정 경로가 없으면 exit 2(폴백 없음)", r.returncode == 2, r.stderr[-200:])
    with tempfile.TemporaryDirectory() as t:
        os.makedirs(os.path.join(t, "mnt"))
        os.symlink(WIKI, os.path.join(t, "mnt", "LLM-wiki"))
        env = {k: v for k, v in ENV.items() if k != "WK_LEGAL_WIKI_ROOT"}
        env["HOME"] = t
        r = run("info", env=env)
        check("Cowork 마운트 위치(~/mnt/LLM-wiki) 발견", r.returncode == 0 and "로컬 판례DB:" in r.stdout, r.stderr)
    with tempfile.TemporaryDirectory() as t:
        os.makedirs(os.path.join(t, "mnt", "user"))
        os.symlink(WIKI, os.path.join(t, "mnt", "user", "LLM-wiki"))
        env = {k: v for k, v in ENV.items() if k != "WK_LEGAL_WIKI_ROOT"}
        env["HOME"] = t
        r = run("info", env=env)
        check("기본 경로 밖 깊이 3 마운트(~/mnt/user/LLM-wiki) 발견", r.returncode == 0 and "로컬 판례DB:" in r.stdout, r.stderr)
        check("$HOME 탐색으로 찾으면 --root·env 지정 안내", "$HOME 탐색" in r.stderr, r.stderr[-200:])
    with tempfile.TemporaryDirectory() as t:
        r = run("--root", synth_wiki(t), "info")
        check("합성 색인: 수행이 더 늦어도 수집 최대 선고일은 따로",
              "~ 2026-08-20" in r.stdout and "수집 선고일 2025-01-02 ~ 2026-06-15" in r.stdout and "수행 1건" in r.stdout,
              r.stdout[-400:] + r.stderr[-200:])

    check("위키 무변경(색인·도구 폴더·__pycache__)", stamp() == before)

    width = max(len(n) for n, _, _ in RESULTS)
    fails = 0
    for name, ok, detail in RESULTS:
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}" + (f"  — {detail.strip()[:300]}" if not ok and detail else ""))
    print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} 통과")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
