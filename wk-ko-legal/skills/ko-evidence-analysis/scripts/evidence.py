#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
증거·기록 분석 보조 CLI (ko-evidence-analysis)
==============================================

쪽 단위 markdown 변환본(pages/page_NNN.md + _index.md)을 읽어 입력 발견·반입·게이트·
작업 색인·증거목록 파싱·증거 단위 배정·전수성 검사·대화 프로필·인용 역검증·수치 합산을 한다.

    python evidence.py discover --root <작업 루트>
    python evidence.py import   --root R --out <분석 폴더> --src <변환 산출 폴더> --dest <변환본 저장소> [--original <원본>]
    python evidence.py gate     --root R [--out A] <record 경로>...
    python evidence.py index    --root R --out A <record 경로>...
    python evidence.py evlist   --root R --out A --record <경로> --list-id L1 [--pages 3-16]
    python evidence.py assign   --out A --list-id L1
    python evidence.py coverage --out A [--list-id L1]
    python evidence.py cardfill --root R --out A [카드.md...] [--resha]
    python evidence.py profile  --root R --record <경로> --pages 120-522 --report <파일>
    python evidence.py verify   --root R --out A --report <파일> <산출물.md>...
    python evidence.py sum      --root R --record <경로> --pages 45-47 --col 지급금액
    python evidence.py locate   --root R --out A [record_id]

- 경로는 모두 작업 루트 기준 상대 경로로 기록한다. 변환본과 원본에는 쓰지 않는다(import의 복사 제외).
- 터미널에는 건수·경로만 출력한다. 기록 내용(인명 등)은 --out/--report 파일에만 쓴다.
- 종료코드: 0 정상, 2 사용 오류, 3 검증 실패. 표준 라이브러리만 사용한다.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from pathlib import Path

KEYS4 = {"pdf_page", "printed_page", "heading", "notes"}
KEYS_OLD = {"record_page", "doc_title", "evidence_ref"}
ALIAS = {"record_page": "printed_page", "doc_title": "heading"}
FIXED_MARKS = {"판독불가", "불명확", "날인", "스탬프", "서명", "무인", "수기", "사진", "도면", "그림",
               "빈 페이지", "강조", "마스킹", "좌", "우", "v", " ", ""}
MASK_VARIANTS = ["가림", "블러", "이름가림", "가려짐", "블라인드", "모자이크", "비식별화", "비실명처리", "블랙마킹"]
PAGE_RE = re.compile(r"^page_(\d{3,4})\.md$")
MARK_RE = re.compile(r"\[([^\[\]\n^]{0,40}?)(?::[^\[\]\n]*)?\](?!\()")
PTAG_RE = re.compile(r"\{\{p:([^}]+)\}\}")


def die(msg: str, code: int = 2) -> None:
    print(f"오류: {msg}", file=sys.stderr)
    sys.exit(code)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def rel(root: Path, p: Path) -> str:
    return nfc(os.path.relpath(p.resolve(), root.resolve()))


def same_or_nested(a: Path, b: Path) -> bool:
    """두 경로가 같거나 한쪽이 다른 쪽 안에 있는지(NFC·심볼릭 링크 무관)."""
    if a.exists() and b.exists() and os.path.samefile(a, b):
        return True
    x, y = nfc(str(a.resolve())), nfc(str(b.resolve()))
    return x == y or x.startswith(y + os.sep) or y.startswith(x + os.sep)


# ── front-matter·페이지 ────────────────────────────────────────────────

def parse_value(v: str):
    v = v.strip()
    if v in ("", "null", "~"):
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].replace('\\"', '"')
    return v


def read_page(path: Path):
    """(front-matter dict 또는 None, 본문)"""
    text = path.read_text(encoding="utf-8-sig")  # BOM이 붙은 쪽 파일도 front-matter로 읽는다
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    fm, end = {}, None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
        m = re.match(r"^([A-Za-z_][\w]*):(.*)$", line)
        if m:
            fm[m.group(1)] = parse_value(m.group(2))
    if end is None:
        return None, text
    return fm, "\n".join(lines[end + 1:])


def page_files(rec: Path):
    out = []
    pdir = rec / "pages"
    if pdir.is_dir():
        for e in os.scandir(pdir):
            m = PAGE_RE.match(e.name)
            if m:
                out.append((int(m.group(1)), Path(e.path)))
    return sorted(out)


def count_marks(body: str) -> int:
    return body.count("[판독불가") + body.count("[불명확")


def right_page(notes) -> int | None:
    m = re.search(r"우측\s*면수\s*(\d+)", notes or "")
    return int(m.group(1)) if m else None


def parse_index_md(path: Path) -> dict:
    info = {"총쪽수": None, "변환": None, "누락": None, "표식": None, "rows": []}
    for line in path.read_text(encoding="utf-8-sig").split("\n"):
        if m := re.match(r"^-\s*원본:.*\((\d+)쪽\)", line):
            info["총쪽수"] = int(m.group(1))
        elif m := re.match(r"^-\s*변환:\s*(\d+)쪽\s*/\s*누락:\s*(.+)$", line):
            info["변환"], info["누락"] = int(m.group(1)), m.group(2).strip()
        elif m := re.match(r"^-\s*판독불가·불명확 표식:\s*(\d+)개", line):
            info["표식"] = int(m.group(1))
        elif line.startswith("|"):
            c = split_row(line)
            if len(c) >= 6 and c[0].isdigit() and c[1].isdigit():
                info["rows"].append({"시작pdf": int(c[0]), "끝pdf": int(c[1]), "인쇄면": c[2],
                                     "제목": c[3], "비고": c[5]})
    return info


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def tables(body: str) -> list[tuple[list[str], list[list[str]]]]:
    """본문의 GFM 표를 (머리글, 행들)로."""
    out, lines, i = [], body.split("\n"), 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith("|") and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]):
            head, rows, i = split_row(lines[i]), [], i + 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            out.append((head, rows))
        else:
            i += 1
    return out


def parse_range(s: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", s.strip())
    if not m:
        die(f"쪽 범위 형식 오류: {s} (예: 12 또는 12-40)")
    a = int(m.group(1))
    return a, int(m.group(2) or a)


# ── 매니페스트 ─────────────────────────────────────────────────────────

def work(out: Path) -> Path:
    return out / "_작업"


def load_manifest(out: Path) -> dict:
    p = work(out) / "사건세트.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"version": 1, "사건": {"법원": None, "사건번호": None, "분야": None, "원사건": None,
                                  "확보경로": None, "제출당사자": None},
            "변환본_저장소": None, "records": [], "증거목록": []}


def save_manifest(out: Path, m: dict) -> None:
    write_atomic(work(out) / "사건세트.json", json.dumps(m, ensure_ascii=False, indent=2) + "\n")


def manifest_record(m: dict, rid: str) -> dict | None:
    return next((r for r in m["records"] if r["record_id"] == rid), None)


def upsert_record(m: dict, rid: str, path: str, **attrs) -> dict:
    r = manifest_record(m, rid)
    if r is None:
        r = {"record_id": rid, "경로": path, "분야": None, "기록종류": None, "분류": None, "증거번호": None,
             "증거번호_상태": "미배정", "printed_page_뜻": None, "변환모델": None, "권": None,
             "세대": None, "등급": None, "원본": None, "사본": [], "반입": None}
        m["records"].append(r)
    r["경로"] = path
    r.update({k: v for k, v in attrs.items() if v is not None})
    return r


def record_id(root: Path, rec: Path, m: dict) -> str:
    path = rel(root, rec)
    for r in m["records"]:
        if r["경로"] == path:
            return r["record_id"]
    rid = nfc(rec.name)
    if manifest_record(m, rid):  # stem 충돌 → 묶음/stem
        rid = nfc(f"{rec.parent.name}/{rec.name}")
    return rid


def read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").split("\n") if x.strip()]


def write_jsonl(p: Path, rows: list[dict]) -> None:
    write_atomic(p, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


# ── discover ───────────────────────────────────────────────────────────

def cmd_discover(a) -> int:
    root = Path(a.root)
    skip = {str((root / x).resolve()) for x in (a.exclude or [])}
    if a.out:
        skip.add(str(Path(a.out).resolve()))
    found, other, originals = [], [], []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x != "_work"
                   and str((Path(d) / x).resolve()) not in skip]
        originals += [Path(d) / f for f in files if f.lower().endswith(".pdf")]
        if "pages" in dirs:
            names = [e.name for e in os.scandir(Path(d) / "pages")]
            done = "_index.md" in files and any(PAGE_RE.match(n) for n in names)
            (found if done else other).append(d)  # pages/만 있고 _index.md가 없거나 쪽이 없으면 변환 미완성
            dirs.remove("pages")
    for d in sorted(found):
        print(json.dumps({"record": rel(root, Path(d)), "서명": "쪽 단위 변환본(세대·규격은 gate가 판정)"}, ensure_ascii=False))
    for d in sorted(other):
        print(json.dumps({"record": rel(root, Path(d)), "서명": "_index.md 없음(변환 미완성 또는 규격 밖) — 입력으로 잡지 않음"},
                         ensure_ascii=False))
    stems = {nfc(Path(d).name) for d in found + other}
    bare = [p for p in originals if nfc(p.stem).replace(" ", "_") not in stems]
    for p in sorted(bare):
        print(json.dumps({"원본": rel(root, p), "서명": "변환본 없음"}, ensure_ascii=False))
    print(f"발견 {len(found)}건, 미완성·규격 밖 {len(other)}건, 변환본 없는 PDF {len(bare)}건", file=sys.stderr)
    return 0


