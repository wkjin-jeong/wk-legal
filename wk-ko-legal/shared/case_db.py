#!/usr/bin/env python3
"""로컬 판례DB(LLM-wiki 판례DB/_색인.sqlite) 읽기 전용 조회 — 원격 판례 MCP(llm-wiki)와 같은 다섯 조회.

    python3 case_db.py info                                     # 수록 건수·기간·text_status (MCP collection_info)
    python3 case_db.py search --case-no 92다49218                # 목록 (MCP search_cases) — 사건번호·법원·조문·키워드 등
    python3 case_db.py fulltext '"자주점유의 추정" 타주점유' --court 대법원 --sort cited   # 전문 검색 (MCP fulltext_search)
    python3 case_db.py read '대법원 95다28625' --max-chars 8000   # 원문 (MCP read_case)
    python3 case_db.py citing '대법원 95다28625'                  # 이 판례를 인용한 보유 판례·이 판례가 인용한 판례 (MCP find_citing)

- 위키 루트: --root > WK_LEGAL_WIKI_ROOT > ~/LLM-wiki > ~/mnt/LLM-wiki(Cowork VM). SQLite는 file: URI mode=ro + PRAGMA query_only로 연다.
  위키에 아무것도 쓰지 않는다(색인 재생성·반입 스크립트도 실행하지 않음).
- 인용 표기는 결과의 citation을 그대로 쓴다(판례DB/_인용규약.md §4 — 공식 사건번호로 역변환, 병합 사건번호 포함 — 헌재는 주 번호만). 정규형(1992다…)을
  인용문에 쓰지 않는다. (본소)·(반소)·(병합) 같은 표시는 citation에 없다 — 필요하면 read의 '원문 표제'를 따른다.
- trigram FTS는 3자 미만 용어를 잡지 못한다 — 짧은 용어는 본문 LIKE로 보강한다(느림). 0건을 '보유분 없음'으로 단정하지 말 것.
- 표준 라이브러리만 사용. Python 3.9 이상.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sqlite3
import sys
import unicodedata

sys.dont_write_bytecode = True   # 위키의 정규화 모듈을 불러와도 위키 폴더에 __pycache__를 만들지 않는다


def wiki_root(arg: str | None) -> str:
    """--root > WK_LEGAL_WIKI_ROOT > ~/LLM-wiki > ~/mnt/LLM-wiki(Cowork 등 샌드박스 VM의 마운트 위치)."""
    explicit = arg or os.environ.get("WK_LEGAL_WIKI_ROOT")
    cands = [explicit] if explicit else ["~/LLM-wiki", "~/mnt/LLM-wiki"]
    for c in cands:
        root = os.path.expanduser(c)
        if os.path.isdir(os.path.join(root, "판례DB")):
            return root
    sys.stderr.write(f"ERROR: 판례DB 없음 — 찾은 곳: {', '.join(cands)} (위키 루트는 --root 또는 WK_LEGAL_WIKI_ROOT)\n")
    sys.exit(2)


def _normalizer(root: str):
    """위키의 식별자 정규화 모듈(판례DB/_도구/식별자_정규화.py — 정본)을 쓰고, 없으면 최소 대체 구현."""
    tool_dir = os.path.join(root, "판례DB", "_도구")
    if os.path.isfile(os.path.join(tool_dir, "식별자_정규화.py")):
        sys.path.insert(0, tool_dir)
        try:
            import 식별자_정규화 as n  # type: ignore
            return n.canon_case_id, n.canon_case_num, n.official_case_num, n.canon_court
        except Exception:
            pass
        finally:
            sys.path.remove(tool_dir)

    def canon_case_num(num: str) -> str:
        num = unicodedata.normalize("NFC", num or "").strip()
        m = re.match(r"^(\d{2})([가-힣])", num)
        if m and 62 <= int(m.group(1)) <= 99:
            return "19" + num
        return num

    def official_case_num(num: str) -> str:
        m = re.match(r"^(\d{4})([가-힣])", num or "")
        return num[2:] if m and 1962 <= int(m.group(1)) <= 1999 else num

    abbr = {"고법": "고등법원", "지법": "지방법원", "행법": "행정법원", "가법": "가정법원"}

    def canon_court(c: str) -> str:
        c = re.sub(r"\s+", " ", unicodedata.normalize("NFC", c or "").strip())
        c = re.sub(r"(고법|지법|행법|가법)(?=\s|$)", lambda m: abbr[m.group(1)], c)
        return "헌법재판소" if c == "헌재" else c

    def canon_case_id(s: str) -> str:
        s = re.sub(r"\s+", " ", unicodedata.normalize("NFC", s).strip())
        m = re.match(r"^(.*?)\s*(\d{2,4}[가-힣]+\d+)$", s)
        if not m:
            raise ValueError(s)
        return f"{canon_court(m.group(1) or '대법원')} {canon_case_num(m.group(2))}"

    return canon_case_id, canon_case_num, official_case_num, canon_court


def connect(root: str) -> sqlite3.Connection:
    path = os.path.join(root, "판례DB", "_색인.sqlite")
    if not os.path.isfile(path):
        sys.stderr.write(f"ERROR: 판례DB 색인 없음 — {path} (재생성하지 말고 확인 불가로 처리)\n")
        sys.exit(2)
    con = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


COLS = ("c.case_id, c.court, c.case_no, c.instance, c.decision_type, c.en_banc, c.date, c.case_name, "
        "c.field, c.report, c.published, c.text_status, c.chars, c.file, c.source_raw, c.acquisition, "
        "(SELECT COUNT(*) FROM case_refs r WHERE r.ref_case_id = c.case_id) AS cited, "
        "(SELECT group_concat(n.case_no, '|') FROM case_numbers n WHERE n.case_id = c.case_id) AS nums")
_NUM = re.compile(r"^(\d{4})(\D+)(\d+)$")   # 정규형 사건번호: 연도·사건부호·일련번호


def case_nums(row, official) -> str:
    """주 사건번호 + 병합 사건번호(case_numbers = front-matter 사건번호·병합사건번호). 주 번호 다음에 같은 연도·부호
    번호를 일련번호 오름차순으로, 이어서 다른 부호 번호를 둔다. 앞 번호와 연도·부호가 같으면 일련번호만 적는다
    ('2022다302497, 302503', '2012노12, 2012전노2')."""
    main = row["case_no"]
    rest = [n for n in (row["nums"] or "").split("|") if n and n != main]
    head = _NUM.match(main)

    def key(n: str) -> tuple:
        m = _NUM.match(n)
        if not m:
            return (2, n, 0)
        return (0 if head and m.group(1, 2) == head.group(1, 2) else 1, m.group(1) + m.group(2), int(m.group(3)))

    out, prev = [official(main)], head
    for n in sorted(rest, key=key):
        m = _NUM.match(n)
        out.append(m.group(3) if m and prev and m.group(1, 2) == prev.group(1, 2) else official(n))
        prev = m
    return ", ".join(out)


def citation(row, official) -> str:
    """판례DB/_인용규약.md §4 형식."""
    y, m, d = (row["date"] or "0000-00-00").split("-")
    date = f"{int(y)}. {int(m)}. {int(d)}." if d != "00" else f"{int(y)}."
    kind = row["decision_type"] or "판결"
    court = row["court"]
    if court == "헌법재판소":  # 헌재 병합사건번호는 front-matter에 오기가 있어(2015헌마1177: 2016헌마17 → 2015헌마17) 주 번호만
        return f"{court} {date} 선고 {official(row['case_no'])} 결정"
    num = case_nums(row, official)
    eb = "전원합의체 " if row["en_banc"] else ""
    if kind in ("결정", "명령", "심판", "재정"):  # 가사 심판·재정도 원문 표제가 '…자 …느단… 심판'·'…자 79초70 재정'
        return f"{court} {date}자 {num} {eb}{kind}"
    return f"{court} {date} 선고 {num} {eb}{kind}"


def line(row, official, extra: str = "") -> str:
    flags = [row["text_status"], f"피인용 {row['cited']}"]
    if (row["file"] or "").startswith("수행/"):
        flags.append("수행(실명 원문 — 외부 산출물엔 case_id·법리 요지만)")
    return f"{citation(row, official)} | {row['case_name'] or ''} | {' · '.join(flags)} | case_id={row['case_id']}" + (
        f"\n    {extra}" if extra else "")


def _filters(a) -> tuple[list[str], list]:
    where, args = [], []
    if getattr(a, "court", None):
        where.append("c.court LIKE ?"); args.append(a.normalizer[3](a.court) + "%")
    for col, attr in (("c.field", "field"), ("c.instance", "instance"), ("c.decision_type", "decision_type")):
        if getattr(a, attr, None):
            where.append(f"{col} = ?"); args.append(getattr(a, attr))
    if getattr(a, "en_banc", False):
        where.append("c.en_banc = 1")
    if getattr(a, "date_from", None):
        where.append("c.date >= ?"); args.append(a.date_from)
    if getattr(a, "date_to", None):
        where.append("c.date <= ?"); args.append(a.date_to)
    return where, args


def cmd_info(con, a, official) -> None:
    n, lo, hi = con.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM cases").fetchone()
    st = ", ".join(f"{r[0]} {r[1]}" for r in con.execute("SELECT text_status, COUNT(*) FROM cases GROUP BY 1 ORDER BY 2 DESC"))
    src = ", ".join(f"{r[0]} {r[1]}" for r in con.execute(
        "SELECT substr(file, 1, instr(file, '/') - 1), COUNT(*) FROM cases GROUP BY 1"))
    import datetime
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(a.root, "판례DB", "_색인.sqlite")))
    print(f"로컬 판례DB: {n}건 | 선고일 {lo} ~ {hi} | 색인 갱신 {mt:%Y-%m-%dT%H:%M} | text_status: {st} | 폴더: {src}")
    print("원격 판례 MCP와 비교: 최대 선고일 대 MCP collection_info의 date_max(늦은 쪽 우선), 같으면 색인 갱신 대 loaded_at.")


def cmd_search(con, a, official) -> None:
    where, args = _filters(a)
    if a.case_no:
        canon_num = a.normalizer[1]
        num = canon_num(re.sub(r"\s+", "", a.case_no))
        where.append("c.case_id IN (SELECT case_id FROM case_numbers WHERE case_no = ?)"); args.append(num)
    for col_sql, attr in (("c.case_id IN (SELECT case_id FROM law_refs WHERE ref_text LIKE ?)", "law_ref"),
                          ("c.case_id IN (SELECT case_id FROM keywords WHERE keyword LIKE ?)", "keyword"),
                          ("c.case_name LIKE ?", "case_name")):
        if getattr(a, attr):
            where.append(col_sql); args.append(f"%{getattr(a, attr)}%")
    if not where:
        sys.stderr.write("ERROR: 조건을 하나 이상 주세요(--case-no·--law-ref·--keyword·--case-name·--court 등).\n")
        sys.exit(2)
    rows = con.execute(f"SELECT {COLS} FROM cases c WHERE {' AND '.join(where)} ORDER BY c.date DESC LIMIT ?",
                       (*args, a.limit)).fetchall()
    _print_rows(rows, official, "search")


def _terms(q: str) -> list[str]:
    """공백으로 나눈 용어(AND). 큰따옴표로 묶은 구절은 한 용어."""
    return [quoted or bare for quoted, bare in re.findall(r'"([^"]+)"|(\S+)', q)]


def cmd_fulltext(con, a, official) -> None:
    terms = _terms(a.query)
    if not terms:
        sys.stderr.write("ERROR: 검색어가 비었습니다.\n")
        sys.exit(2)
    long_t = [t for t in terms if len(t) >= 3]
    short_t = [t for t in terms if len(t) < 3]
    where, args = _filters(a)
    sel = COLS
    frm = "cases c"
    order = {"cited": "cited DESC, c.date DESC", "date": "c.date DESC"}.get(a.sort, "")
    if long_t:
        match = " AND ".join('"' + t.replace('"', '""') + '"' for t in long_t)
        frm = "cases_fts f JOIN cases c ON c.case_id = f.case_id"
        where.insert(0, "cases_fts MATCH ?"); args.insert(0, match)
        sel += f", snippet(cases_fts, 1, '[', ']', '…', 64) AS snip, bm25(cases_fts) AS rk"
        order = order or "rk"
    else:
        frm = "cases_fts f JOIN cases c ON c.case_id = f.case_id"
        sel += ", '' AS snip"
        order = order or "c.date DESC"
        sys.stderr.write("NOTE: 3자 이상 용어가 없어 본문 전수 스캔(LIKE)입니다 — 느립니다. 3자 이상 용어를 함께 넣으세요.\n")
    for t in short_t:
        where.append("f.body LIKE ?"); args.append(f"%{t}%")
    rows = con.execute(f"SELECT {sel} FROM {frm} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?",
                       (*args, a.limit)).fetchall()
    if not rows and any(len(t) < 3 for t in terms):
        sys.stderr.write("NOTE: 0건 — 2자 이하 용어가 섞여 있으면 보유분 없음으로 단정하지 말고 용어를 바꿔 재검색하세요.\n")
    _print_rows(rows, official, "fulltext")


def _print_rows(rows, official, kind: str) -> None:
    for r in rows:
        snip = ""
        if kind == "fulltext" and r["snip"]:
            snip = re.sub(r"\s+", " ", r["snip"])
        print(line(r, official, snip))
    if not rows:
        print("0건")


def _resolve(con, a, raw: str) -> sqlite3.Row:
    """case_id(또는 사건번호만)를 한 판례로 확정한다. 법원을 적었으면 그 법원으로만, 생략했으면(대법원 기본)
    대법원에 없을 때 병합사건번호까지 보되 여러 법원에 걸리면 목록을 보이고 exit 2 — 다른 판례로 바꿔치지 않는다."""
    canon_id = a.normalizer[0]
    try:
        cid = canon_id(raw)
    except ValueError:
        sys.stderr.write(f"ERROR: case_id 해석 불가: {raw!r} (예: '대법원 95다28625', '2019다247385')\n")
        sys.exit(2)
    court, num = cid.rsplit(" ", 1)
    court_given = bool(re.match(r"^\s*\D", raw.strip())) and not raw.strip().startswith(num)
    row = con.execute(f"SELECT {COLS} FROM cases c WHERE c.case_id = ?", (cid,)).fetchone()
    if row is not None:
        return row
    sql = f"SELECT {COLS} FROM cases c WHERE c.case_id IN (SELECT case_id FROM case_numbers WHERE case_no = ?)"
    params: list = [num]
    if court_given:
        sql += " AND c.court = ?"; params.append(court)
    rows = con.execute(sql + " LIMIT 10", params).fetchall()
    if len(rows) == 1:
        return rows[0]
    if rows:
        sys.stderr.write(f"ERROR: 사건번호 {num}이(가) 여러 법원에 있습니다 — 법원을 붙여 다시 조회하세요:\n"
                         + "\n".join("  " + line(r, a.normalizer[2]) for r in rows) + "\n")
        sys.exit(2)
    sys.stderr.write(f"ERROR: 판례DB에 없음 — {cid}. 원격 MCP 또는 브라우저 URL 직행으로 확인하세요.\n")
    sys.exit(2)


def cmd_read(con, a, official) -> None:
    row = _resolve(con, a, a.case_id)
    path = os.path.join(a.root, "판례DB", row["file"])
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        sys.stderr.write(f"ERROR: 원문 파일을 열 수 없음 — {path}: {e}\n")
        sys.exit(2)
    fm = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.DOTALL)
    url = re.search(r'^source_url:\s*"?([^"\n]+)"?', fm.group(1), re.M) if fm else None
    text = text[fm.end():].strip() if fm else text.strip()
    title = re.search(r"^# (.+)$", text, re.M)
    a.offset = max(0, a.offset)
    laws = [r[0] for r in con.execute("SELECT ref_text FROM law_refs WHERE case_id = ?", (row["case_id"],))]
    refs = [r[0] for r in con.execute("SELECT ref_case_id FROM case_refs WHERE case_id = ?", (row["case_id"],))]
    part = text[a.offset:a.offset + a.max_chars]
    nxt = a.offset + a.max_chars if a.offset + a.max_chars < len(text) else None
    print(line(row, official))
    print(f"원문 표제: {title.group(1).strip() if title else '-'}")
    if row["text_status"] == "auto-extracted":
        print("참고: auto-extracted(lbox·bigcase 개별 취득분 — 그 사이트 원문과 같으므로 대조 없이 인용, 판례-인용-정책 3.)")
    print(f"참조조문: {', '.join(laws) or '-'}\n참조판례: {', '.join(refs) or '-'}")
    print(f"원본: {row['source_raw'] or '-'} | URL: {url.group(1).strip() if url else '-'}")
    print(f"본문 {a.offset}~{a.offset + len(part)} / {len(text)}자" + (f" (다음: --offset {nxt})" if nxt else ""))
    print(part)


def cmd_citing(con, a, official) -> None:
    row = _resolve(con, a, a.case_id)
    print(line(row, official))
    cited_by = con.execute(f"SELECT {COLS} FROM cases c WHERE c.case_id IN "
                           "(SELECT case_id FROM case_refs WHERE ref_case_id = ?) ORDER BY c.date DESC LIMIT ?",
                           (row["case_id"], a.limit)).fetchall()
    print(f"\n[이 판례를 인용한 보유 판례 — 최신순 {len(cited_by)}건(최대 {a.limit})]")
    for r in cited_by:
        print("  " + line(r, official))
    print("\n[이 판례가 인용한 판례]")
    for (ref,) in con.execute("SELECT ref_case_id FROM case_refs WHERE case_id = ?", (row["case_id"],)):
        held = con.execute(f"SELECT {COLS} FROM cases c WHERE c.case_id = ?", (ref,)).fetchone()
        print("  " + (line(held, official) if held else f"{ref} (미보유)"))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="case_db.py", description="로컬 판례DB 읽기 전용 조회")
    p.add_argument("--root", help="위키 루트 (기본 WK_LEGAL_WIKI_ROOT 또는 ~/LLM-wiki)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--root", default=argparse.SUPPRESS, help="위키 루트(하위 명령 뒤에도 쓸 수 있음)")

    def filters(sp):
        sp.add_argument("--court"); sp.add_argument("--field", choices=["민사", "형사", "행정", "가사", "특허", "헌법"])
        sp.add_argument("--instance", choices=["제1심", "항소심", "상고심", "헌법재판소"])
        sp.add_argument("--decision-type", dest="decision_type", choices=["판결", "결정", "명령", "심판", "재정"])
        sp.add_argument("--en-banc", dest="en_banc", action="store_true", help="전원합의체만")
        sp.add_argument("--date-from", dest="date_from", help="선고일 하한 YYYY-MM-DD")
        sp.add_argument("--date-to", dest="date_to", help="선고일 상한 YYYY-MM-DD")
        sp.add_argument("--limit", type=int, default=20)

    common(sub.add_parser("info", help="수록 건수·기간·text_status"))
    s = sub.add_parser("search", help="목록 — 사건번호·법원·조문·키워드·사건명 (선고일 내림차순)")
    s.add_argument("--case-no", dest="case_no", help="사건번호(92다49218·1992다49218 모두 가능, 병합번호 포함)")
    s.add_argument("--law-ref", dest="law_ref", help="참조조문 부분일치, 예 '민법 제750조'")
    s.add_argument("--keyword"); s.add_argument("--case-name", dest="case_name")
    filters(s); common(s)
    f = sub.add_parser("fulltext", help="본문 전문 검색 — 공백 AND, 큰따옴표 구절")
    f.add_argument("query")
    f.add_argument("--sort", choices=["rank", "cited", "date"], default="rank",
                   help="rank(관련도, 기본) / cited(피인용 수 — 대표 판례 고를 때) / date(최신순)")
    filters(f); common(f)
    r = sub.add_parser("read", help="원문(메타·참조조문·참조판례 포함)")
    r.add_argument("case_id"); r.add_argument("--offset", type=int, default=0)
    r.add_argument("--max-chars", dest="max_chars", type=int, default=30000); common(r)
    c = sub.add_parser("citing", help="이 판례를 인용한 보유 판례(최신순)와 이 판례가 인용한 판례")
    c.add_argument("case_id"); c.add_argument("--limit", type=int, default=30); common(c)

    a = p.parse_args(argv)
    a.root = wiki_root(a.root)
    a.normalizer = _normalizer(a.root)
    con = connect(a.root)
    official = a.normalizer[2]
    {"info": cmd_info, "search": cmd_search, "fulltext": cmd_fulltext,
     "read": cmd_read, "citing": cmd_citing}[a.cmd](con, a, official)


if __name__ == "__main__":
    main()
