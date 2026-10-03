#!/usr/bin/env python3
"""hwpx 변환기(shared/hwpx/render.py) 회귀.

  python3 tools/hwpx_regress.py                 기본프로필·기본양식으로 가상 초안 변환 + 구조 단언
  python3 tools/hwpx_regress.py --profile DIR   로컬 프로필로 같은 검사(+ 프로필 수치 단언)
  python3 tools/hwpx_regress.py --md 초안.md…    실제 초안 변환 시험 — 결과 파일은 임시 폴더, 출력은 건수만(내용 비표시)

한글에서의 쪽 나눔·표 폭은 검증하지 못한다 — 릴리스 전 한글로 한 번 열어 본다(CHANGELOG에 기록).
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "hwpx"))
import render  # noqa: E402

DEFAULT = str(ROOT / "shared" / "hwpx" / "기본프로필.json")


def png(w, h, rgb=(200, 210, 230)):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


CASES = {
    "준비서면": ("""# 준 비 서 면

사    건   2025가합123456 대여금
원    고   홍길동
피    고   김철수

위 사건에 관하여 피고의 소송대리인은 다음과 같이 변론을 준비합니다.

**다        음**

## 1. 원고 주장의 요지

원고는 2024. 3. 2. 피고에게 50,000,000원을 대여하였다고 주장합니다.

## 2. 피고의 변제 사실

### 가. 송금 내역

| 순번 | 송금일 | 금액 | 메모 |
|:---:|:---:|---:|:---|
| 1 | 2024. 6. 30. | 5,000,000원 | 대여금 일부 변제 |
| 2 | 2024. 8. 31. | 3,000,000원 | 대여금 일부 변제 |
| 합계 | | 8,000,000원 | |

송금 메모에는 **"대여금 일부 변제"**라고 적혀 있습니다.

![을 제2호증 거래내역](g1.png)

### 나. 변제의 효과

#### 1) 관련 법리

> 가상 인용문입니다.

##### (1) 세부

###### (가) 더 세부

① 첫째, ② 둘째.

## 3. 결론

그렇다면 원고의 청구는 이유 없습니다.

**증 명 방 법**

1. 을 제2호증 거래내역
2. 을 제3호증 거래내역

**첨 부 서 류**

(변호사 확인 후 기재 — 민사소송규칙 제2조 제1항 제3호)

2026. 10. 3.

피고 소송대리인
법무법인 ○○
담당변호사 ○○○

서울중앙지방법원 제12민사부 귀중

---

# [별지] 변제 내역

## 1. 1차 송금

2024. 6. 30. 송금.

## 2. 2차 송금

2024. 8. 31. 송금.
""", {"side": "피고측", "outline": {1: 3, 2: 2, 3: 1, 4: 0, 5: 1, 6: 1}, "tables": 1, "pics": 1, "annex": 2, "warn": 0}),
    "신청서": ("""채권가압류신청서

채    권    자   주식회사 갑
                 서울 서초구 ○○로 1
채    무    자   을
제3채무자   주식회사 병은행

**신 청 취 지**

1. 채무자의 제3채무자에 대한 별지 목록 기재 채권을 가압류한다.
2. 제3채무자는 채무자에게 위 채권에 관한 지급을 하여서는 아니 된다.

라는 결정을 구합니다.

**신 청 이 유**

1. 피보전권리

채권자는 채무자에게 1억 원을 대여하였습니다.

3. 보전의 필요성

채무자는 재산을 처분하고 있습니다.

2026. 10. 3.

채권자 소송대리인
법무법인 ○○
담당변호사 ○○○

서울중앙지방법원 귀중
""", {"side": "원고측", "outline": {1: 2}, "tables": 0, "pics": 0, "annex": 0, "warn": 1}),
    "의견서": ("""의 견 서

사    건   2026가단1234 손해배상(기)
원    고   홍길동
피    고   주식회사 갑
            대표이사 을
            서울 강남구 ○○로 2

위 사건에 관하여 원고의 소송대리인은 다음과 같이 의견을 진술합니다.

**의 견 요 지**

1. 감정 신청을 채택하여 주시기 바랍니다.

2. 기일을 속행하여 주시기 바랍니다.

**이 유**

1. 감정의 필요성

가. 손해액은 전문 지식이 필요합니다.

나. 다른 증거가 없습니다.

2. 속행의 필요성

가. 감정 결과가 필요합니다.

2026. 10. 3.

원고 소송대리인
법무법인 ○○
담당변호사 ○○○