# ── gate ───────────────────────────────────────────────────────────────

def gate_record(root: Path, rec: Path) -> dict:
    err, warn = [], []
    rp = rel(root, rec)
    if nfc(rec.name) != rec.name:
        warn.append("폴더명이 NFC가 아님")
    pages = page_files(rec)
    if not pages:
        return {"record": rp, "세대": "쪽 파일 없음(증거 단위 md 등 구세대)", "등급": None,
                "오류": ["pages/page_NNN.md 없음"], "경고": warn}
    nums = [n for n, _ in pages]
    if nums != list(range(1, len(nums) + 1)):
        err.append(f"쪽 번호 불연속(1~{nums[-1]} 중 {len(nums)}개)")
    gen, marks, odd, latest = None, 0, {}, 0.0
    for n, p in pages:
        if len(str(p.resolve()).encode("utf-8")) > 1024:
            warn.append(f"경로 1,024바이트 초과: page {n}")
        latest = max(latest, p.stat().st_mtime)
        fm, body = read_page(p)
        if fm is None:
            g = "규격 밖(front-matter 없음)"
        elif set(fm) == KEYS4:
            g = "현행"
        elif set(fm) & KEYS_OLD:
            g = "구 규격(12키)"
        else:
            g = "현행"
            err.append(f"page {n}: front-matter 키 {sorted(set(fm) ^ KEYS4)} 불일치")
        gen = g if gen in (None, g) else "혼재"
        if fm:
            pdf = fm.get("pdf_page")
            if pdf != n:
                err.append(f"page {n}: pdf_page={pdf}")
            pp = fm.get("printed_page", fm.get("record_page"))
            if pp is not None and not isinstance(pp, int):
                err.append(f"page {n}: printed_page가 정수·null이 아님")
        marks += count_marks(body)
        for m in MARK_RE.finditer(body):
            head = m.group(1).strip()
            if head not in FIXED_MARKS and not re.fullmatch(r"[\d\s.,~-]+", head) and \
                    (any(v in head for v in MASK_VARIANTS) or head.startswith(("판독", "불명", "식별"))):
                # 터미널에는 표식 종류만 — 대괄호 안의 나머지(가린 인명·번호일 수 있음)는 내보내지 않는다
                kind = next((v for v in sorted(MASK_VARIANTS, key=len, reverse=True) if v in head), head[:2])
                odd[kind] = odd.get(kind, 0) + 1
    if gen == "혼재":
        err.append("front-matter 세대 혼재")
    elif gen == "규격 밖(front-matter 없음)":
        err.append("front-matter 없음 — 규격 밖 변환본은 입력으로 잡지 않는다(어댑터로 규격 형태로 만든 뒤 B등급)")
    idx = rec / "_index.md"
    if not idx.exists():
        err.append("_index.md 없음")
    else:
        info = parse_index_md(idx)
        if info["총쪽수"] is not None and info["총쪽수"] != len(pages):
            err.append(f"pages {len(pages)}개 ≠ 원본 총쪽수 {info['총쪽수']}")
        if info["변환"] is not None and info["변환"] != info["총쪽수"]:
            err.append(f"변환 {info['변환']}쪽 ≠ 총쪽수 {info['총쪽수']}")
        if info["누락"] not in (None, "없음"):
            err.append(f"누락: {info['누락']}")
        if idx.stat().st_mtime < latest:
            warn.append("_index.md가 pages보다 오래됨 — 하류가 재구성한 구간표를 쓴다")
        if info["표식"] is not None and info["표식"] != marks:
            warn.append(f"표식 개수 차이: _index.md {info['표식']} / 재집계 {marks}")
    if odd:
        warn.append("고정 표식 밖 표기(종류×건수): " + ", ".join(f"[{k}…]×{v}" for k, v in sorted(odd.items())))
    grade = {"현행": "A", "구 규격(12키)": "A′"}.get(gen) if not err else None
    return {"record": rp, "세대": gen, "등급": grade, "쪽수": len(pages), "표식": marks, "오류": err, "경고": warn}


def cmd_gate(a) -> int:
    root = Path(a.root)
    results = [gate_record(root, Path(r) if Path(r).is_absolute() else root / r) for r in a.records]
    extra = []
    if a.out and Path(a.out).is_dir():
        out = Path(a.out)
        for d, _, files in os.walk(out):
            extra += [f"동기화 충돌 사본: {rel(root, Path(d) / f)}" for f in files
                      if re.search(r"(충돌|conflicted copy|conflict)", f, re.I)]
        for r in load_manifest(out)["records"]:
            o = r.get("원본")
            if not o:
                extra.append(f"원본 미보유: {r['record_id']}")
            elif not (root / o["경로"]).exists():
                extra.append(f"원본 경로 없음(locate 필요): {r['record_id']}")
    print(json.dumps({"records": results, "분석폴더": extra}, ensure_ascii=False, indent=2))
    return 3 if any(r["오류"] for r in results) else 0


# ── index ──────────────────────────────────────────────────────────────

def norm_heading(h) -> str:
    h = re.sub(r"\((?:계속|이어서|앞면|뒷면)\)", "", str(h or ""))
    h = re.sub(r"[-–]\s*\d+\s*[-–]|\(\s*\d+\s*/\s*\d+\s*\)|\d+\s*쪽$", "", h)
    return re.sub(r"[\s①-⑳]+", "", h)


def build_segments(rows: list[dict]) -> list[dict]:
    segs = []
    for r in rows:
        key = norm_heading(r["heading"])
        if segs and segs[-1]["_key"] == key:
            s = segs[-1]
            s["끝pdf"] = r["pdf_page"]
            if r["heading"] not in s["제목들"]:
                s["제목들"].append(r["heading"])
        else:
            segs.append({"record_id": r["record_id"], "시작pdf": r["pdf_page"], "끝pdf": r["pdf_page"],
                         "제목": r["heading"], "제목들": [r["heading"]], "_key": key, "면": []})
        if r["printed_page"] is not None:
            segs[-1]["면"].append(r["printed_page"])
    for s in segs:
        s["인쇄면"] = [min(s["면"]), max(s["면"])] if s["면"] else None
        s["제목흔들림"] = len(s["제목들"]) > 1
        del s["_key"], s["면"]
    return segs


STEM_RE = re.compile(r"^(?P<사건번호>\d{2,4}[가-힣]{1,3}\d+)_(?P<제출일>\d{4}\.\d{2}\.\d{2})_(?P<서면>[^_]+)_")
EXH_RE = re.compile(r"(?<![가-힣])(소?[갑을병정])\s*(?:제\s*)?(\d+)(?:호증)?(?:\s*(?:-|의)\s*(\d+))?(?=[)_\s]|$)")


def stem_meta(stem: str) -> dict:
    """전자소송 파일명 모양의 stem에서 사건번호·제출일·서면·호증 라벨·제출자를 읽는다(초안 — 사용자 확인 대상)."""
    meta = {}
    if m := STEM_RE.match(stem):
        meta.update(m.groupdict())
        meta["제출일"] = meta["제출일"].replace(".", "-")
    if m := EXH_RE.search(stem):
        meta["호증"] = m.group(1) + m.group(2) + (f"-{m.group(3)}" if m.group(3) else "")
    if m := re.search(r"_((?:원고|피고|신청인|피신청인|채권자|채무자|청구인|피청구인)[^()]*)$", stem):
        meta["제출자"] = m.group(1).replace("_", " ")
    return meta


def sibling_original(root: Path, rec: Path) -> dict | None:
    """변환본 옆의 원본(파일명의 공백을 _로 바꾸면 폴더명과 같은 파일). 내용은 읽지 않는다."""
    for e in os.scandir(rec.parent):
        if e.is_file() and nfc(Path(e.name).stem).replace(" ", "_") == nfc(rec.name):
            return {"경로": rel(root, Path(e.path)), "크기": e.stat().st_size, "sha256": None}
    return None


