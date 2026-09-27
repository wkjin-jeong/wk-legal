#!/usr/bin/env python3
"""wk-ko-legal 스킬 description 트리거 실측 러너(표준).

skills/*/evals/trigger-eval.json 전부를 읽어 질의마다 `claude -p … --plugin-dir <플러그인> --output-format
stream-json --verbose --max-turns 4`를 돌리고, 첫 Skill 호출에서 실행을 끊어 그 스킬로 채점한다.
모델 실측이라 요금이 든다 — 먼저 --check, 그다음 --limit 3으로 사전 점검한다.

평가 세트 스키마(skill-creator의 query·should_trigger 호환 + 선택 필드):
  query(str, 필수) · should_trigger(bool, 필수)
  expected   — 음성 행 전부에 둔다. 기대하는 첫 Skill: 스킬명(플러그인 접두어 없음)·"none"·허용 목록 배열.
               "none"은 스킬 미호출과 플러그인 밖 스킬을 뜻한다.
  site_unspecified(bool) — lbox·bigcase 세트의 사이트 미지정 양성. 두 검색 스킬 어느 쪽이든 정답.
  note(str)  — 사유.
채점: 양성은 첫 Skill == 그 세트의 스킬(site_unspecified면 두 검색 스킬 중 하나), 음성은 첫 Skill ∈ expected
(expected가 없으면 '그 세트의 스킬이 아님'). 실행 오류(인증·api_error·timeout·출력 없음)는 표본에서 빼고,
유효 실행이 없으면 SKIP, 유효 실행 동수면 TIE. --max-turns 상한 도달은 정상 종료(스킬 미호출)다.

사용:
  python3 tools/trigger_eval.py --check                     세트 형식 검사만(모델 호출 없음)
  python3 tools/trigger_eval.py --limit 3 [--skill 이름]    사전 점검(고유 질의 앞 3건)
  python3 tools/trigger_eval.py [--out DIR] [--par 4] [--model M]
  python3 tools/trigger_eval.py --rescore DIR [--legacy-queries queries.json]
                                                            기존 실행 기록(DIR/runs)을 현재 세트로 재채점(모델 호출 없음)
  python3 tools/trigger_eval.py --fake map.json --out DIR   러너 자체 시험 — map {질의: [회차별 결과]}로
                                                            stream-json을 합성(결과 값: 스킬명 | "" | "ERR:<유형>" | "EXT:<외부 스킬>")
결과: DIR/results.tsv · DIR/summary.json · DIR/keys.json(질의→키) · DIR/runs/<키>_r<회차>.jsonl
DIR 기본값은 임시 폴더다. 플러그인 폴더 안은 쓰지 않는다(각 실행의 cwd도 빈 임시 폴더).
"""
import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PREFIX = "wk-ko-legal:"
CASE_SKILLS = ("lbox-case-search", "bigcase-case-search")
AUTH_ERRORS = ("authentication_failed", "api_error", "no-output", "result-error")
ALLOWED_KEYS = {"query", "should_trigger", "expected", "site_unspecified", "note"}
NONE = "(없음)"
MAX_TURNS = 4
MAX_TRIES = 5
WANT_VALID = 3


# ---------------------------------------------------------------- 세트 적재·검사

def plugin_skills():
    return sorted(p.parent.name for p in (REPO / "skills").glob("*/SKILL.md"))


def load_rows(only=None):
    """모든 trigger-eval.json → 행 목록(스킬·번호·필드). only: 스킬명 집합."""
    rows = []
    for f in sorted(glob.glob(str(REPO / "skills" / "*" / "evals" / "trigger-eval.json"))):
        skill = Path(f).parent.parent.name
        if only and skill not in only:
            continue
        for i, x in enumerate(json.load(open(f, encoding="utf-8")), 1):
            exp = x.get("expected")
            rows.append({
                "skill": skill, "n": i, "query": x.get("query"),
                "should_trigger": x.get("should_trigger"),
                "expected": [exp] if isinstance(exp, str) else exp,
                "site_unspecified": x.get("site_unspecified", False),
                "note_src": x.get("note", ""), "_raw": x,
            })
    return rows