○○지방법원 귀중
""", {"side": "원고측", "outline": {1: 4, 2: 3}, "tables": 0, "pics": 0, "annex": 0, "warn": 0, "segments": 2}),
}

REJECT = {
    "작성 메모": "# 준비서면\n\n> **초안 v1 (검토 필요)**\n\n사    건   2026가단1\n\n1. 가\n\n2026. 1. 1.\n\n○○법원 귀중\n",
    "검토 메모": "# 준비서면\n\n사    건   2026가단1\n\n1. 가\n\n2026. 1. 1.\n\n○○법원 귀중\n\n## [검토 1] 쟁점\n- 메모\n",
    "부분 초안": "## 2. 쟁점\n\n본문입니다.\n",
}


def outline_counts(X, H):
    lv = {}
    heads = {}
    for m in re.finditer(r'<hh:paraPr id="(\d+)"[ >].*?</hh:paraPr>', H, re.S):
        h = re.search(r'<hh:heading type="(OUTLINE|NUMBER)" idRef="\d+" level="(\d+)"', m.group(0))
        if h:
            heads[m.group(1)] = int(h.group(2)) + 1
    for a, b in render.top_paras(X):
        p = X[a:b]
        pid = re.search(r'paraPrIDRef="(\d+)"', p).group(1)
        if pid in heads and ''.join(re.findall(r"<hp:t>([^<]*)", p)).strip():
            lv[heads[pid]] = lv.get(heads[pid], 0) + 1
    return lv


def run_case(name, md, exp, prof_path, tmp):
    d = Path(tmp) / name
    d.mkdir()
    (d / "g1.png").write_bytes(png(400, 200))
    src = d / f"{name}.md"
    src.write_text(md, encoding="utf-8")
    out = d / f"{name}.hwpx"
    prof = render.load_profile(prof_path)
    doc = render.parse_md(md, prof)
    side = render.detect_side(doc, prof)
    errs = []
    if side != exp["side"]:
        errs.append(f"측 판단 {side} ≠ {exp['side']}")
    tpl = render.pick_template(prof, side)
    r = render.Renderer(tpl, prof, str(d)).build(doc)
    r.save(str(out))
    errs += render.check(str(out), md, prof)
    if len(doc["warnings"]) != exp["warn"]:
        errs.append(f"경고 {len(doc['warnings'])}건 ≠ {exp['warn']} {doc['warnings']}")
    z = zipfile.ZipFile(out)
    X = z.read("Contents/section0.xml").decode("utf-8")
    H = z.read("Contents/header.xml").decode("utf-8")
    oc = outline_counts(X, H)
    oc[1] = oc.get(1, 0) - exp["annex"]            # 별지 개요 1은 따로 센다
    for k, v in exp["outline"].items():
        if oc.get(k, 0) != v:
            errs.append(f"개요 {k} {oc.get(k, 0)} ≠ {v}")
    if X.count("<hp:tbl ") != exp["tables"]:
        errs.append(f"표 {X.count('<hp:tbl ')} ≠ {exp['tables']}")
    if X.count("<hp:pic ") != exp["pics"]:
        errs.append(f"그림 {X.count('<hp:pic ')} ≠ {exp['pics']}")
    if X.count("<hp:footer ") != 1 or "TOTAL_PAGE" not in X:
        errs.append("꼬리말(쪽 번호) 보존 실패")
    first = X[slice(*render.top_paras(X)[0])]
    if re.search(r"[가-힣] [가-힣]", "".join(re.findall(r"<hp:t>([^<]*)", first))):
        errs.append("표제 글자 사이 공백 미정리")
    if not re.search(r"<hp:t>[^<]{1,3}<hp:tab ", X):
        errs.append("당사자 줄 탭 배분 없음")
    kw = set(re.findall(r'<hh:paraPr id="(\d+)"[^>]*>(?:(?!</hh:paraPr>).)*keepWithNext="1"', H, re.S))
    court = [X[a:b] for a, b in render.top_paras(X) if "귀중" in X[a:b]][0]
    sig = [X[a:b] for a, b in render.top_paras(X)]
    ci = sig.index(court)
    sig_text = "".join(re.findall(r"<hp:t>([^<]*)", "".join(sig[ci - 5:ci])))
    if "○○" in sig_text and prof["signers"].get("firm"):
        errs.append("서명 자리표시가 프로필로 채워지지 않음")
    if not all(re.search(r'paraPrIDRef="(\d+)"', p).group(1) in kw for p in sig[ci - 4:ci]):
        errs.append("서명 블록 keepWithNext 누락")
    if exp["annex"] and 'type="NUMBER"' not in H:
        errs.append("별지 번호 재시작 없음")
    nseg = len(re.findall(r"<hh:numbering id=", H)) - 1
    if "segments" in exp and nseg != exp["segments"] - 1:
        errs.append(f"번호열 {nseg + 1} ≠ {exp['segments']}")
    if 2 in exp["outline"] and len(set(re.findall(r'level="1"', H))) == 0:
        errs.append("개요 2 없음")
    parties = [X[a:b] for a, b in render.top_paras(X) if "<hp:lineBreak/>" in X[a:b]]
    if name == "의견서" and not parties:
        errs.append("당사자 이음 줄(줄바꿈) 없음")
    return errs, prof, X, H


def profile_asserts(prof, X, H):
    errs = []
    tp = prof["table"]
    if X.count("<hp:tbl "):
        widths = set(re.findall(r'<hh:(?:left|top)Border type="SOLID" width="([\d.]+ mm)"', H))
        for w in {tp["outer"], tp["inner"]}:
            if w not in widths:
                errs.append(f"표 선 {w} 없음")
        if f'cellMargin left="{tp["cell_margin"]}"' not in X:
            errs.append("셀 여백 불일치")
    pp = prof["picture"]
    if X.count("<hp:pic "):
        if pp["caption_native"] != ("<hp:caption " in X):
            errs.append("캡션 방식 불일치")
        if bool(pp["border_hwpunit"]) != ("<hp:lineShape " in X):
            errs.append("그림 테두리 불일치")
    if "underline" in prof["body"]["emphasis"] and 'underline type="BOTTOM"' not in H:
        errs.append("강조 밑줄 없음")
    return errs


def main():
    args = sys.argv[1:]
    prof_path = DEFAULT
    if "--profile" in args:
        prof_path = render.find_profile(args[args.index("--profile") + 1])
    if "--md" in args:
        files = [a for a in args[args.index("--md") + 1:] if not a.startswith("--")]
        prof = render.load_profile(render.find_profile(None, [os.path.dirname(os.path.abspath(files[0]))]) if prof_path == DEFAULT else prof_path)
        bad = 0
        with tempfile.TemporaryDirectory() as tmp:
            for i, f in enumerate(files):
                md = Path(f).read_text(encoding="utf-8")
                try:
                    doc = render.parse_md(md, prof)
                    side = render.detect_side(doc, prof)
                    out = os.path.join(tmp, f"{i}.hwpx")
                    render.Renderer(render.pick_template(prof, side), prof, os.path.dirname(os.path.abspath(f))).build(doc).save(out)
                    probs = render.check(out, md, prof)
                    print(f"[{i}] 측={side or '-'} 경고 {len(doc['warnings'])}건 · 오류 {len(probs)}건")
                    bad += bool(probs)
                except render.RenderError as e:
                    print(f"[{i}] 변환 실패({type(e).__name__})"); bad += 1
        print("FAIL" if bad else "OK", f"{len(files) - bad}/{len(files)}")
        return 1 if bad else 0
    fails = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, (md, exp) in CASES.items():
            errs, prof, X, H = run_case(name, md, exp, prof_path, tmp)
            errs += profile_asserts(prof, X, H)
            print(("OK  " if not errs else "FAIL") + f" {name}" + ("" if not errs else ": " + " / ".join(errs)))
            fails += bool(errs)
        # 작업본(메모·검토 논평·부분 초안) 거부
        for nm, md in REJECT.items():
            try:
                render.parse_md(md, render.load_profile(prof_path))
                print(f"FAIL 작업본 거부 — {nm}"); fails += 1
            except render.RenderError:
                print(f"OK   작업본 거부 — {nm}")
        # 같은 이름 덮어쓰기 거부
        d = Path(tmp) / "준비서면"
        rc = render.main(["build", str(d / "준비서면.md"), "--profile", prof_path])
        print(("OK  " if rc == 2 else "FAIL") + " 기존 hwpx 덮어쓰기 거부")
        fails += rc != 2
        # 표지 넣기(mark) 왕복: 양식의 표지를 견본 글로 되돌린 사본 → mark → 표지 10종
        tpl = render.pick_template(render.load_profile(prof_path), "원고측")
        sample = {"표제": "준비서면", "모두문": "  위 사건에 관하여 원고의 소송대리인은 다음과 같이 변론을 준비합니다.",
                  "다음": "다 음", "본문": "일", "날짜": "2026. . .", "대리인": "원고의 소송대리인",
                  "법인": "법무법인 ○○", "변호사": "담당변호사 ○○○", "법원": "○○지방법원 귀중"}
        zt = zipfile.ZipFile(tpl)
        raw = Path(tmp) / "raw.hwpx"
        with zipfile.ZipFile(raw, "w") as zo:
            for info in zt.infolist():
                data = zt.read(info.filename)
                if info.filename == "Contents/section0.xml":
                    t = data.decode("utf-8")
                    t = t.replace("<hp:t>{{당사자}}", "<hp:t>사")
                    for k, v in sample.items():
                        t = t.replace("{{" + k + "}}", v)
                    data = t.encode("utf-8")
                zo.writestr(info, data, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
        found = render.mark_template(str(raw), str(Path(tmp) / "m.hwpx"))
        ok = set(found) == set(render.MARKERS)
        print(("OK  " if ok else "FAIL") + f" mark 왕복 표지 {len(found)}/{len(render.MARKERS)}종" + ("" if ok else f" {found}"))
        fails += not ok
    print("FAIL" if fails else "OK", "hwpx 회귀")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