def cmd_index(a) -> int:
    root, out = Path(a.root), Path(a.out)
    m = load_manifest(out)
    idx_path, seg_path = work(out) / "색인.jsonl", work(out) / "구간.jsonl"
    all_rows, all_segs = read_jsonl(idx_path), read_jsonl(seg_path)
    for r in a.records:
        rec = Path(r) if Path(r).is_absolute() else root / r
        g = gate_record(root, rec)
        if g["오류"]:
            die(f"{g['record']}: gate 실패 — {g['오류'][0]}", 3)
        rid = record_id(root, rec, m)
        rows = []
        for n, p in page_files(rec):
            fm, body = read_page(p)
            fm = {ALIAS.get(k, k): v for k, v in (fm or {}).items()}
            rows.append({"record_id": rid, "pdf_page": n, "printed_page": fm.get("printed_page"),
                         "우측면": right_page(fm.get("notes")), "heading": fm.get("heading"),
                         "notes": fm.get("notes"), "sha256": hashlib.sha256(body.encode()).hexdigest(),
                         "표식": count_marks(body), "마스킹": body.count("[마스킹]"), "글자수": len(body)})
        segs = build_segments(rows)
        # printed_page의 뜻: 구간 경계에서 1 근처로 되돌아가면 문서 쪽수
        firsts = [s["인쇄면"][0] for s in segs if s["인쇄면"]]
        meaning = None
        if len(firsts) >= 2:
            resets = sum(1 for x, y in zip(firsts, firsts[1:]) if y <= x)
            meaning = "문서 쪽수" if resets >= max(1, (len(firsts) - 1) // 2) else "연속 증가 — 기록 면수 또는 단일 문서 쪽수"
        elif len(segs) == 1 and firsts:
            meaning = "문서 쪽수"
        # _index.md 구간표 대조·비고 칸
        info = parse_index_md(rec / "_index.md")
        notes = {(x["시작pdf"], x["끝pdf"]): x["비고"] for x in info["rows"] if x["비고"]}
        for s in segs:
            s["비고"] = next((v for (st, en), v in notes.items() if st <= s["시작pdf"] <= en), None)
        mine = {(s["시작pdf"], s["끝pdf"]) for s in segs}
        theirs = {(x["시작pdf"], x["끝pdf"]) for x in info["rows"]}
        all_rows = [x for x in all_rows if x["record_id"] != rid] + rows
        all_segs = [x for x in all_segs if x["record_id"] != rid] + segs
        upsert_record(m, rid, rel(root, rec), 세대=g["세대"], 등급=g["등급"])
        mr = manifest_record(m, rid)
        if mr.get("printed_page_뜻") is None:
            mr["printed_page_뜻"] = (meaning + "(추정)") if meaning else None
        meta = stem_meta(Path(rid).name)
        mr.setdefault("파일명메타", meta)
        if mr.get("증거번호") is None and meta.get("호증"):
            mr["증거번호"], mr["증거번호_상태"] = meta["호증"], "추정(파일명)"
            first = read_page(page_files(rec)[0][1])[1]
            marks = [(m.group(1) + m.group(2), m.group(3)) for m in
                     re.finditer(r"(소?[갑을병정])\s*제?\s*(\d+)\s*호증(?:\s*(?:의|/|-)?\s*(\d+))?", first)]
            seen = {a + (f"-{b}" if b else "") for a, b in marks}
            if meta["호증"] in seen:
                mr["증거번호_상태"] = "추정(파일명·본문 호증 표시 일치)"
            elif meta["호증"].split("-")[0] in {a for a, _ in marks}:
                mr["증거번호_상태"] = "추정(파일명·본문 주번호 일치)"
            elif marks:
                mr["증거번호_상태"] = "추정(파일명 — 본문 호증 표시와 다름)"
        if mr.get("원본") is None:
            mr["원본"] = sibling_original(root, rec)
        print(json.dumps({"record_id": rid, "쪽수": len(rows), "글자수": sum(x["글자수"] for x in rows),
                          "구간": len(segs), "구간표_불일치": len(mine ^ theirs),
                          "면수없는쪽": sum(1 for x in rows if x["printed_page"] is None),
                          "표식": sum(x["표식"] for x in rows), "printed_page_뜻": mr["printed_page_뜻"],
                          "비고기재": len(notes), "증거번호": mr.get("증거번호"),
                          "원본": bool(mr.get("원본"))}, ensure_ascii=False))
    seen, dup = {}, set()
    for x in all_rows:  # 서로 다른 record에 본문이 같은 쪽 — 같은 원본의 중복 제출 또는 변환 중복
        if x["글자수"] >= 200 and seen.setdefault(x["sha256"], x["record_id"]) != x["record_id"]:
            dup.add(tuple(sorted((seen[x["sha256"]], x["record_id"]))))
    if dup:
        print(json.dumps({"본문이_같은_쪽이_있는_record쌍": [list(d) for d in sorted(dup)]}, ensure_ascii=False))
    write_jsonl(idx_path, all_rows)
    write_jsonl(seg_path, all_segs)
    save_manifest(out, m)
    print(json.dumps({"색인_전체": {"record": len({x["record_id"] for x in all_rows}), "쪽": len(all_rows),
                                  "글자수": sum(x["글자수"] for x in all_rows)}}, ensure_ascii=False))  # 약식 1층 판정용
    return 0


# ── import · locate ────────────────────────────────────────────────────

def cmd_import(a) -> int:
    root, out, src = Path(a.root), Path(a.out), Path(a.src)
    if not (src / "pages").is_dir() or not (src / "_index.md").exists():
        die(f"{src}: pages/와 _index.md를 가진 변환 산출 폴더가 아님")
    m = load_manifest(out)
    dest_base = a.dest or m.get("변환본_저장소")
    if not dest_base:
        die("변환본 저장소가 정해지지 않음 — --dest 지정(작업 폴더 지침에 없으면 '변환본' 제안)")
    orig = None
    if a.original:
        op = Path(a.original).resolve()
        if root.resolve() not in op.parents:
            die("원본이 작업 루트 밖에 있음 — 원본을 작업 루트에 함께 넣은 뒤 다시 지정")
        orig = {"경로": rel(root, op), "크기": op.stat().st_size, "sha256": sha256_file(op)}
    stem = nfc(src.name)
    dest = root / dest_base / (nfc(a.bundle) if a.bundle else "") / stem
    rid = nfc(f"{a.bundle}/{stem}") if a.bundle else stem
    if same_or_nested(src, dest):  # 제자리 --replace는 복사 전에 원천을 지운다
        die(f"{rel(root, src)}: 변환 산출 폴더가 반입 대상과 같거나 서로 포함됨 — 이미 저장소에 있는 변환본은 "
            "import하지 않고 gate → index로 등록한다")
    if dest.exists():
        prev = manifest_record(m, rid)
        same = prev and orig and prev.get("원본") and prev["원본"]["sha256"] == orig["sha256"]
        if not a.replace:
            die(f"{rel(root, dest)} 이미 있음 — " + ("재반입이면 --replace" if same else
                "같은 stem의 다른 문서면 --bundle <묶음>, 재반입이면 --replace"))
    tmp = dest.with_name(f".{dest.name}.반입중")  # 복사가 끝난 뒤에만 기존 폴더를 바꾼다(점 폴더라 discover가 건너뜀)
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("_work", ".*"))
    if dest.exists():
        shutil.rmtree(dest)
    os.replace(tmp, dest)
    g = gate_record(root, dest)
    upsert_record(m, rid, rel(root, dest), 세대=g["세대"], 등급=g["등급"], 분류=a.category, 변환모델=a.model,
                  원본=orig, 반입={"출처": str(src), "반입일": datetime.date.today().isoformat()})
    m["변환본_저장소"] = m.get("변환본_저장소") or dest_base
    save_manifest(out, m)
    print(json.dumps(g, ensure_ascii=False, indent=2))
    if not orig:
        print("경고: 원본 미지정 — '원본 미보유'로 기록됨", file=sys.stderr)
    return 3 if g["오류"] else 0


def cmd_locate(a) -> int:
    root, out = Path(a.root), Path(a.out)
    m = load_manifest(out)
    targets = [r for r in m["records"] if (not a.record_id or r["record_id"] == a.record_id)
               and r.get("원본") and not (root / r["원본"]["경로"]).exists()]
    if not targets:
        print("재탐색 대상 없음")
        return 0
    cands = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("pages", "_work", "_작업")]
        cands += [Path(d) / f for f in files if f.lower().endswith((".pdf", ".tif", ".tiff", ".jpg", ".png"))]
    missing = 0
    for r in targets:
        o, stem = r["원본"], Path(r["record_id"]).name
        ordered = sorted(cands, key=lambda p: nfc(p.stem).replace(" ", "_") != stem)  # stem 일치 우선
        hit = next((p for p in ordered if p.stat().st_size == o["크기"] and (
            sha256_file(p) == o["sha256"] if o.get("sha256") else nfc(p.stem).replace(" ", "_") == stem)), None)
        if hit:
            o["경로"] = rel(root, hit)
        else:
            missing += 1
        print(json.dumps({"record_id": r["record_id"], "원본": o["경로"] if hit else None}, ensure_ascii=False))
    save_manifest(out, m)
    return 3 if missing else 0


# ── evlist ─────────────────────────────────────────────────────────────

VOL_RE = re.compile(r"^(본권|제\s*\d+\s*권|별권\s*\d*)\s*(.*)$")
DITTO = {"〃", "″", "\"", "”", "상동", "동"}


def norm_vol(v) -> str | None:
    """권 식별자 비교용 — 공백을 없앤다('별권 5' = '별권5', '제 3 권' = '제3권')."""
    return re.sub(r"\s+", "", nfc(str(v))) if v else None


def find_col(head: list[str], *alts: str, skip: set[int] = frozenset()) -> int | None:
    """머리글 부분일치. alts는 우선순위 순의 후보이고, 후보 안의 '+'는 모두 포함을 뜻한다."""
    for alt in alts:
        for i, h in enumerate(head):
            hh = re.sub(r"\s+", "", h)
            if i not in skip and all(n in hh for n in alt.split("+")):
                return i
    return None