def check_rows(rows, full=True):
    """스키마 검사 → 오류 문자열 목록. full이면 세트가 없는 스킬도 오류로 센다."""
    names = set(plugin_skills())
    errs = []
    have = {r["skill"] for r in rows}
    for s in sorted(names - have) if full else ():
        errs.append(f"{s}: evals/trigger-eval.json 없음")
    seen = defaultdict(set)
    for r in rows:
        loc = f"{r['skill']}#{r['n']}"
        x = r["_raw"]
        extra = set(x) - ALLOWED_KEYS
        if extra:
            errs.append(f"{loc}: 모르는 필드 {sorted(extra)}")
        if not isinstance(r["query"], str) or not r["query"].strip():
            errs.append(f"{loc}: query 없음")
            continue
        if not isinstance(r["should_trigger"], bool):
            errs.append(f"{loc}: should_trigger가 bool이 아님")
            continue
        if r["query"] in seen[r["skill"]]:
            errs.append(f"{loc}: 같은 세트 안 중복 질의")
        seen[r["skill"]].add(r["query"])
        if not isinstance(r["site_unspecified"], bool):
            errs.append(f"{loc}: site_unspecified가 bool이 아님")
        elif r["site_unspecified"] and (not r["should_trigger"] or r["skill"] not in CASE_SKILLS):
            errs.append(f"{loc}: site_unspecified는 lbox·bigcase 세트의 양성 행에만 둔다")
        if "note" in x and not isinstance(x["note"], str):
            errs.append(f"{loc}: note가 문자열이 아님")
        exp = r["expected"]
        if r["should_trigger"]:
            if exp is not None:
                errs.append(f"{loc}: expected는 음성 행에만 둔다")
        else:
            if exp is None:
                errs.append(f"{loc}: 음성 행에 expected 없음")
            elif not isinstance(exp, list) or not exp or not all(isinstance(v, str) for v in exp):
                errs.append(f"{loc}: expected 형식 오류(스킬명·\"none\"·배열)")
            else:
                for v in exp:
                    if v != "none" and v not in names:
                        errs.append(f"{loc}: expected 값 '{v}'는 플러그인 스킬명이 아님(접두어 없이)")
                if r["skill"] in exp:
                    errs.append(f"{loc}: 음성 행의 expected에 자기 스킬이 들어 있음")
    # 세트 사이 정합: 같은 질의가 한 세트의 양성이면 다른 세트 음성의 expected에 그 스킬이 있어야 한다
    by_q = defaultdict(list)
    for r in rows:
        if isinstance(r["query"], str):
            by_q[r["query"]].append(r)
    for q, rs in by_q.items():
        pos = [r for r in rs if r["should_trigger"] is True]
        for p in pos:
            want = set(CASE_SKILLS) if p["site_unspecified"] else {p["skill"]}
            for n in rs:
                if n["should_trigger"] is False and isinstance(n["expected"], list) and not want & set(n["expected"]):
                    errs.append(f"{n['skill']}#{n['n']}: 같은 질의가 {p['skill']}#{p['n']} 양성인데 expected {n['expected']}")
        negs = [r for r in rs if r["should_trigger"] is False and isinstance(r["expected"], list)]
        if len({tuple(sorted(r["expected"])) for r in negs}) > 1:
            errs.append(f"질의 '{q[:30]}…': 세트마다 expected가 다름 " + ", ".join(f"{r['skill']}#{r['n']}={r['expected']}" for r in negs))
    return errs


# ---------------------------------------------------------------- 실행·파싱

def short(s):
    """Skill 입력값 → 표시 이름. 플러그인 밖 스킬은 '(외부:이름)'."""
    if not s:
        return NONE
    if s.startswith(PREFIX):
        return s[len(PREFIX):]
    return f"(외부:{s})"