def cmd_evlist(a) -> int:
    root, out = Path(a.root), Path(a.out)
    rec = Path(a.record) if Path(a.record).is_absolute() else root / a.record
    m = load_manifest(out)
    rid = record_id(root, rec, m)
    lo, hi = parse_range(a.pages) if a.pages else (None, None)
    rows, vol, last, skipped = [], "본권", {}, 0
    for n, p in page_files(rec):
        fm, body = read_page(p)
        head_ok = "증거목록" in re.sub(r"\s+", "", str((fm or {}).get("heading") or ""))
        if (lo is not None and not lo <= n <= hi) or (lo is None and not head_ok):
            continue
        for head, trs in tables(body):
            c_no = find_col(head, "순번")
            c_pg = find_col(head, "쪽수+(수)", "쪽수")
            if c_no is None or c_pg is None or find_col(head, "쪽수+(공)") is not None:
                skipped += 1  # 증인 등 목록·압수물총목록은 별도 표
                continue
            cols = {"쪽수_증": find_col(head, "쪽수+(증)", skip={c_pg}),
                    "증거명칭": find_col(head, "증거명칭", "증거방법", skip={c_pg}), "작성": find_col(head, "작성"),
                    "성명": find_col(head, "성명"), "참조": find_col(head, "참조"), "비고": find_col(head, "비고"),
                    "증거의견": find_col(head, "증거의견+내용", "증거의견"),
                    "증거결정": find_col(head, "증거결정+내용", "증거결정")}
            for tr in trs:
                tr = tr + [""] * (len(head) - len(tr))
                row = {"목록ID": a.list_id, "record_id": rid, "출처pdf": n, "채움": []}
                for k, i in cols.items():
                    v = tr[i] if i is not None else ""
                    if v in DITTO and k in last:
                        v = last[k]
                        row["채움"].append(k)
                    row[k] = v
                no, pg = tr[c_no], tr[c_pg]
                row["순번표기"] = no
                row["괄호순번"] = bool(re.fullmatch(r"\(\s*\d+\s*\)", no))
                nm = re.search(r"(\d+)(?:\s*(?:-|의)\s*(\d+))?", no)  # '12-1'은 순번 12의 가지 1
                row["순번"] = int(nm.group(1)) if nm else None
                row["순번가지"] = int(nm.group(2)) if nm and nm.group(2) else None
                row["쪽수원문"], row["가지"], row["권전체"], row["공판기록"] = pg, None, False, False
                if vm := VOL_RE.match(pg):
                    vol, pg = norm_vol(vm.group(1)), vm.group(2).strip()
                    row["권전체"] = not pg and row["순번"] is not None
                if km := re.match(r"^공\s*(\d+)", pg):
                    row["공판기록"], pg = True, km.group(1)
                row["권"] = "공판기록" if row["공판기록"] else vol
                st = en = None
                if rm := re.fullmatch(r"(\d+)\s*(?:[~∼〜]|내지)\s*(\d+)", pg):
                    st, en = int(rm.group(1)), int(rm.group(2))
                elif bm := re.fullmatch(r"(\d+)\s*-\s*(\d+)", pg):
                    st, row["가지"] = int(bm.group(1)), int(bm.group(2))
                elif re.fullmatch(r"\d+", pg):
                    st = int(pg)
                row["시작면"], row["끝면"], row["끝면_기재"] = st, en, en is not None
                if row["순번"] is None and st is None and not any(row[k] for k in cols):
                    continue  # 권 전환만 있는 줄 — ditto 기준값(last)을 덮어쓰지 않는다
                rows.append(row)
                last.update({k: row[k] for k in cols})
    # 끝면: 같은 권의 다음 행 시작면 − 1
    for v in {r["권"] for r in rows}:
        rs = [r for r in rows if r["권"] == v and r["시작면"] is not None and not r["공판기록"]]
        for cur, nxt in zip(rs, rs[1:]):
            if cur["끝면"] is None:
                cur["끝면"] = max(cur["시작면"], nxt["시작면"] - 1) if nxt["시작면"] > cur["시작면"] else cur["시작면"]
    write_jsonl(work(out) / f"증거목록_{a.list_id}.jsonl", rows)
    lists = [x for x in m["증거목록"] if x["목록ID"] != a.list_id]
    lists.append({"목록ID": a.list_id, "record_id": rid, "커버권": sorted({r["권"] for r in rows}), "승계": a.succeeds})
    m["증거목록"] = lists
    save_manifest(out, m)
    print(json.dumps({"목록ID": a.list_id, "행": len(rows), "권": sorted({r["권"] for r in rows}),
                      "쪽수공란": sum(1 for r in rows if r["시작면"] is None),
                      "괄호순번": sum(r["괄호순번"] for r in rows), "가지순번": sum(r["순번가지"] is not None for r in rows),
                      "채움행": sum(bool(r["채움"]) for r in rows),
                      "의견기재": sum(bool(r["증거의견"]) for r in rows), "제외표": skipped}, ensure_ascii=False))
    return 0 if rows else 3


# ── assign · coverage ──────────────────────────────────────────────────

def pages_by_volume(out: Path) -> dict[str, list[dict]]:
    m = load_manifest(out)
    vol = {r["record_id"]: norm_vol(r.get("권")) for r in m["records"]
           if r.get("권") and str(r.get("printed_page_뜻") or "").startswith("기록 면수")}
    by = {}
    for row in read_jsonl(work(out) / "색인.jsonl"):
        if row["record_id"] in vol:
            by.setdefault(vol[row["record_id"]], []).append(row)
    return by


def cmd_assign(a) -> int:
    out = Path(a.out)
    ev = [r for r in read_jsonl(work(out) / f"증거목록_{a.list_id}.jsonl")]
    by = pages_by_volume(out)
    if not ev or not by:
        die("증거목록 또는 대상 record 없음 — evlist 실행, 매니페스트 records[]에 권과 printed_page_뜻('기록 면수') 기재 필요", 3)
    listed = {norm_vol(r["권"]) for r in ev}
    orphan = sorted(v for v in by if v not in listed)  # 매니페스트의 권이 목록 어디에도 없으면 그 권은 전부 미배정이 된다
    result = []
    for v, pages in by.items():
        rows = sorted([r for r in ev if norm_vol(r["권"]) == v and r["시작면"] is not None],
                      key=lambda r: (r["시작면"], r["가지"] or 0))
        whole = next((r for r in ev if norm_vol(r["권"]) == v and r["권전체"]), None)
        starts = {p["printed_page"] for p in pages}
        cur = []
        for p in pages:
            pp, hit = p["printed_page"], None
            if whole:
                hit, state = whole, "확정"
            elif pp is not None:
                hit = next((r for r in reversed(rows) if r["시작면"] <= pp and r["가지"] is None), None)
                state = "확정" if hit and hit["시작면"] in starts else "추정"  # 가지 면수 행은 쪽으로 배정하지 않는다
            cur.append({"record_id": p["record_id"], "pdf_page": p["pdf_page"], "printed_page": pp, "권": v,
                        "목록ID": a.list_id, "순번": hit["순번"] if hit else None,
                        "순번가지": hit.get("순번가지") if hit else None, "상태": state if hit else "미배정"})
        # 면수 없는 쪽: 앞뒤 구간으로 귀속(추정)
        for i, c in enumerate(cur):
            if c["printed_page"] is None and not whole:
                prev = next((x for x in reversed(cur[:i]) if x["printed_page"] is not None), None)
                nxt = next((x for x in cur[i + 1:] if x["printed_page"] is not None), None)
                if prev and prev["순번"] is not None:
                    c["순번"], c["순번가지"], c["상태"] = prev["순번"], prev["순번가지"], "추정"
                    c["귀속"] = [prev["printed_page"], nxt["printed_page"] if nxt else None]
                    c["경계걸침"] = bool(nxt and nxt["순번"] != prev["순번"])
        result += cur
    write_jsonl(work(out) / f"배정_{a.list_id}.jsonl", result)
    cnt = {}
    for c in result:
        cnt[c["상태"]] = cnt.get(c["상태"], 0) + 1
    print(json.dumps({"목록ID": a.list_id, "쪽": len(result), **cnt,
                      **({"권_불일치": {"매니페스트에만": orphan, "목록의 권": sorted(x for x in listed if x)}} if orphan else {})},
                     ensure_ascii=False))
    return 3 if orphan else 0


def card_ranges(out: Path) -> dict[str, list[tuple[int, int]]]:
    base, got = work(out) / "카드", {}
    if base.is_dir():
        for d, _, files in os.walk(base):
            for f in files:
                if mm := re.fullmatch(r"(\d+)-(\d+)\.md", f):
                    got.setdefault(nfc(os.path.relpath(d, base)), []).append((int(mm.group(1)), int(mm.group(2))))
    return got


def cmd_coverage(a) -> int:
    out = Path(a.out)
    index, cards, report, bad = read_jsonl(work(out) / "색인.jsonl"), card_ranges(out), {"기록→카드": [], "목록→기록": []}, 0
    # 1) 기록 → 카드: 카드 sha 대조는 카드 front-matter의 출처sha256(구간 쪽 sha를 이은 것의 sha256)
    for rid in sorted({r["record_id"] for r in index}):
        rows = [r for r in index if r["record_id"] == rid]
        miss = [r["pdf_page"] for r in rows if not any(s <= r["pdf_page"] <= e for s, e in cards.get(rid, []))]
        stale = []
        for s, e in cards.get(rid, []):
            text = (work(out) / "카드" / rid / f"{s}-{e}.md").read_text(encoding="utf-8")
            if mm := re.search(r"^출처sha256:\s*\"?([0-9a-f]{64})", text, re.M):
                if mm.group(1) != range_sha(rows, s, e):
                    stale.append(f"{s}-{e}")
        bad += len(miss) + len(stale)
        report["기록→카드"].append({"record_id": rid, "쪽수": len(rows), "카드": len(cards.get(rid, [])),
                                  "누락쪽": miss, "원문변경카드": stale})
    # 2) 목록 → 기록(형사)
    if a.list_id:
        by = pages_by_volume(out)
        for r in read_jsonl(work(out) / f"증거목록_{a.list_id}.jsonl"):
            have = set()
            for p in by.get(norm_vol(r["권"]), []):
                have |= {x for x in (p["printed_page"], p["우측면"]) if x is not None}
            if r["권전체"]:
                held, state = sorted(have), "전부" if have else "없음"
            elif r["시작면"] is None or r["공판기록"] or r["가지"] is not None:
                held, state = [], "판정불가(쪽수란 공란·공판기록 편철·가지 면수)"
            else:
                want = set(range(r["시작면"], (r["끝면"] or r["시작면"]) + 1))
                held = sorted(want & have)
                state = "전부" if held and len(held) == len(want) else ("일부" if held else "없음")
            report["목록→기록"].append({"목록ID": a.list_id, "순번": r["순번표기"], "증거명칭": r["증거명칭"],
                                      "작성": r["작성"], "성명": r["성명"], "권": r["권"], "시작면": r["시작면"],
                                      "끝면": r["끝면"], "보유": state, "보유면": compress(held),
                                      "괄호순번": r["괄호순번"]})
    if not a.no_write:
        write_atomic(work(out) / "전수성.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    st = {}
    for x in report["목록→기록"]:
        st[x["보유"]] = st.get(x["보유"], 0) + 1
    print(json.dumps({"누락·변경": bad, "보유": st, "보고": "_작업/전수성.json"}, ensure_ascii=False))
    return 3 if bad else 0


def range_sha(rows: list[dict], s: int, e: int) -> str:
    return hashlib.sha256("".join(r["sha256"] for r in rows if s <= r["pdf_page"] <= e).encode()).hexdigest()


def compress(nums: list[int]) -> str:
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(out)


def cmd_cardsha(a) -> int:
    index = read_jsonl(work(Path(a.out)) / "색인.jsonl")
    if a.record_id and a.pages:
        s, e = parse_range(a.pages)
        rid = next((r["record_id"] for r in load_manifest(Path(a.out))["records"]
                    if nfc(a.record_id) in (r.get("증거번호"), r.get("별칭"), r["record_id"])), nfc(a.record_id))
        rows = [r for r in index if r["record_id"] == rid]
        if not rows:
            die(f"색인에 없는 record: {a.record_id}", 3)
        print(range_sha(rows, s, e))
        return 0
    for g in read_jsonl(work(Path(a.out)) / "구간.jsonl"):  # 인자 없으면 전 구간
        rows = [r for r in index if r["record_id"] == g["record_id"]]
        print(f"{g['record_id']}\t{g['시작pdf']}-{g['끝pdf']}\t{range_sha(rows, g['시작pdf'], g['끝pdf'])}")
    return 0


# ── cardfill ───────────────────────────────────────────────────────────

CARD_ORDER = ["record_id", "pdf", "출처sha256", "문서종류", "문서성격", "제목", "좌표", "증거ID", "표식"]
CARD_MECH = {"record_id", "pdf", "출처sha256", "좌표", "증거ID", "표식"}  # 모델은 문서종류·문서성격·제목(·쪽공유)만 쓴다


def card_rid(out: Path, f: Path) -> str | None:
    """카드 경로 `_작업/카드/{record_id}/{시작}-{끝}.md`에서 record_id."""
    try:
        return nfc(str(f.resolve().relative_to((work(out) / "카드").resolve()).parent))
    except ValueError:
        return None


def flow(v) -> str:
    """front-matter 값의 flow 표기(따옴표 없음)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {flow(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(flow(x) for x in v) + "]"
    return str(v)


def split_front(text: str) -> tuple[dict, str]:
    """(키 → 원문 값 문자열 — 순서 보존·여러 줄 허용, 본문)."""
    m = re.match(r"\A---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return {}, text
    fm, key = {}, None
    for line in m.group(1).split("\n"):
        k = re.match(r"^([^\s:#-][^:]*):(.*)$", line)
        if k:
            key = k.group(1).strip()
            fm[key] = k.group(2).strip()
        elif key is not None:
            fm[key] += "\n" + line
    return fm, text[m.end():]


def card_mech(root: Path, m: dict, rows: list[dict], assigns: dict, rid: str, s: int, e: int, prev: dict) -> dict:
    mr = manifest_record(m, rid) or {}
    pf = dict(page_files(root / mr["경로"])) if mr.get("경로") else {}
    marks = {"불명확": 0, "판독불가": 0, "마스킹": 0}
    for i in range(s, e + 1):
        if i in pf:
            body = read_page(pf[i])[1]
            marks["불명확"] += body.count("[불명확")
            marks["판독불가"] += body.count("[판독불가")
            marks["마스킹"] += body.count("[마스킹]")
    rng = [r for r in rows if s <= r["pdf_page"] <= e]
    boundary = False
    if norm_vol(mr.get("권")) and str(mr.get("printed_page_뜻") or "").startswith("기록 면수"):  # 형사 좌표
        nums = [x for r in rng for x in (r["printed_page"], r.get("우측면")) if x is not None]
        coord = {"권": mr["권"], "면수": [min(nums), max(nums)] if nums else None}
        if nop := [r["pdf_page"] for r in rng if r["printed_page"] is None]:
            coord["면수없는쪽"] = nop
        ids = {}
        for lid, arows in assigns.items():
            for x in arows:
                if x["record_id"] == rid and s <= x["pdf_page"] <= e and x.get("순번") is not None:
                    key = (lid, x["순번"], x.get("순번가지"))
                    ids[key] = "확정" if x["상태"] == "확정" or ids.get(key) == "확정" else "추정"
        evid = [{"목록ID": l, "순번": n, **({"가지": g} if g else {}), "상태": st}
                for (l, n, g), st in sorted(ids.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0))]
        boundary = any(len({n for l2, n, _ in ids if l2 == l}) > 1 for l in {l for l, _, _ in ids})  # 카드가 순번 경계를 넘음
    else:  # 민사·행정 좌표
        coord = {"호증": mr.get("증거번호"), "문건내쪽": [1, e - s + 1]}
        if str(mr.get("printed_page_뜻") or "").startswith("문서 쪽수"):
            pr = [(r["pdf_page"], r["printed_page"]) for r in rng if r["printed_page"] is not None]
            if pr and len(pr) == len(rng):
                coord["문서쪽"] = [min(v for _, v in pr), max(v for _, v in pr)]
            elif pr:
                coord["문서쪽"] = {"pdf": [pr[0][0], pr[-1][0]], "값": [min(v for _, v in pr), max(v for _, v in pr)]}
        if "문서쪽" not in coord and (dm := re.search(r"문서쪽:\s*(\[[^\]]*\]|\{[^}]*\})", prev.get("좌표", ""))):
            coord["문서쪽"] = dm.group(1)  # 본문에 남은 내부 쪽번호로 모델이 적은 값은 보존
        ev = mr.get("증거번호")
        evid = [{"호증": ev, "상태": mr.get("증거번호_상태") or "추정"}] if ev else []  # 상태는 매니페스트 값 그대로
    boundary = boundary or bool(re.search(r"경계불일치:\s*true", prev.get("표식", "")))
    return {"record_id": rid, "pdf": [s, e], "좌표": coord, "증거ID": evid, "표식": {**marks, "경계불일치": boundary}}


def cmd_cardfill(a) -> int:
    root, out = Path(a.root), Path(a.out)
    m, index = load_manifest(out), read_jsonl(work(out) / "색인.jsonl")
    by = {}
    for r in index:
        by.setdefault(r["record_id"], []).append(r)
    assigns = {mm.group(1): read_jsonl(f) for f in sorted(work(out).glob("배정_*.jsonl"))
               if (mm := re.fullmatch(r"배정_(.+)\.jsonl", f.name))}
    files = [Path(x) for x in a.files] if a.files else sorted((work(out) / "카드").rglob("*.md"))
    stat, bad, missing = {"카드": 0, "출처sha256_새로": 0}, [], {}
    for f in files:
        rid, mm = card_rid(out, f), re.fullmatch(r"(\d+)-(\d+)\.md", f.name)
        if not rid or not mm:
            bad.append(f"{f.name}: 카드 경로 형식 아님(_작업/카드/{{record_id}}/{{시작}}-{{끝}}.md)")
            continue
        s, e = int(mm.group(1)), int(mm.group(2))
        rows = by.get(rid, [])
        if not rows or s > e or not all(any(r["pdf_page"] == i for r in rows) for i in (s, e)):
            bad.append(f"{rid}/{f.name}: 색인에 없는 record 또는 쪽")
            continue
        prev, body = split_front(f.read_text(encoding="utf-8"))
        mech = card_mech(root, m, rows, assigns, rid, s, e, prev)
        old = re.search(r"[0-9a-f]{64}", prev.get("출처sha256", ""))
        if old and not a.resha:  # 이미 있는 값은 그 카드가 쓰인 원문 판의 기록 — 덮어쓰면 coverage가 원문 변경을 못 잡는다
            mech["출처sha256"] = old.group(0)
        else:
            mech["출처sha256"] = range_sha(rows, s, e)
            stat["출처sha256_새로"] += 1
        new = {}
        for k in CARD_ORDER:
            if k in CARD_MECH:
                new[k] = flow(mech[k])
            elif k in prev:
                new[k] = prev[k]
            else:
                missing[k] = missing.get(k, 0) + 1
        new.update({k: v for k, v in prev.items() if k not in new})
        write_atomic(f, "---\n" + "\n".join(f"{k}: {v}" for k, v in new.items()) + "\n---\n" + body)
        stat["카드"] += 1
    print(json.dumps({**stat, "모델필드_누락": missing, "오류": bad[:20]}, ensure_ascii=False))
    return 3 if bad else 0


# ── derive ─────────────────────────────────────────────────────────────

def cmd_derive(a) -> int:
    """카드의 `## 색인` 절과 `주의메모 후보:` 줄을 모아 파생물 초안을 만든다."""
    out = Path(a.out)
    alias = {r["record_id"]: r.get("증거번호") or r.get("별칭") or r["record_id"] for r in load_manifest(out)["records"]}
    times, people, memos, loose = [], [], [], 0
    for f in sorted((work(out) / "카드").rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        rid = (re.search(r"^record_id:\s*\"?([^\"\n]+?)\"?\s*$", text, re.M) or [None, ""])[1] or card_rid(out, f) or ""
        kind = (re.search(r"^문서종류:\s*(.+)$", text, re.M) or [None, ""])[1].strip()
        tagfix = lambda t: re.sub(r"\{\{p:(\d[\d-]*)\}\}", lambda m: "{{p:%s#%s}}" % (alias.get(nfc(rid), rid), m.group(1)), t)
        for line in text.split("\n"):
            if m := re.match(r"^-\s*일시:\s*(.+)$", line):
                parts = [x.strip() for x in m.group(1).split("|")]
                if len(parts) >= 3 and re.match(r"^\d{4}(-\d{2}(-\d{2})?)?", parts[0]):
                    times.append((parts[0], parts[1], tagfix(" | ".join(parts[2:])), kind))
                else:
                    loose += 1
                    times.append(("(형식 밖)", "", tagfix(m.group(1)), kind))
            elif m := re.match(r"^-\s*인물:\s*(.+)$", line):
                people.append((alias.get(nfc(rid), rid), tagfix(m.group(1))))
            elif "주의메모 후보:" in line:
                memos.append(tagfix(line.strip().lstrip("- ")))
    esc = lambda t: t.replace("|", "\\|")
    timeline = "| 일시 | 원문 표현 | 사실·좌표 | 출처 문건 종류 |\n|---|---|---|---|\n" + "".join(
        f"| {d} | {esc(o)} | {esc(t)} | {esc(k)} |\n" for d, o, t, k in sorted(times))
    prow = []
    for r, t in people:  # 인물은 병합하지 않고 이름(원문 표기)순으로 모은다
        c = [x.strip() for x in t.split("|")]
        prow.append((c[0], " · ".join(x for x in c[1:] if x), r))
    persons = "| 인물(원문 표기) | 지위·설명·좌표 | 출처 |\n|---|---|---|\n" + "".join(
        f"| {esc(n)} | {esc(d)} | {esc(r)} |\n" for n, d, r in sorted(prow))
    carded = {card_rid(out, f) for f in (work(out) / "카드").rglob("*.md")}
    n_pages = sum(1 for r in read_jsonl(work(out) / "색인.jsonl") if r["record_id"] in carded)
    written, kept = [], []
    for name, body in (("타임라인.md", timeline), ("인물.md", persons)):
        dest = out / name
        if dest.exists() and not split_front(dest.read_text(encoding="utf-8"))[0].get("생성", "").startswith("derive"):
            write_atomic(work(out) / name.replace(".md", "_초안.md"), body)  # 사람이 손본 파일은 덮어쓰지 않는다
            kept.append(name)
            continue
        fm = {"산출스킬": "ko-evidence-analysis", "층": 1, "기준일": datetime.date.today().isoformat(),
              "입력": {"record": len(carded), "쪽": n_pages}, "생성": "derive — 카드 색인 절의 기계 수집(원문 재확인·병합 없음)",
              "미해결표식": {k.strip("[]"): v for k, v in open_marks(body).items()}}
        write_atomic(dest, "---\n" + "".join(f"{k}: {flow(v)}\n" for k, v in fm.items()) + "---\n" + body)
        written.append(name)
    write_atomic(work(out) / "주의메모_후보.md", "".join(f"- {t}\n" for t in memos))
    print(json.dumps({"일시": len(times), "형식_밖_일시": loose, "인물": len(people), "주의메모_후보": len(memos),
                      "작성": written, "보존(초안은 _작업/)": kept, "후보": "_작업/주의메모_후보.md"}, ensure_ascii=False))
    return 0


# ── profile · verify · sum ─────────────────────────────────────────────

MSG_RE = re.compile(r"^(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*(오전|오후)\s*(\d{1,2}):(\d{2}),\s*([^:]+?)\s*:\s*(.*)$")


def cmd_profile(a) -> int:
    root = Path(a.root)
    rec = Path(a.record) if Path(a.record).is_absolute() else root / a.record
    lo, hi = parse_range(a.pages)
    days, senders, kw, total = {}, {}, {k: [] for k in (a.keyword or [])}, 0
    for n, p in page_files(rec):
        if not lo <= n <= hi:
            continue
        fm, body = read_page(p)
        for line in body.split("\n"):
            for k in kw:
                if k in line and (not kw[k] or kw[k][-1] != n):
                    kw[k].append(n)
            if mm := MSG_RE.match(line.strip()):
                total += 1
                d = f"{int(mm.group(1)):04d}-{int(mm.group(2)):02d}-{int(mm.group(3)):02d}"
                days.setdefault(d, {"건수": 0, "pdf": n})["건수"] += 1
                senders[mm.group(7)] = senders.get(mm.group(7), 0) + 1
    ds = sorted(days)
    gaps = []
    for x, y in zip(ds, ds[1:]):
        g = (datetime.date.fromisoformat(y) - datetime.date.fromisoformat(x)).days
        if g >= a.gap:
            gaps.append({"부터": x, "까지": y, "일수": g})
    write_atomic(Path(a.report), json.dumps({"record": rel(root, rec), "쪽": [lo, hi], "메시지": total,
                 "기간": [ds[0], ds[-1]] if ds else None, "발신자별": senders, "일자별": days, "공백기간": gaps,
                 "키워드_pdf쪽": kw}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"메시지": total, "일수": len(ds), "발신자수": len(senders), "보고": a.report}, ensure_ascii=False))
    return 0 if total else 3


def squash(s: str, drop_titles: bool = False) -> str:
    s = unicodedata.normalize("NFKC", s).replace("“", '"').replace("”", '"')  # 호환 한자·전각 문자
    s = re.sub(r"&(?:emsp|ensp|nbsp|thinsp);|\\(?=[*_#\[\]])", "", s)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"^#{1,6} .*$" if drop_titles else r"^#{1,6} ", "", s, flags=re.M)  # 쪽마다 반복되는 제목 줄
    s = re.sub(r"^\s*[-–]\s*\d+\s*[-–]\s*$", "", s, flags=re.M)  # 본문에 남은 내부 쪽번호 줄
    s = re.sub(r"<br\s*/?>|\*\*|\\\||^\s*>\s?", "", s, flags=re.M)  # ~~말소~~는 남긴다 — 말소 문구는 ~~째 인용해야 통과
    s = re.sub(r"\[(?:%s)[^\]]*\]" % "|".join(MASK_VARIANTS), "[마스킹]", s)
    s = re.sub(r"\[강조:\s*|[\[\]]", "", s)  # 표식 경계에 걸친 인용 허용(양쪽에 똑같이 적용)
    return re.sub(r"[\s|]+", "", s)


def in_order(parts: list[str], hay: str) -> bool:
    """중략(…)으로 나눈 조각이 원문에 차례대로 나오는지."""
    pos = 0
    for x in parts:
        i = hay.find(x, pos)
        if i < 0:
            return False
        pos = i + len(x)
    return True


OPEN_MARKS = ["원본 PDF 확인 필요", "면수 확인 필요", "변호사 확정 필요", "행 좌표 필요", "프레임 필요", "확인 필요",
              "[판례 미확인]", "[법령 미확인]", "[불명확", "[판독불가", "[마스킹]"]
def open_marks(body: str) -> dict:
    """미해결 표식의 종류별 개수(긴 표식부터 세고 지워 '확인 필요'의 중복 집계를 막는다)."""
    body, got = body.replace("미완성 — 확인 필요 사항 있음", ""), {}
    for mk in OPEN_MARKS:
        if n := body.count(mk):
            got[mk] = n
        body = body.replace(mk, "")
    return got


QUOTE_RE = re.compile(r"(?:[\"“]([^\"“”\n]{2,})[\"”]|「([^」\n]{2,})」)\s*\{\{p:([^}]+)\}\}")  # 「」는 원문에 큰따옴표가 든 구절용


def cmd_verify(a) -> int:
    root, out = Path(a.root), Path(a.out)
    recs = load_manifest(out)["records"]
    paths = {r["record_id"]: root / r["경로"] for r in recs}
    nums = [r.get("증거번호") or r.get("별칭") for r in recs]
    paths.update({n: root / r["경로"] for r, n in zip(recs, nums) if n and nums.count(n) == 1})  # {{p:갑7#3}} 별칭
    cache, fails, n_q, n_untagged, n_tag, tally, per_file = {}, [], 0, 0, 0, {}, {}
    files = a.files or sorted(str(x) for x in list((work(out) / "카드").rglob("*.md")) + list(out.glob("*.md")))
    for f in files:
        text = Path(f).read_text(encoding="utf-8")
        default = (re.search(r"^record_id:\s*\"?([^\"\n]+?)\"?\s*$", text, re.M) or [None, None])[1] or card_rid(out, Path(f))
        body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
        fm_end = text.count("\n", 0, len(text) - len(body))
        body, mine = body.replace("미완성 — 확인 필요 사항 있음", ""), {}
        for mk in OPEN_MARKS:  # 긴 표식부터 세고 지워 '확인 필요'의 중복 집계를 막는다
            mine[mk], tally[mk] = body.count(mk), tally.get(mk, 0) + body.count(mk)
            body = body.replace(mk, "")
        per_file[Path(f).name] = {k: v for k, v in mine.items() if v}
        for ln, line in enumerate(text.split("\n"), 1):
            for tag in PTAG_RE.findall(QUOTE_RE.sub("", line)):  # 요약·추론 좌표의 실재 검사
                n_tag += 1
                rid, _, pg = tag.rpartition("#")
                rid = nfc(rid or default or "")
                ok = rid in paths and re.fullmatch(r"\d+(-\d+)?", pg)
                if ok:
                    have = dict(page_files(paths[rid]))
                    ok = all(i in have for i in range(parse_range(pg)[0], parse_range(pg)[1] + 1))
                if not ok:
                    fails.append({"파일": f, "줄": ln, "좌표": tag, "사유": "좌표 해석 불가(요약)"})
            if ln > fm_end:  # front-matter 제외, 따옴표를 차례로 짝지어 8자 이상만
                n_untagged += sum(len(x) >= 8 for x in re.findall(r"[\"“]([^\"“”\n]*)[\"”]", QUOTE_RE.sub("", line)))
            for q1, q2, tag in QUOTE_RE.findall(line):
                q = q1 or q2
                n_q += 1
                rid, _, pg = tag.rpartition("#")
                rid = nfc(rid or default or "")
                if rid not in paths or not re.fullmatch(r"\d+(-\d+)?", pg):
                    fails.append({"파일": f, "줄": ln, "좌표": tag, "사유": "좌표 해석 불가"})
                    continue
                s, e = parse_range(pg)
                key = (rid, s, e)
                if key not in cache:
                    pf = dict(page_files(paths[rid]))
                    if s > e or any(i not in pf for i in range(s, e + 1)):
                        cache[key] = None  # 요약 좌표와 같이 좌표의 쪽이 모두 있어야 한다
                    else:
                        raw = "".join(read_page(pf[i])[1] for i in range(s, e + 1))
                        cache[key] = (squash(raw), squash(raw, drop_titles=True))  # 제목 인용 / 쪽 경계 인용
                if cache[key] is None:
                    fails.append({"파일": f, "줄": ln, "좌표": tag, "사유": "좌표 해석 불가(없는 쪽)"})
                    continue
                parts = [x for x in (squash(y) for y in re.split(r"…+|\.{3,}|\(중략\)|\[중략\]", q)) if x]
                if not parts or (len(parts) > 1 and max(map(len, parts)) < 4):
                    fails.append({"파일": f, "줄": ln, "좌표": tag, "사유": "인용 조각이 없거나 너무 짧음(중략 인용은 가장 긴 조각 4자 이상)", "인용": q})
                elif not any(in_order(parts, hay) for hay in cache[key]):
                    fails.append({"파일": f, "줄": ln, "좌표": tag, "사유": "원문 불일치", "인용": q})
    if a.report:
        write_atomic(Path(a.report), json.dumps(fails, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"직접인용": n_q, "요약좌표": n_tag, "불일치": len(fails), "미해결표식": {k: v for k, v in tally.items() if v},
                      **({"파일별_표식": per_file} if a.files and len(files) <= 12 else {}), "좌표없는_인용부호": n_untagged,
                      "파일": len(files),
                      "위치": [f"{x['파일']}:{x['줄']}" for x in fails][:30]}, ensure_ascii=False))
    return 3 if fails else 0


def to_number(cell: str):
    c = re.sub(r"[,\s원₩]", "", cell)
    if re.fullmatch(r"\(\d+(\.\d+)?\)", c):
        c = "-" + c[1:-1]
    c = c.replace("△", "-").replace("▲", "-")
    return float(c) if re.fullmatch(r"-?\d+(\.\d+)?", c) else None


AGG_RE = re.compile(r"^(?:소계|합계|누계|총계|총합계|이월|전기이월|계)$|(?:^|[\s(])(?:소|합|누|총)?\s*계$|총사용계$")


def is_agg(cells: list[str]) -> bool:
    """소계·합계 행 — 어느 칸이든 라벨이 '계'·'○○ 계'·'[ ○○ 계 ]'·소계·합계·누계·총계·이월이면."""
    for c in cells:
        lab = re.sub(r"[\[\]*\s]+", " ", c).strip()
        if lab and len(lab) <= 20 and AGG_RE.search(lab):
            return True
    return False


def resolve_record(root: Path, record: str, out: str | None) -> Path:
    """--record는 경로, 또는 (--out이 있으면) 매니페스트의 증거번호·record_id."""
    rec = Path(record) if Path(record).is_absolute() else root / record
    if not rec.is_dir() and out:
        rs = load_manifest(Path(out))["records"]
        hit = [r for r in rs if nfc(record) in (r.get("증거번호"), r.get("별칭"), r["record_id"])]
        if len(hit) == 1:
            rec = root / hit[0]["경로"]
    if not page_files(rec):
        die(f"record를 찾지 못함(쪽 파일 0개): {record}", 3)
    return rec


def sum_col(rec: Path, ranges: list[tuple[int, int]], col: str | None, col_index: int | None, check: bool,
            by: str | None) -> tuple[dict, dict]:
    """한 열의 합계·건수(소계·합계 행 제외, 표식 셀 분리). (결과, --by 집계)"""
    total, n, uncertain, excluded, bad, groups, used, warn, seg, mid, mism = 0.0, 0, [], 0, [], {}, set(), [], 0.0, 0.0, []
    guessed = 0
    want = re.sub(r"\s+", "", col) if col else None
    for pn, p in page_files(rec):
        if not any(lo <= pn <= hi for lo, hi in ranges):
            continue
        for head, rows in tables(read_page(p)[1]):
            if col_index:
                ci = col_index - 1
                if ci >= len(head):
                    continue
            else:
                hits = [k for k, h in enumerate(head) if want in re.sub(r"\s+", "", h)]
                exact = [k for k in hits if re.sub(r"\s+", "", head[k]) == want]
                if not hits:
                    continue
                if len(exact) != 1 and len(hits) > 1:
                    msg = f"'{col}'에 맞는 열이 여럿 — 첫 열을 읽음(--col-index로 지정)"
                    warn.append(msg) if msg not in warn else None
                ci = (exact or hits)[0]
            used.add(f"{ci + 1}:{head[ci]}")
            bi = find_col(head, re.sub(r"\s+", "", by)) if by else None
            if any(len(r) != len(head) for r in rows):
                warn.append(f"{pn}쪽: 머리글 {len(head)}열과 칸 수가 다른 행이 있음")
            last, tsum, tn = None, 0.0, 0
            for ri, r in enumerate(rows, 1):
                cell = re.sub(r"\[강조:\s*([^\]]*)\]|\*\*", r"\1", r[ci] if ci < len(r) else "")
                if is_agg(r):
                    excluded += 1
                    v = to_number(cell)
                    lab = " ".join(r)
                    if check and v is not None:  # 계층 소계: 직전 구간·중간 누적·전체 가운데 하나와 맞으면 통과
                        if abs(mid - v) <= 0.5:
                            mid = 0.0
                        elif not any(abs(x - v) <= 0.5 for x in (seg, total)):
                            mism.append(f"{pn}쪽 {ri}행(기재 {int(v)} / 구간 합 {int(seg)}·중간 누적 {int(mid)}·전체 {int(total)})")
                    seg = 0.0
                elif "[불명확" in cell or "[판독불가" in cell or "[마스킹]" in cell:
                    uncertain.append(pn)
                elif cell:
                    v = to_number(cell)
                    if v is None:
                        bad.append(f"{pn}쪽 {ri}행")
                    else:
                        total, n, seg, mid, tsum, tn = total + v, n + 1, seg + v, mid + v, tsum + v, tn + 1
                        gk = r[bi] if bi is not None and bi < len(r) else None
                        if gk is not None:
                            g = groups.setdefault(gk, {"합계": 0.0, "건수": 0})
                            g["합계"], g["건수"] = g["합계"] + v, g["건수"] + 1
                        unlabeled = not any(re.search(r"[가-힣A-Za-z]", c) for k, c in enumerate(r) if k != ci)
                        last = (pn, ri, v, unlabeled, gk)
            # 라벨 없는 말미 합계 행: 숫자 외 칸에 글자가 없는 표의 마지막 행이 같은 표(또는 여러 쪽에 걸친 범위 전체)의
            # 위 행 합과 같을 때만 합계로 본다. '잔금'처럼 라벨이 있는 행은 합계와 값이 같아도 데이터다
            if last and last[3] and last[1] == len(rows) and (
                    (tn > 2 and abs((tsum - last[2]) - last[2]) < 0.5) or (n > 2 and abs((total - last[2]) - last[2]) < 0.5)):
                total, n, excluded, guessed = total - last[2], n - 1, excluded + 1, guessed + 1
                if last[4] is not None:
                    groups[last[4]]["합계"] -= last[2]
                    groups[last[4]]["건수"] -= 1
                warn.append(f"{last[0]}쪽 {last[1]}행: 라벨 없는 말미 행이 위 행들의 합과 같아 합계 행으로 보고 제외함")
    if not used:
        die(f"대상 열을 가진 표가 없음 — --col 철자 또는 --col-index 확인({col or col_index})")
    res = {"읽은열": sorted(used), "합계": int(total) if total == int(total) else total, "건수": n,
           "소계·합계행_제외": excluded, "말미행_합계추정": guessed, "표식셀_pdf쪽(합산 제외)": uncertain, "숫자아님(합산 제외)": bad, "경고": warn}
    if check:
        res["소계_불일치"] = mism
    return res, dict(sorted(groups.items(), key=lambda kv: -kv[1]["합계"]))


def cmd_sum(a) -> int:
    """열 여러 개(--col 반복)·쪽 범위 여러 개(--pages 45-47,60-62)를 한 번에 — 열마다 따로 합산한다."""
    rec = resolve_record(Path(a.root), a.record, a.out)
    ranges = [parse_range(x) for x in a.pages.split(",") if x.strip()]
    if a.by and not a.report:
        die("--by는 --report와 함께 쓴다(집계 키에 인명이 들 수 있어 터미널에 출력하지 않는다)")
    if not a.col and not a.col_index:
        die("--col 또는 --col-index 지정")
    specs = [(None, a.col_index)] if a.col_index else [(c, None) for c in a.col]
    results, reports = [], {}
    for col, cidx in specs:
        res, groups = sum_col(rec, ranges, col, cidx, a.check, a.by)
        results.append(res)
        reports[col or f"{cidx}열"] = groups
    if a.by:
        write_atomic(Path(a.report), json.dumps(next(iter(reports.values())) if len(reports) == 1 else reports,
                                                ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results[0] if len(results) == 1 else {"열별": results}, ensure_ascii=False))
    return 3 if any(r["표식셀_pdf쪽(합산 제외)"] or r.get("소계_불일치") for r in results) else 0


# ── main ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="증거·기록 분석 보조 CLI — 쪽 단위 markdown 변환본 처리")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, root=True, out=None):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        if root:
            p.add_argument("--root", required=True, help="작업 루트(사건 폴더)")
        if out is not None:
            p.add_argument("--out", required=out, help="분석 폴더")
        return p

    p = add("discover", cmd_discover, "서명(pages/page_*.md + _index.md)으로 변환본 발견 — 목록 조회만", out=False)
    p.add_argument("--exclude", action="append", help="제외할 폴더(작업 루트 기준, 반복 가능)")
    p = add("gate", cmd_gate, "입력 게이트 — 규격 불변식·세대 판정·반입 검증", out=False)
    p.add_argument("records", nargs="+", help="변환본 폴더")
    p = add("index", cmd_index, "작업 색인·구간 재구성(_작업/색인.jsonl·구간.jsonl)", out=True)
    p.add_argument("records", nargs="+")
    p = add("import", cmd_import, "변환 산출 폴더를 변환본 저장소로 반입(_work 제외·NFC·gate·매니페스트 기록)", out=True)
    p.add_argument("--src", required=True, help="변환 산출 폴더")
    p.add_argument("--dest", help="변환본 저장소(작업 루트 기준). 생략 시 매니페스트 값")
    p.add_argument("--bundle", help="묶음 이름(여러 권 한 묶음 또는 stem 충돌 시)")
    p.add_argument("--original", help="원본 파일(작업 루트 안)")
    p.add_argument("--category", help="분류(자유 문자열)")
    p.add_argument("--model", help="변환 모델(사용자 기재)")
    p.add_argument("--replace", action="store_true", help="재반입 — 기존 폴더를 통째로 교체")
    p = add("locate", cmd_locate, "원본 재탐색(stem → 크기 → sha256, 크기가 같은 후보만 해시)", out=True)
    p.add_argument("record_id", nargs="?")
    p = add("evlist", cmd_evlist, "형사 증거목록 파서(_작업/증거목록_{ID}.jsonl)", out=True)
    p.add_argument("--record", required=True)
    p.add_argument("--list-id", required=True, help="목록 ID(예: L1)")
    p.add_argument("--pages", help="목록 pdf쪽 범위. 생략 시 heading에 '증거목록'이 든 쪽")
    p.add_argument("--succeeds", help="승계한 앞 목록 ID")
    p = add("assign", cmd_assign, "증거 단위 배정 — (권, 면수)로 쪽별 순번(_작업/배정_{ID}.jsonl)", root=False, out=True)
    p.add_argument("--list-id", required=True)
    p = add("coverage", cmd_coverage, "전수성 — 기록→카드, 목록→기록(_작업/전수성.json)", root=False, out=True)
    p.add_argument("--list-id")
    p.add_argument("--no-write", action="store_true", help="전수성.json을 쓰지 않고 요약만 출력")
    p = add("cardsha", cmd_cardsha, "카드 front-matter에 적을 출처sha256 계산", root=False, out=True)
    p.add_argument("record_id", nargs="?", help="생략하면 구간.jsonl의 전 구간을 출력")
    p.add_argument("pages", nargs="?")
    p = add("cardfill", cmd_cardfill, "카드 front-matter의 기계 필드(record_id·pdf·출처sha256·좌표·증거ID·표식) 채움", out=True)
    p.add_argument("--resha", action="store_true", help="출처sha256도 현재 원문으로 다시 계산(원문을 다시 확인한 카드에만)")
    p.add_argument("files", nargs="*", help="카드 경로. 생략하면 분석 폴더의 전 카드")
    add("derive", cmd_derive, "카드의 색인 절에서 타임라인.md·인물.md 작성(손본 파일은 보존)과 주의메모 후보 수집", root=False, out=True)
    p = add("profile", cmd_profile, "대화 내보내기 출력물 프로필(일자·발신자별 건수·공백·키워드 위치)")
    p.add_argument("--record", required=True)
    p.add_argument("--pages", required=True)
    p.add_argument("--report", required=True, help="결과 JSON 경로(분석 폴더 안)")
    p.add_argument("--keyword", action="append")
    p.add_argument("--gap", type=int, default=7, help="공백으로 볼 최소 일수(기본 7)")
    p = add("verify", cmd_verify, "직접 인용 역검증 — \"인용구\"{{p:record_id#쪽}}을 pages와 대조", out=True)
    p.add_argument("--report", help="불일치 상세 JSON 경로(분석 폴더 안)")
    p.add_argument("files", nargs="*", help="생략하면 분석 폴더의 전 카드와 산출물")
    p = add("sum", cmd_sum, "표 열 합산(소계·합계 행 제외, 표식 셀 분리 보고)")
    p.add_argument("--record", required=True)
    p.add_argument("--pages", required=True, help="pdf쪽 범위 — 여러 개는 쉼표(45-47,60-62)")
    p.add_argument("--col", action="append", help="열 머리글(완전일치 우선, 없으면 부분일치) — 반복하면 열마다 따로 합산")
    p.add_argument("--col-index", type=int, help="열 번호(1부터) — 다단 머리글·동명 열에서 --col 대신")
    p.add_argument("--check", action="store_true", help="소계·합계 행의 기재값을 행 합과 대조")
    p.add_argument("--out", help="분석 폴더 — 주면 --record에 증거번호(갑7)를 쓸 수 있다")
    p.add_argument("--by", help="이 열의 값별로 합계·건수 집계(--report 필수)")
    p.add_argument("--report", help="--by 집계 JSON 경로(분석 폴더 안)")

    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