def parse(path):
    """stream-json → {model, skill(첫 Skill 호출), first_tool, error, done}"""
    out = {"model": None, "skill": None, "first_tool": None, "error": None, "done": False}
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        out["error"] = "no-output"
        return out
    if not any(l.strip() for l in lines):
        out["error"] = "no-output"
        return out
    for l in lines:
        try:
            d = json.loads(l)
        except ValueError:
            continue
        t = d.get("type")
        if t == "system" and d.get("subtype") == "init":
            out["model"] = d.get("model")
        elif t == "assistant":
            if d.get("error"):
                out["error"] = d.get("error")
            for c in (d.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    if out["first_tool"] is None:
                        out["first_tool"] = c.get("name")
                    if c.get("name") == "Skill" and out["skill"] is None:
                        out["skill"] = (c.get("input") or {}).get("skill")
        elif t == "result":
            out["done"] = True
            max_turns = d.get("terminal_reason") == "max_turns" or d.get("subtype") == "error_max_turns"
            if d.get("is_error") and not out["error"] and not max_turns:
                out["error"] = d.get("terminal_reason") or d.get("subtype") or "result-error"
            m = re.search(r"maximum number of turns \((\d+)\)", " ".join(map(str, d.get("errors") or [])))
            if max_turns and m and int(m.group(1)) < MAX_TURNS:
                out["error"] = f"short-max-turns({m.group(1)})"   # 옛 러너의 1턴 절단 기록 — 표본에서 뺀다
            if "Failed to authenticate" in str(d.get("result") or ""):
                out["error"] = "authentication_failed"
    if out["skill"]:            # 스킬 호출까지 받았으면 뒤의 오류는 판정과 무관
        out["error"] = None
    elif not out["done"] and not out["error"]:
        out["error"] = "incomplete"   # result 줄도 스킬 호출도 없이 끊긴 기록
    return out


def has_skill_call(line):
    try:
        d = json.loads(line)
    except ValueError:
        return False
    if d.get("type") != "assistant":
        return False
    return any(c.get("type") == "tool_use" and c.get("name") == "Skill"
               for c in (d.get("message") or {}).get("content") or [])


def fake_stream(fake, q, rep):
    seq = fake.get(q, [""])
    v = seq[min(rep, len(seq)) - 1]
    lines = [{"type": "system", "subtype": "init", "model": "fake-model"}]
    if v.startswith("ERR:"):
        e = v[4:]
        txt = "Failed to authenticate: OAuth session expired" if e == "authentication_failed" else f"error: {e}"
        lines.append({"type": "assistant", "error": e, "message": {"content": [{"type": "text", "text": txt}]}})
        lines.append({"type": "result", "subtype": "error_during_execution", "is_error": True,
                      "terminal_reason": "api_error", "result": txt})
    else:
        content = [{"type": "text", "text": "x"}]
        if v:
            name = v[4:] if v.startswith("EXT:") else PREFIX + v
            content.append({"type": "tool_use", "name": "Skill", "input": {"skill": name}})
        lines.append({"type": "assistant", "message": {"content": content}})
        if not v:                 # 스킬 호출이 있으면 실제 러너처럼 거기서 끊는다(result 줄 없음)
            lines.append({"type": "result", "subtype": "error_max_turns", "is_error": True,
                          "terminal_reason": "max_turns", "result": ""})
    return "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n"


class Runner:
    def __init__(self, out, timeout, model, fake):
        self.out = out
        self.runs = out / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.cwd_root = Path(tempfile.mkdtemp(prefix="wk-ko-legal-trigger-cwd-"))
        self.timeout = timeout
        self.model = model
        self.fake = fake

    def cmd(self, q):
        c = ["claude", "-p", q, "--plugin-dir", str(REPO), "--strict-mcp-config",
             "--output-format", "stream-json", "--verbose", "--max-turns", str(MAX_TURNS),
             "--disallowedTools", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"]
        if self.model:
            c += ["--model", self.model]
        return c

    def run_one(self, key, q, rep):
        path = self.runs / f"{key}_r{rep}.jsonl"
        err = self.runs / f"{key}_r{rep}.err"
        t0 = time.time()
        if self.fake is not None:
            path.write_text(fake_stream(self.fake, q, rep), encoding="utf-8")
            err.write_text("")
            res = parse(path)
        else:
            old = parse(path) if path.exists() else None
            if old and not old["error"] and (old["skill"] or (old["done"] and old["first_tool"] is None)):
                res = dict(old, reused=True)      # 이어 달리기: 스킬 호출 또는 도구 없이 끝난 실행만 재사용
            else:
                wd = tempfile.mkdtemp(prefix=f"{key}_r{rep}_", dir=self.cwd_root)
                with open(err, "w", encoding="utf-8") as fe:
                    proc = subprocess.Popen(self.cmd(q), cwd=wd, stdout=subprocess.PIPE, stderr=fe,
                                            text=True, encoding="utf-8", start_new_session=True)
                    state = {"killed": None}

                    def kill_group():   # 자식이 stdout을 쥐고 있어도 읽기가 풀리게 프로세스 그룹째 끊는다
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            pass

                    def on_timeout():
                        state["killed"] = state["killed"] or "timeout"
                        kill_group()
                    timer = threading.Timer(self.timeout, on_timeout)
                    timer.start()
                    try:
                        with open(path, "w", encoding="utf-8") as fo:
                            for line in proc.stdout:
                                fo.write(line)
                                if has_skill_call(line):
                                    state["killed"] = "after-skill"
                                    break
                    finally:
                        timer.cancel()
                        kill_group()
                        proc.wait()
                res = parse(path)
                if state["killed"] == "timeout" and not res["skill"]:
                    res["error"] = "timeout"      # parse의 incomplete·no-output보다 우선(사전 점검이 인증 실패로 오판하지 않게)
        res["sec"] = round(time.time() - t0, 1)
        res["rep"] = rep
        return res


def load_runs(runs_dir, key):
    files = sorted(Path(runs_dir).glob(f"{key}_r*.jsonl"), key=lambda p: int(p.stem.rsplit("_r", 1)[1]))
    out = []
    for f in files:
        r = parse(f)
        r["rep"] = int(f.stem.rsplit("_r", 1)[1])
        out.append(r)
    return out


# ---------------------------------------------------------------- 채점

def expected_label(row):
    if row["should_trigger"]:
        return "판례(사이트 미지정)" if row["site_unspecified"] else row["skill"]
    if row["expected"]:
        return "|".join(row["expected"])
    return f"¬{row['skill']}"


def verdict_one(row, skill_called):
    s = short(skill_called)
    if row["should_trigger"]:
        if row["site_unspecified"]:
            return s in CASE_SKILLS
        return s == row["skill"]
    if row["expected"]:
        if s == NONE or s.startswith("(외부:"):
            return "none" in row["expected"]
        return s in row["expected"]
    return s != row["skill"]


def valid(rs):
    return [x for x in rs if not x.get("error")]


def judge(row, rs):
    """→ (verdict, note). 오류 실행은 표본에서 제외."""
    v = valid(rs)
    errs = Counter(x["error"] for x in rs if x.get("error"))
    enote = (" · 오류 제외 " + ",".join(f"{k}×{n}" for k, n in errs.items())) if errs else ""
    if not v:
        return "SKIP", f"유효 실행 0/{len(rs)}{enote}"
    votes = [verdict_one(row, x["skill"]) for x in v]
    ok, n = sum(votes), len(votes)
    if ok * 2 > n:
        return "PASS", f"{ok}/{n} 일치{enote}"
    if ok * 2 == n:
        return "TIE", f"{ok}/{n} 동수{enote}"
    return "FAIL", f"{ok}/{n} 일치{enote}"


def summarize(rows, results):
    """스킬별 재현율·특이도·음성 목적지 적중, 혼동행렬(기대 → 유효 실행 최빈 관측), 합계."""
    per = defaultdict(Counter)
    conf = defaultdict(Counter)
    for r in rows:
        k = "pos" if r["should_trigger"] else "neg"
        per[r["skill"]][f"{k}_{r['verdict']}"] += 1
        if not r["should_trigger"] and r["expected"]:
            per[r["skill"]]["dest_total"] += r["verdict"] in ("PASS", "FAIL", "TIE")
            per[r["skill"]]["dest_hit"] += r["verdict"] == "PASS"
        v = valid(results.get(r["key"], []))
        if v:
            mode = Counter(short(x["skill"]) for x in v).most_common(1)[0][0]
            conf[expected_label(r)][mode] += 1
    out = {"per_skill": {}, "confusion": {k: dict(v) for k, v in sorted(conf.items())}}
    for s, c in sorted(per.items()):
        pp, pf, pt = c["pos_PASS"], c["pos_FAIL"], c["pos_TIE"]
        np_, nf, nt = c["neg_PASS"], c["neg_FAIL"], c["neg_TIE"]
        out["per_skill"][s] = {
            "recall": f"{pp}/{pp + pf + pt}" if pp + pf + pt else "n/a",
            "specificity": f"{np_}/{np_ + nf + nt}" if np_ + nf + nt else "n/a",
            "neg_dest": f"{c['dest_hit']}/{c['dest_total']}" if c["dest_total"] else "n/a",
            "pos_skip": c["pos_SKIP"], "neg_skip": c["neg_SKIP"],
        }
    tot = Counter(r["verdict"] for r in rows)
    seen = set()
    err_runs = ext_runs = 0
    for r in rows:
        if r["key"] in seen:
            continue
        seen.add(r["key"])
        for x in results.get(r["key"], []):
            err_runs += bool(x.get("error"))
            ext_runs += (not x.get("error")) and short(x["skill"]).startswith("(외부:")
    out["totals"] = dict(tot, ERR_runs=err_runs, EXTERNAL_runs=ext_runs)
    return out


def write_outputs(out, rows, results):
    tsv = out / "results.tsv"
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("스킬\t번호\t질의\t기대\t기대 목적지\t사이트미지정\t실측(회차순)\t판정\t비고\n")
        for r in rows:
            rs = results.get(r["key"], [])
            obs = [short(x["skill"]) if not x.get("error") else f"ERR:{x['error']}" for x in rs]
            f.write("\t".join([r["skill"], str(r["n"]), r["query"],
                               "트리거" if r["should_trigger"] else "비트리거",
                               "" if r["should_trigger"] else expected_label(r),
                               "예" if r["site_unspecified"] else "",
                               " | ".join(obs), r.get("verdict", "SKIP"), r.get("note", "")]) + "\n")
    return tsv


def assign_keys(rows, keymap=None):
    keymap = dict(keymap or {})
    for r in rows:
        if r["query"] not in keymap:
            keymap[r["query"]] = f"q{len(keymap):03d}"
        r["key"] = keymap[r["query"]]
    return keymap


# ---------------------------------------------------------------- 진입점

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="세트 형식 검사만")
    ap.add_argument("--skill", action="append", help="이 스킬 세트만(여러 번 가능)")
    ap.add_argument("--limit", type=int, help="고유 질의 앞 N건만 실행")
    ap.add_argument("--out", help="결과 폴더(기본: 임시 폴더)")
    ap.add_argument("--par", type=int, default=4, help="동시 실행 수(기본 4)")
    ap.add_argument("--timeout", type=int, default=180, help="실행당 제한 초(기본 180)")
    ap.add_argument("--model", help="claude --model 값")
    ap.add_argument("--fake", help="합성 결과 map.json(러너 자체 시험)")
    ap.add_argument("--rescore", help="기존 결과 폴더를 현재 세트로 재채점")
    ap.add_argument("--legacy-queries", help="--rescore 대상에 keys.json이 없을 때 쓸 옛 queries.json(행 순서로 키 복원)")
    a = ap.parse_args(argv)

    only = set(a.skill) if a.skill else None
    rows = load_rows(only)
    errs = check_rows(rows, full=not only)
    if a.check or errs:
        pos = sum(r["should_trigger"] is True for r in rows)
        print(f"세트 {len({r['skill'] for r in rows})}개 · 행 {len(rows)}(양성 {pos}·음성 {len(rows) - pos}) · "
              f"고유 질의 {len({r['query'] for r in rows})}")
        for e in errs:
            print("ERROR", e)
        if errs:
            print(f"형식 오류 {len(errs)}건")
            return 1
        print("형식 검사 통과")
        return 0

    if a.rescore:
        out = Path(a.rescore).resolve()
        if out == REPO or REPO in out.parents:
            print(f"--rescore 폴더는 플러그인 폴더 밖이어야 한다: {out}")
            return 2
        kfile = out / "keys.json"
        if kfile.exists():
            keymap = json.load(open(kfile, encoding="utf-8"))
        elif a.legacy_queries:
            keymap = {}
            for x in json.load(open(a.legacy_queries, encoding="utf-8")):
                keymap.setdefault(x["query"], f"q{len(keymap):03d}")
        else:
            print("keys.json이 없다 — --legacy-queries로 옛 queries.json을 준다")
            return 2
        known = set(keymap)
        assign_keys(rows, keymap)
        results = {}
        for r in rows:
            if r["query"] in known and r["key"] not in results:
                results[r["key"]] = load_runs(out / "runs", r["key"])
        for r in rows:
            rs = results.get(r["key"])
            r["verdict"], r["note"] = judge(r, rs) if rs else ("SKIP", "기록 없음(질의 변경·신규)")
        dst = out / "rescore"
        dst.mkdir(exist_ok=True)
        summ = summarize(rows, results)
        json.dump(summ, open(dst / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(write_outputs(dst, rows, results))
        print(json.dumps(summ["totals"], ensure_ascii=False))
        return 0

    out = Path(a.out).resolve() if a.out else Path(tempfile.mkdtemp(prefix="wk-ko-legal-trigger-"))
    if out == REPO or REPO in out.parents:
        print(f"--out은 플러그인 폴더 밖이어야 한다: {out}")
        return 2
    fake = json.load(open(a.fake, encoding="utf-8")) if a.fake else None
    keymap = assign_keys(rows)
    uniq = list(dict.fromkeys(r["query"] for r in rows))
    if a.limit:
        uniq = uniq[:a.limit]
    runner = Runner(out, a.timeout, a.model, fake)
    json.dump(keymap, open(out / "keys.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    results = {}

    pre = runner.run_one(keymap[uniq[0]], uniq[0], 1)
    results[keymap[uniq[0]]] = [pre]
    meta = {"fake": fake is not None, "repo": str(REPO), "preflight_error": pre.get("error"),
            "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    if pre.get("error") in AUTH_ERRORS:
        note = f"SKIP: claude -p 사전 점검 실패({pre.get('error')}) — 모델 호출 불가"
        for r in rows:
            r["verdict"], r["note"] = "SKIP", note
        write_outputs(out, rows, results)
        meta["summary"] = summarize(rows, results)
        json.dump(meta, open(out / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(note)
        return 2

    with ThreadPoolExecutor(a.par) as ex:
        futs = {q: ex.submit(runner.run_one, keymap[q], q, 1) for q in uniq[1:]}
        for q, fu in futs.items():
            results[keymap[q]] = [fu.result()]

    def needs_more(q):
        k = keymap[q]
        rs = results[k]
        v = valid(rs)
        if len(rs) >= MAX_TRIES or len(v) >= WANT_VALID:
            return False
        if not v:                      # 오류만 → 재시도
            return True
        mine = [r for r in rows if r["key"] == k]
        if len(v) == 1:                # 첫 유효 실행이 어느 행과든 어긋날 때만 추가
            return any(not verdict_one(r, v[0]["skill"]) for r in mine)
        return True                    # 2회 이상 유효 실행이 시작됐으면 3회까지 채운다

    while True:
        todo = [q for q in uniq if needs_more(q)]
        if not todo:
            break
        with ThreadPoolExecutor(a.par) as ex:
            futs = {q: ex.submit(runner.run_one, keymap[q], q, len(results[keymap[q]]) + 1) for q in todo}
            for q, fu in futs.items():
                results[keymap[q]].append(fu.result())

    for r in rows:
        rs = results.get(r["key"])
        r["verdict"], r["note"] = judge(r, rs) if rs else ("SKIP", "미실행(--limit)")
    meta["models"] = sorted({x.get("model") or "" for v in results.values() for x in v})
    meta["summary"] = summarize(rows, results)
    json.dump(meta, open(out / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(write_outputs(out, rows, results))
    print(json.dumps(meta["summary"]["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
