#!/usr/bin/env python3
"""ko-law-api 스크립트(law_api.py) 회귀 검사 — 표준 라이브러리만 사용.

    python3 tools/law_api_regress.py            # 오프라인 검사만 (키 불필요)
    python3 tools/law_api_regress.py --live     # + 실제 API 재현 검사 (키 필요, 격리 캐시 사용)

라이브 검사는 2026-09-25 점검에서 재현한 결함 사례를 고정한다. 법령 데이터가 바뀌면(개정·시행)
기대값이 달라질 수 있으므로, 실패하면 먼저 사례 자체가 여전히 유효한지 확인한다.
출력에는 인증키를 쓰지 않는다 — 키 노출 여부는 개수만 센다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT_DIR = os.path.join(ROOT, "skills", "ko-law-api", "scripts")
SCRIPT = os.path.join(SCRIPT_DIR, "law_api.py")
sys.path.insert(0, SCRIPT_DIR)
import law_api as L  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
BASE_ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def exits(fn, *a) -> int | None:
    """fn(*a)가 sys.exit로 끝나면 그 코드, 정상 반환이면 None."""
    try:
        fn(*a)
    except SystemExit as e:
        return e.code
    return None


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


# ---------------------------------------------------------------------------
# 오프라인
# ---------------------------------------------------------------------------

def offline() -> None:
    check("encode_jo 가지조", L.encode_jo("제10조의2") == "001002")
    check("encode_jo 일반", L.encode_jo("390") == "039000")
    check("encode_jo 조+항은 조까지(제71조로 오독 금지)", L.encode_jo("제7조제1항") == "000700"
          and L.encode_jo("제10조의2제3항") == "001002")
    check("encode_jo 해석 불가 → exit 2", exits(L.encode_jo, "390-2") == 2)
    check("encode_jo '조' 없는 항·호 → exit 2(제1조로 오독 금지)",
          all(exits(L.encode_jo, x) == 2 for x in ("제1항", "1항", "제2호")))
    check("encode_jo 목·단서 꼬리", L.encode_jo("제3조제2항제1호가목") == "000300" and L.encode_jo("제390조 단서") == "039000")
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        for x in ("039000", "03900100", "390"):
            L._jo_note(x)
    check("숫자 입력에는 항·호 NOTE 없음", buf.getvalue() == "", buf.getvalue())

    check("날짜: 0 채움('2020. 6. 1.')", L._parse_date("2020. 6. 1.") == "20200601"
          and L._parse_date("2020-06-01") == L._parse_date("20200601") == "20200601")
    check("날짜: 자릿수 부족·없는 날짜 → exit 2", exits(L._parse_date, "202061") == 2
          and exits(L._parse_date, "20151340") == 2)

    url = "https://www.law.go.kr/DRF/lawSearch.do?OC=secretkey&target=law"
    body = "<LawSearch><법령상세링크>/DRF/lawService.do?OC=secretkey&amp;target=law</법령상세링크></LawSearch>"
    check("본문 echo OC 마스킹", "secretkey" not in L._mask_oc_in_body(body, url))
    html = '<input type="hidden" id="OC" name="OC" value="secretkey" />'
    check("HTML 숨은 입력란의 키 마스킹", "secretkey" not in L._mask_oc_in_body(html, url))

    base = {"공포일자": "20240920", "공포번호": "20432", "명칭": "민법", "시행일자": "20250131", "법령구분": "법률"}
    _, phrase, _ = L._precedent_phrase(base, {**base, "시행일자": "20260101"}, "law")
    check("판례식: 단계적 시행분", phrase == "구 민법(2024. 9. 20. 법률 제20432호로 개정되어 2026. 1. 1. 시행되기 전의 것)",
          phrase)
    _, phrase, _ = L._precedent_phrase(base, {**base, "공포일자": "20250401", "공포번호": "20500"}, "law")
    check("판례식: 일반 개정", phrase == "구 민법(2025. 4. 1. 법률 제20500호로 개정되기 전의 것)", phrase)
    o = {"공포일자": "20150622", "공포번호": "2465", "명칭": "가평군 옥외광고물 등 관리 조례",
         "시행일자": "20150622", "법령구분": "조례", "지자체": "경기도 가평군"}
    _, phrase, _ = L._precedent_phrase({**o, "공포번호": "2400"}, o, "ordin")
    check("판례식: 자치법규 표기", "경기도가평군조례 제2465호" in phrase, phrase)
    a = {"공포일자": "20221123", "공포번호": "2022-44", "명칭": "전자금융감독규정", "시행일자": "20230101",
         "법령구분": "고시", "소관부처": "금융위원회"}
    _, phrase, _ = L._precedent_phrase({**a, "공포번호": "2018-36"}, a, "admrul", [])
    check("판례식: 행정규칙 표기", "금융위원회고시 제2022-44호" in phrase, phrase)

    # 공포(발령)번호 정규화 — 앞자리 0 제거, 행정규칙 자리표시 9999 생략(법령 9999는 실제 번호)
    lw = lambda d, no, eff, name="행정소송법": {"공포일자": d, "공포번호": no, "명칭": name, "시행일자": eff,
                                             "법령구분": "법률"}
    _, phrase, _ = L._precedent_phrase(lw("19940727", "04770", "19980301"), lw("20020126", "06627", "20020701"), "law")
    check("번호 정규화: 06627 → 제6627호", phrase == "구 행정소송법(2002. 1. 26. 법률 제6627호로 개정되기 전의 것)", phrase)
    sa = {"공포일자": "20240913", "공포번호": "9999", "명칭": "전자금융감독규정시행세칙", "시행일자": "20240915",
          "법령구분": "세칙", "소관부처": "금융감독원"}
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        head, phrase, _ = L._precedent_phrase({**sa, "공포일자": "20221229", "시행일자": "20230101"}, sa, "admrul", [])
    check("번호 정규화: 행정규칙 9999 → 종류·번호 생략",
          phrase == "구 전자금융감독규정시행세칙(2024. 9. 13. 개정되기 전의 것)" and "9999" not in head
          and "자리표시" in buf.getvalue(), phrase + head)
    _, phrase, _ = L._precedent_phrase(lw("20091231", "9800", "20100101", "X"), lw("20100125", "9999", "20100126", "X"),
                                       "law")
    check("번호 정규화: 법령 9999는 유지", "법률 제9999호로 개정되기 전의 것" in phrase, phrase)
    check("번호 정규화: 그 밖의 값", [L._norm_prom_no(v, t) for v, t in (("00968", "law"), ("2022-44", "admrul"),
          ("none", "ordin"), ("", "law"), ("9999", "ordin"))] == ["968", "2022-44", "", "", "9999"])
    txt = L._body_text("<법령><기본정보><법령명_한글>행정소송법</법령명_한글><공포일자>20090210</공포일자>"
                       "<공포번호>09359</공포번호></기본정보></법령>") or ""
    txt2 = L._body_text("<AdmRulService><행정규칙기본정보><행정규칙명>세칙</행정규칙명><발령일자>20221229</발령일자>"
                        "<발령번호>9999</발령번호></행정규칙기본정보></AdmRulService>") or ""
    check("--text 머리행 번호 정규화", txt == "행정소송법 · 공포 20090210 · 제9359호" and txt2 == "세칙 · 발령 20221229",
          txt + " / " + txt2)

    # 단계적 시행·공포 순서 역전 판별(A 같은 개정 / B 선택본 전 일부 시행 / C 먼저 공포·뒤에 시행 / 그 밖 일반형)
    hl = lambda d, no, eff: lw(d, no, eff, "주택임대차보호법")
    rows = [hl("20230418", "19356", "20230418"), hl("20230711", "19520", "20230711"), hl("20230719", "19356", "20230719")]
    rows[2]["공포일자"] = "20230418"
    head, phrase, kind = L._precedent_phrase(rows[1], rows[2], "law", rows)
    check("판례식 B: 선택본 전에 일부 시행된 개정의 나머지",
          kind == "B" and phrase == "구 주택임대차보호법(2023. 4. 18. 법률 제19356호로 개정되어 2023. 7. 19. 시행되기 전의 것)",
          phrase)
    _, phrase, kind = L._precedent_phrase(lw("20250814", "21016", "20260101", "도로교통법"),
                                          lw("20250401", "20864", "20260402", "도로교통법"), "law", [])
    check("판례식 C: 먼저 공포·뒤에 시행",
          kind == "C" and phrase == "구 도로교통법(2025. 4. 1. 법률 제20864호로 개정되어 2026. 4. 2. 시행되기 전의 것)", phrase)
    _, phrase, kind = L._precedent_phrase(lw("20011229", "6541", "20020101"), lw("20020126", "6627", "20020701"),
                                          "law", [])
    check("판례식 D: 뒤에 공포된 개정은 일반형", kind == "" and phrase.endswith("법률 제6627호로 개정되기 전의 것)"), phrase)
    _, _, kind = L._precedent_phrase(lw("20200101", "17000", "20200101"), lw("20200101", "16999", "20210101"), "law", [])
    check("판례식 C: 같은 날 공포·번호가 작으면", kind == "C")
    _, _, k1 = L._precedent_phrase({**a, "공포번호": "2022-45"}, {**a, "시행일자": "20230201"}, "admrul", [])
    ph = {**o, "공포일자": "00000000", "공포번호": "none", "시행일자": "99991231"}
    _, _, k2 = L._precedent_phrase(o, ph, "ordin", [o, ph])
    check("판례식: 숫자 아닌 번호·자리표시 행은 C로 보지 않음", k1 == "" and k2 == "", f"{k1!r} {k2!r}")

    # 자리표시 연혁 행(시행일자 99991231·공포일자 00000000·공포번호 none) — 선택·직후 개정·계통표에서 제외(LAW-05)
    gn = {"명칭": "서울특별시 강남구 옥외광고물 등의 관리와 옥외광고산업 진흥에 관한 조례", "계통ID": "2072455",
          "시행일자": "20250321", "공포일자": "20250321", "공포번호": "2031", "MST": "2023185", "구분": "현행"}
    gph = {**gn, "명칭": "서울특별시 강남구 옥외광고물 등 관리 조례", "시행일자": "99991231", "공포일자": "00000000",
           "공포번호": "none", "MST": "915155", "구분": "연혁"}
    pick, nxt, ex = L._pick_versions(L._dedupe_sort([dict(gn), dict(gph)]), "20251001")
    check("자리표시: 직후 개정 없음·제외 1건", pick and pick["MST"] == "2023185" and nxt is None and ex == 1,
          f"{pick} {nxt} {ex}")
    gg = {"명칭": "경기도옥외광고물등관리조례", "계통ID": "2024478", "시행일자": "20060630", "공포일자": "20060630",
          "공포번호": "3529", "MST": "873196", "구분": "연혁"}
    pick, nxt, ex = L._pick_versions([gg, {**gg, "시행일자": "99991231", "공포일자": "19910207", "공포번호": "2104",
                                           "MST": "1"}], "20260926")
    check("자리표시: 시행일만 99991231인 행도 제외(시행기간 끝 99991230 금지)",
          pick and pick["MST"] == "873196" and nxt is None and ex == 1, f"{nxt}")
    tab = L._lineage_table(L._group_lineages([dict(gn), dict(gph)])).splitlines()
    check("자리표시: 계통표 최신 시행일·명칭", tab[1:] == ["2072455 | 20250321 | 2 | " + gn["명칭"]
          + " (옛 명칭: 서울특별시 강남구 옥외광고물 등 관리 조례)"], tab)
    pick, nxt, ex = L._pick_versions([gph], "20251001")
    check("자리표시: 모두 자리표시면 선택본 없음", pick is None and ex == 1)
    # 선택본이 없을 때 기준일 전에 공포된 자리표시 행이 있으면 '제정 전'이 아니다(검증 보정)
    ggp = [gg] + [{**gg, "시행일자": "99991231", "공포일자": d, "공포번호": n, "MST": m}
                  for d, n, m in (("19980629", "2827", "2049921"), ("19910207", "2104", "887670"),
                                  ("20020629", "3197", "887679"))]
    early = L._placeholder_before(ggp, "20000101")
    check("자리표시: 기준일 전 공포 행 — 공포일 오름차순, 기준일 뒤 공포는 제외",
          [r["MST"] for r in early] == ["887670", "2049921"] and L._placeholder_before(ggp, "19900101") == []
          and L._placeholder_before([gph], "20251001") == [], [r["MST"] for r in early])
    # 폐지 판본(제개정구분명 폐지·타법폐지) — 판례식 '…로 폐지되기 전의 것'(대법원 2018도1966)
    pv = {"명칭": "공공기관의 개인정보보호에 관한 법률", "시행일자": "20100505", "공포일자": "20100204",
          "공포번호": "10012", "MST": "102478", "법령구분": "법률", "제개정": "타법개정"}
    rv = {**pv, "시행일자": "20110930", "공포일자": "20110329", "공포번호": "10465", "MST": "111344", "제개정": "타법폐지"}
    _, ph_r, k_r = L._precedent_phrase(pv, rv, "law", [pv, rv])
    check("폐지: 판례식 '폐지되기 전의 것'", ph_r == "구 공공기관의 개인정보보호에 관한 법률(2011. 3. 29. 법률 제10465호로 "
          "폐지되기 전의 것)" and k_r == "" and L._repealed(rv) and L._repealed({"제개정": "폐지"})
          and not L._repealed(pv), ph_r)

    # 조문 문언 대조 서명 — 공백·개정 표지 무시, 편·장 제목 제외(LAW-04)
    art = ("<법령><조문><조문단위><조문여부>전문</조문여부><조문내용>제39장 사기와 공갈의 죄</조문내용></조문단위>"
           "<조문단위><조문여부>조문</조문여부><조문내용>제347조(사기)</조문내용><항><항내용><![CDATA[{}]]></항내용></항></조문단위>"
           "</조문></법령>")
    s1 = L._article_sig(art.format("①사람을 기망하여 … 10년 이하의 징역 <개정 1995.12.29>"))
    s2 = L._article_sig(art.format("① 사람을 기망하여 …  10년 이하의 징역 <개정 1995. 12. 29.>").replace("제39장", "제40장"))
    s3 = L._article_sig(art.format("① 사람을 기망하여 … 20년 이하의 징역 <개정 2025.12.23>"))
    check("조문 대조: 공백·개정 표지·장 제목 무시, 문언 차이는 구분", s1 == s2 and s1 != s3 and s1,
          f"{s1} / {s2} / {s3}")
    check("조문 대조: 조문 없는 응답 → None", L._article_sig("<법령><기본정보/></법령>") is None)

    # --addenda — <부칙단위>에서 공포번호가 맞는 부칙만(LAW-R02)
    adx = ("<법령><기본정보/><부칙>"
           "<부칙단위 부칙키='2007122108730'><부칙공포일자>20071221</부칙공포일자><부칙공포번호>08730</부칙공포번호>"
           "<부칙내용><![CDATA[부칙 <제8730호,2007.12.21>]]>\n<![CDATA[]]>\n<![CDATA[제3조 (공소시효에 관한 경과조치) "
           "이 법 시행 전에 범한 죄에 대하여는 종전의 규정을 적용한다.]]>\n<![CDATA[]]></부칙내용></부칙단위>"
           "<부칙단위 부칙키='2015073113454'><부칙공포일자>20150731</부칙공포일자><부칙공포번호>13454</부칙공포번호>"
           "<부칙내용><![CDATA[부칙 <제13454호,2015.7.31>]]>\n<![CDATA[제2조(공소시효의 적용 배제에 관한 경과조치) "
           "제253조의2의 개정규정은 …]]></부칙내용></부칙단위></부칙></법령>")
    u1 = L._addenda_units(adx, ["8730"])
    u2 = L._addenda_units(adx, ["08730"])
    check("--addenda 번호 일치 부칙만·앞자리 0 무시·CDATA 이어 붙임",
          u1 == u2 and len(u1) == 1 and "공소시효에 관한 경과조치" in u1[0] and "13454" not in u1[0]
          and u1[0].startswith("부칙 <제8730호,2007.12.21>\n제3조") and "\n\n" not in u1[0], u1)
    check("--addenda 번호 없으면 전부·번호 해석", len(L._addenda_units(adx)) == 2
          and L._addenda_nums(["8730,13454", "제20795호"]) == ["8730", "13454", "20795"])
    skill = open(os.path.join(ROOT, "skills", "ko-law-api", "SKILL.md"), encoding="utf-8").read()
    check("SKILL.md 옛 문구 없음(절대 혼용·부칙 확인 권고)", "절대 혼용하지 않는다" not in skill
          and "(부칙 확인 권고)" not in skill)

    # 공포본(target=law) → 시행일 기준 본문(eflaw) 요청 파라미터
    import argparse
    lb = "<법령><기본정보><법령ID>001700</법령ID><시행일자>20250712</시행일자></기본정보>{}</법령>"
    ns = lambda **k: argparse.Namespace(**{"mst": None, "id": None, "lm": None, **k})
    for label, body in (("본문", lb.format("<조문><조문단위><조문내용>제1조</조문내용></조문단위></조문>")),
                        ("빈 JO 응답", lb.format(""))):
        check(f"law → eflaw 파라미터({label})",
              L._law_effective_params(body, ns(mst="252393")) == {"target": "eflaw", "MST": "252393", "efYd": "20250712"}
              and L._law_effective_params(body, ns(lm="민사소송법")) == {"target": "eflaw", "ID": "001700"}
              and L._law_effective_params(body, ns(id="001700")) == {"target": "eflaw", "ID": "001700"})

    # --byl — 본문 <별표단위>에서 별표·서식 하나
    bylx = ("<법령><기본정보/><별표>"
            "<별표단위 별표키='000302E'><별표번호>0003</별표번호><별표가지번호>02</별표가지번호><별표구분>별표</별표구분>"
            "<별표제목>행정처분의 기준(제33조제2항 관련)</별표제목><별표시행일자>20231212</별표시행일자>"
            "<별표서식파일링크>/LSW/flDownload.do?flSeq=1</별표서식파일링크>"
            "<별표내용><![CDATA[■ [별표 3의2]    \n\n        \n\n  1. 일반기준   \n\n  가. 첫 줄\n\n  이어짐]]></별표내용></별표단위>"
            "<별표단위><별표번호>0003</별표번호><별표가지번호>00</별표가지번호><별표구분>서식</별표구분>"
            "<별표제목>신고서</별표제목><별표내용>[별지 제3호서식]</별표내용></별표단위></별표></법령>")
    out = L._byl_extract(bylx, "별표 3의2", "별표")
    check("--byl 별표 가지번호·머리 한 줄·내용 정리",
          out == ("[별표 3의2] 행정처분의 기준(제33조제2항 관련) · 별표시행일자 20231212 · "
                  "https://www.law.go.kr/LSW/flDownload.do?flSeq=1\n\n■ [별표 3의2]\n\n  1. 일반기준\n  가. 첫 줄\n  이어짐"),
          out)
    check("--byl 서식", L._byl_extract(bylx, "3", "서식").startswith("[별지 제3호서식] 신고서"))
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        code = exits(L._byl_extract, bylx, "3", "별표")
    check("--byl 없는 번호 → 목록과 exit 2", code == 2 and "[별표 3의2] 행정처분의 기준" in buf.getvalue(), buf.getvalue())
    with contextlib.redirect_stderr(io.StringIO()):
        code = exits(L._parse_byl, "abc")
    check("--byl 번호 해석", L._parse_byl("23") == (23, 0) and L._parse_byl("[별표 4의2]") == (4, 2) and code == 2)

    nf = '<?xml version="1.0" encoding="utf-8"?><Law>일치하는 법령이 없습니다.  법령명을 확인하여 주십시오.</Law>'
    check("일치 없음 XML 감지", bool(L._find_not_found(nf)))
    check("일치 없음 JSON 감지", bool(L._find_not_found('{"Law": "일치하는 행정규칙이 없습니다."}')))
    empty_search = ("<LawSearch><target>law</target><totalCnt>0</totalCnt><resultCode>00</resultCode>"
                    "<resultMsg>success</resultMsg></LawSearch>")
    check("검색 0건은 오류 아님", L._find_not_found(empty_search) is None)

    head_future = ("<법령><기본정보><법령ID>010199</법령ID><시행일자>20261217</시행일자>"
                   "<조문시행일자문자열>20251216:제42조제3항</조문시행일자문자열></기본정보></법령>")
    check("미시행 판별: 본문 시행일자", "20261217" in L._pending_info(head_future, today="20260925"))
    part_future = ("<법령><기본정보><시행일자>20260911</시행일자><조문시행일자문자열>"
                   "20270701:제32조의2제1항 단서,제75조제2항제15호</조문시행일자문자열></기본정보></법령>")
    info = L._pending_info(part_future, today="20260925")
    check("미시행 판별: 조문별 시행일", "20270701" in info and "제32조의2" in info, info)
    check("미시행 없음", L._pending_info(part_future, today="20270702") == "")

    adm = ('<?xml version="1.0" encoding="UTF-8"?><AdmRulService><행정규칙기본정보><행정규칙ID>1</행정규칙ID>'
           '</행정규칙기본정보><조문내용><![CDATA[제7조(기준) 본문]]></조문내용>'
           '<조문내용><![CDATA[제7조의2(특례) 본문]]></조문내용><조문내용><![CDATA[제70조(기타)]]></조문내용>'
           '</AdmRulService>')
    out = L._extract_articles(adm, "admrul", "7", "XML")
    check("admrul 제7조만 발췌", "제7조(기준)" in out and "제7조의2" not in out and "제70조" not in out)
    out = L._extract_articles(adm, "admrul", "제7조의2", "XML")
    check("admrul 제7조의2 발췌", "제7조의2" in out and "제7조(기준)" not in out)
    one = ('<AdmRulService><행정규칙기본정보><행정규칙ID>22029</행정규칙ID></행정규칙기본정보><조문내용><![CDATA['
           '제7장 자본거래\n제7-13조(신고의 예외거래) 본문\n제7-14조(거주자의 외화자금차입)\n ① 차입 본문\n  1. 호\n'
           '제7-14조의2(현지법인등의 외화자금차입 등) 의2 본문\n제7-15조(보고) 보고 본문\n제8장 기타\n제8-1조(목적) 끝'
           ']]></조문내용></AdmRulService>')
    o1 = L._extract_articles(one, "admrul", "7-14", "XML")
    o2 = L._extract_articles(one, "admrul", "제7-14조의2", "XML")
    o3 = L._extract_articles(one, "admrul", "7-15", "XML")
    check("LAW-07 한 블록 admrul: 제7-14조만(의2·7-15 없이)", "제7-14조(거주자의 외화자금차입)" in o1 and "① 차입 본문" in o1
          and "제7-14조의2" not in o1 and "제7-15조" not in o1 and "22029" in o1, o1)
    check("LAW-07 한 블록 admrul: 제7-14조의2만", "제7-14조의2(" in o2 and "제7-14조(" not in o2 and "제7-15조" not in o2, o2)
    check("LAW-07 한 블록 admrul: 장 제목 앞에서 끊음", "보고 본문" in o3 and "제8장" not in o3, o3)
    check("LAW-07 --text 평문", (L._body_text(o1) or "").splitlines()[-1] == "  1. 호", L._body_text(o1))
    check("LAW-07 편·장식 번호 해석", [L._admrul_jo(x)[1] for x in ("7-14", "제7-14조", "제7-14조의2", "10-21의2", "7",
                                                               "제7조의2")]
          == ["제7-14조", "제7-14조", "제7-14조의2", "제10-21조의2", "제7조", "제7조의2"])
    check("LAW-07 법령은 '390-2' 계속 exit 2·admrul 해석 불가 exit 2",
          exits(L.encode_jo, "390-2") == 2 and exits(L._admrul_jo, "7-14-") == 2 and exits(L._admrul_jo, "제1항") == 2)
    ordin = ("<LawService><자치법규기본정보><자치법규ID>9</자치법규ID></자치법규기본정보><조문>"
             "<조 조문번호='000100'><조문번호>000100</조문번호><조내용><![CDATA[제1조]]></조내용></조>"
             "<조 조문번호='000200'><조문번호>000200</조문번호><조내용><![CDATA[제2조]]></조내용></조>"
             "</조문></LawService>")
    out = L._extract_articles(ordin, "ordin", "2", "XML")
    check("ordin 제2조만 발췌", "000200" in out and "000100" not in out)
    txt = L._body_text(L._extract_articles(adm, "admrul", "7", "XML")) or ""
    check("--text admrul: 발췌 조문만 평문", txt.splitlines()[-1] == "제7조(기준) 본문" and "</" not in txt, txt)

    # 출력 축약 — search 표·get --text
    srch = ('<?xml version="1.0" encoding="UTF-8"?><LawSearch><target>law</target><키워드>민법</키워드>'
            '<totalCnt>9</totalCnt><page>1</page><numOfRows>2</numOfRows>'
            '<law id="1"><법령일련번호>284415</법령일련번호><현행연혁코드>현행</현행연혁코드><법령명한글>민법</법령명한글>'
            '<법령약칭명></법령약칭명><법령ID>001706</법령ID><법령상세링크>/DRF/lawService.do?OC=***&amp;MST=1</법령상세링크></law>'
            '<law id="2"><법령일련번호>188376</법령일련번호><법령명한글>난민|법</법령명한글><법령ID>011546</법령ID></law>'
            '</LawSearch>')
    tab = (L._search_table(srch, "law", display=2) or "").splitlines()
    check("search 표: 머리 줄·다음 쪽 안내", tab[:1] == ["# law '민법' — totalCnt 9, page 1, 2건 (다음 쪽: --page 2)"], tab[:1])
    check("search 표: 태그 이름 열·빈 열 생략·링크 제외", tab[1:2] == ["법령명한글 | 법령ID | 법령일련번호 | 현행연혁코드"]
          and tab[2] == "민법 | 001706 | 284415 | 현행" and tab[3].startswith("난민¦법"), tab)
    # 다음 쪽 판정은 요청한 display 기준 — numOfRows는 이번 쪽의 실제 건수다(LAW-06).
    def pg(total: int, page: int, n: int, display: int) -> str:
        body = (f"<LawSearch><키워드>x</키워드><totalCnt>{total}</totalCnt><page>{page}</page><numOfRows>{n}</numOfRows>"
                + "".join(f"<law><법령명한글>a{i}</법령명한글></law>" for i in range(n)) + "</LawSearch>")
        return (L._search_table(body, "law", display=display) or "").splitlines()[0]
    h1, h2, h3 = pg(2, 2, 0, 4), pg(9, 2, 4, 5), pg(12, 2, 5, 5)
    check("LAW-06 다음 쪽: 빈 쪽·마지막 부분 쪽은 안내 없음, 남으면 안내",
          "(다음 쪽" not in h1 and "마지막 쪽을 지났습니다 — 전체 1쪽" in h1 and "(다음 쪽" not in h2
          and h3.endswith("(다음 쪽: --page 3)"), f"{h1} / {h2} / {h3}")
    byl = ('<licBylSearch><totalCnt>1</totalCnt><page>1</page><ordinbyl><별표명><![CDATA[(별지) <strong class="x">옥외</strong>]]></별표명>'
           '<별표서식파일링크>/LSW/flDownload.do?gubun=ELIS&amp;flSeq=1&amp;flNm=%28abc</별표서식파일링크></ordinbyl></licBylSearch>')
    tab = (L._search_table(byl, "ordinbyl") or "").splitlines()
    check("search 표: 강조 태그·flNm 제거", tab[2:3] == ["(별지) 옥외 | /LSW/flDownload.do?gubun=ELIS&flSeq=1"], tab)
    check("search 표: 해석 불가 → None(원문 출력)", L._search_table("<html>오류", "law") is None)
    nos = ('<LawSearch><totalCnt>2</totalCnt><page>1</page><law><법령명한글>행정소송법</법령명한글><공포번호>06627</공포번호></law>'
           '<law><법령명한글>x</법령명한글><공포번호>2022-44</공포번호></law></LawSearch>')
    tab = (L._search_table(nos, "law") or "").splitlines()
    check("search 표: 공포번호 앞자리 0 제거·비숫자 유지(LAW-01 후속)", tab[2:] == ["행정소송법 | 6627", "x | 2022-44"], tab)
    adm = '<AdmRulSearch><totalCnt>1</totalCnt><admrul><행정규칙명>세칙</행정규칙명><발령번호>9999</발령번호></admrul></AdmRulSearch>'
    tab = (L._search_table(adm, "admrul") or "").splitlines()
    check("search 표: 행정규칙 자리표시 번호 9999 → 없음(원값)", tab[2:] == ["세칙 | 없음(원값 9999)"], tab)
    law = ('<법령><기본정보><법령ID>010199</법령ID><공포일자>20251216</공포일자><공포번호>21205</공포번호>'
           '<법종구분>법률</법종구분><법령명_한글>전자금융거래법</법령명_한글><시행일자>20251216</시행일자></기본정보><조문>'
           '<조문단위><조문여부>전문</조문여부><조문내용>   제2장 전자금융거래 당사자의 권리와 의무</조문내용></조문단위>'
           '<조문단위><조문여부>조문</조문여부><조문내용>제9조(책임)</조문내용><항><항내용>①손해를 배상한다.</항내용>'
           '<호><호내용>1. 위조</호내용><목><목내용>가. 변조</목내용></목></호></항><조문참고자료>[제목개정]</조문참고자료></조문단위>'
           '</조문><부칙><부칙내용>부칙 본문</부칙내용></부칙></법령>')
    txt = L._body_text(law) or ""
    check("--text 법령: 기본정보 한 줄·장 제목·항/호/목 들여쓰기·부칙 제외",
          txt.splitlines()[0] == "전자금융거래법 · 법률 · 법령ID 010199 · 시행 20251216 · 공포 20251216 · 제21205호"
          and "제2장 전자금융거래 당사자의 권리와 의무" in txt and "\n①손해를 배상한다.\n  1. 위조\n    가. 변조" in txt
          and "부칙" not in txt and "참고자료" not in txt, txt)

    rows = [
        {"명칭": "전자금융감독규정", "시행일자": "20260715", "MST": "2100000282622", "계통ID": "21828"},
        {"명칭": "전자금융감독규정시행세칙", "시행일자": "20260420", "MST": "2200000108629", "계통ID": "2050888"},
    ]
    picked = L._select_lineage(rows, "admrul", "전자금융감독규정", None)
    check("계통: 정확명 자동 선택", {r["계통ID"] for r in picked} == {"21828"})
    rows2 = [
        {"명칭": "가평군 옥외광고물 등의 관리와 옥외광고산업 진흥에 관한 조례", "시행일자": "20260420",
         "MST": "2124537", "계통ID": "2019869"},
        {"명칭": "가평군 옥외광고발전기금 설치 및 운용 조례", "시행일자": "20260420",
         "MST": "2124587", "계통ID": "2019870"},
    ]
    try:
        L._select_lineage(rows2, "ordin", "가평군 옥외광고물", None)
        check("계통: 모호하면 중단", False, "exit 없이 선택함")
    except SystemExit as e:
        check("계통: 모호하면 중단", e.code == 2)
    check("계통: --lid 지정", L._select_lineage(rows2, "ordin", "가평군 옥외광고물", "2019869")[0]["MST"] == "2124537")
    check("MST 정수 비교", max([{"시행일자": "1", "MST": "1869"}, {"시행일자": "1", "MST": "2100000000000"}],
                             key=L._version_key)["MST"] == "2100000000000")
    denv = {**BASE_ENV, "LAW_GO_KR_OC": "dummykey_xyz"}
    d1 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "get", "--target", "eflaw", "--lm", "민법"],
                        env=denv, capture_output=True, text=True)
    d2 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "versions", "--target", "admrul", "--query", "x"],
                        env=denv, capture_output=True, text=True)
    check("dry-run 명령: 키 가림·admrul URL", d1.returncode == 0 and "dummykey_xyz" not in d1.stdout + d2.stdout
          and "OC=***" in d1.stdout and "target=admrul" in d2.stdout, d1.stdout + d2.stdout)
    o1 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "search", "--target", "admrul", "--query", "x",
                         "--org", "금융위원회"], env=denv, capture_output=True, text=True)
    o2 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "search", "--target", "ordin", "--query", "x",
                         "--org", "6110000"], env=denv, capture_output=True, text=True)
    check("--org 기관명 → exit 2, 코드는 통과", o1.returncode == 2 and "기관 코드" in o1.stderr
          and o2.returncode == 0 and "org=6110000" in o2.stdout, o1.stderr[-200:] + o2.stdout[-200:])

    # LAW-08 --ancyd 거부·--lang EN → target=elaw
    a8 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "get", "--target", "law", "--id", "001248", "--ancyd",
                         "20200609"], env=denv, capture_output=True, text=True)
    e8 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "get", "--target", "law", "--id", "001248", "--lang", "EN",
                         "--jo", "3"], env=denv, capture_output=True, text=True)
    m8 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "get", "--target", "law", "--mst", "1", "--lang", "EN"],
                        env=denv, capture_output=True, text=True)
    check("LAW-08 --ancyd → exit 2·get-asof 안내", a8.returncode == 2 and "get-asof" in a8.stderr and not a8.stdout,
          a8.stderr[-200:])
    check("LAW-08 --lang EN → target=elaw·LANG 없음, --mst는 exit 2", e8.returncode == 0 and "target=elaw" in e8.stdout
          and "LANG" not in e8.stdout and "ID=001248" in e8.stdout and m8.returncode == 2, e8.stdout + m8.stderr[-200:])
    el = ('<Law><InfSection><lsId>001248</lsId><ancYd>20121015</ancYd><ancNo>00996</ancNo><lsNmEng><![CDATA[X ACT]]>'
          '</lsNmEng></InfSection><JoSection><Jo No="1"><joNo>0003</joNo><joBrNo>00</joBrNo><joYn>N</joYn><joCts>'
          '<![CDATA[CHAPTER I]]></joCts></Jo><Jo No="2"><joNo>0003</joNo><joBrNo>00</joBrNo><joYn>Y</joYn><joCts>'
          '<![CDATA[Article 3 (A) text]]></joCts></Jo><Jo No="3"><joNo>0003</joNo><joBrNo>02</joBrNo><joYn>Y</joYn>'
          '<joCts><![CDATA[Article 3-2 (B)]]></joCts></Jo></JoSection><ArSection><Ar><arCts>ADDENDA</arCts></Ar>'
          '</ArSection></Law>')
    hit, lab = L._elaw_articles(el, "3")
    hit2, _ = L._elaw_articles(el, "제3조의2")
    check("LAW-08 영문본 조 발췌(장 제목·가지조 제외)", lab == "Article 3" and len(hit) == 1 and "text" in hit[0]
          and len(hit2) == 1 and "3-2" in hit2[0], str(hit))
    check("LAW-08 영문본 --text", (L._body_text(el) or "").splitlines()[0] == "X ACT · 법령ID 001248 · 번역 기준 공포 "
          "20121015 · 제996호" and "ADDENDA" not in (L._body_text(el) or ""), L._body_text(el))

    # LAW-09 dry-run은 호출·저장하지 않는다(get-asof 버전 검색, download)
    with tempfile.TemporaryDirectory() as t:
        cenv = {**denv, "WK_LEGAL_CACHE_DIR": os.path.join(t, "cache")}
        g9 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "get-asof", "--lid", "010199", "--date", "20150101",
                             "--jo", "9"], env=cenv, capture_output=True, text=True)
        n_cache = sum(len(f) for _, _, f in os.walk(os.path.join(t, "cache")))
        check("LAW-09 get-asof dry-run: 검색 URL만·캐시 0·선택 헤더 없음", g9.returncode == 0 and "OC=***" in g9.stdout
              and "target=eflaw" in g9.stdout and "LID=010199" in g9.stdout and n_cache == 0
              and "선택본" not in g9.stderr and "dummykey_xyz" not in g9.stdout, g9.stdout + g9.stderr[-200:])
        a9 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "get-asof", "--target", "admrul", "--query", "x",
                             "--date", "20150101", "--jo", "7-14"], env=cenv, capture_output=True, text=True)
        check("LAW-09 get-asof admrul dry-run: 현행·연혁 두 URL", a9.returncode == 0
              and len(a9.stdout.split()) == 2 and "nw=2" in a9.stdout, a9.stdout + a9.stderr[-200:])
        d9 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "download", "--url", "/LSW/flDownload.do?flSeq=1",
                             "--out-dir", os.path.join(t, "x")], env=cenv, capture_output=True, text=True)
        check("LAW-09 download dry-run: URL·예정 경로만, 폴더 미생성", d9.returncode == 0
              and "https://www.law.go.kr/LSW/flDownload.do?flSeq=1" in d9.stdout and os.path.join(t, "x") in d9.stdout
              and not os.path.exists(os.path.join(t, "x")), d9.stdout + d9.stderr[-200:])
        # LAW-10 인자 없음 → exit 2, 기본 저장 폴더 미생성
        w = os.path.join(t, "w")
        os.makedirs(w)
        n10 = subprocess.run([sys.executable, SCRIPT, "download"], cwd=w, env=cenv, capture_output=True, text=True)
        check("LAW-10 download 인자 없음 → exit 2·byl_downloads 미생성", n10.returncode == 2 and os.listdir(w) == [],
              n10.stderr[-200:] + str(os.listdir(w)))
        # 없는 검색 XML → 트레이스백 없이 exit 2, 저장 폴더 안내·생성 없음
        m10 = subprocess.run([sys.executable, SCRIPT, "download", "--from-search-xml", os.path.join(t, "none.xml")],
                             cwd=w, env=cenv, capture_output=True, text=True)
        check("download 없는 검색 XML → exit 2·'읽을 수 없습니다'·트레이스백 없음", m10.returncode == 2
              and "검색 XML 파일을 읽을 수 없습니다" in m10.stderr and "Traceback" not in m10.stderr
              and "저장합니다" not in m10.stderr and os.listdir(w) == [], m10.stderr[-300:])
        # --display 상한 100 — 요청도 100으로(서버는 쪽당 100건·100건 단위 쪽)
        s6 = subprocess.run([sys.executable, SCRIPT, "--dry-run", "search", "--target", "licbyl", "--query", "*",
                             "--display", "150"], env=cenv, capture_output=True, text=True)
        check("LAW-06 --display 150 → NOTE·display=100 요청", s6.returncode == 0 and "display=100" in s6.stdout
              and "상한은 100" in s6.stderr, s6.stdout + s6.stderr[-200:])
        # LAW-11 0건 검색 XML → '검색 결과가 0건', 태그 보강 안내 없음
        zx = os.path.join(t, "z.xml")
        with open(zx, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?><LicBylSearch><target>licbyl</target><키워드>건축법</키워드>'
                    '<totalCnt>0</totalCnt><page>1</page><numOfRows>0</numOfRows><resultCode>00</resultCode>'
                    '</LicBylSearch>')
        z11 = subprocess.run([sys.executable, SCRIPT, "download", "--from-search-xml", zx, "--out-dir",
                              os.path.join(t, "o")], env=cenv, capture_output=True, text=True)
        check("LAW-11 0건 XML → '검색 결과가 0건'·--search 2 안내, 태그 보강 문구 없음", z11.returncode == 1
              and "검색 결과가 0건" in z11.stderr and "--search 2" in z11.stderr
              and "BYL_LINK_TAG_HINTS" not in z11.stderr and not os.path.exists(os.path.join(t, "o")), z11.stderr)
    pe = L._download_payload_error
    check("LAW-10 가짜 파일 판정", pe(b"", "image/gif") is not None
          and pe("<script>alert(' 파일이 없습니다. ');</script>".encode(), "text/html;charset=UTF-8") is not None
          and pe(b"<html>x</html>", "text/html") is not None and pe(b"%PDF-1.4 ...", "application/pdf") is None
          and pe(b"\xd0\xcf\x11\xe0hwp", "application/octet-stream") is None)

    # 키 파일 탐색 — (a) 상위 폴더의 .law_api.env, (b) 키 없는 cwd .env는 건너뛰고 ~/.config를 읽되 FOO는 주입 안 함.
    code = "import os, law_api as L; print(L.resolve_oc(None), os.environ.get('FOO'), os.environ.get('OTHER'))"
    clean = {k: v for k, v in BASE_ENV.items() if k not in ("LAW_GO_KR_OC", "LAW_API_DOTENV")}
    with tempfile.TemporaryDirectory() as t:
        work = os.path.join(t, "work", "case")
        home = os.path.join(t, "home")
        os.makedirs(work)
        os.makedirs(os.path.join(home, ".config", "korean-law-api"))
        with open(os.path.join(t, "work", ".law_api.env"), "w") as f:
            f.write("LAW_GO_KR_OC=from_upward\n")
        env = {**clean, "HOME": home, "PYTHONPATH": SCRIPT_DIR}
        r = subprocess.run([sys.executable, "-c", code], cwd=work, env=env, capture_output=True, text=True)
        check("키 파일: 상위 폴더 .law_api.env 발견", r.stdout.split()[:1] == ["from_upward"], r.stderr[-200:])
        os.remove(os.path.join(t, "work", ".law_api.env"))
        with open(os.path.join(work, ".env"), "w") as f:
            f.write("FOO=bar\n")
        with open(os.path.join(home, ".config", "korean-law-api", ".env"), "w") as f:
            f.write("LAW_GO_KR_OC=from_config\nOTHER=1\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=work, env=env, capture_output=True, text=True)
        check("키 파일: 키 없는 cwd .env 건너뜀·다른 키 미주입", r.stdout.split() == ["from_config", "None", "None"],
              r.stdout + r.stderr[-200:])

    # get-asof 기준일이 오늘 뒤면 시행예정(nw=2)까지 검색 — 시행예정 판본을 고르도록(P2 교차 점검)
    import argparse as _ap
    today = L._today()
    ns = lambda nw=None: _ap.Namespace(nw=nw)
    check("nw: 기준일 ≤ 오늘 → 1,3 / 오늘 뒤 → 1,2,3 / --nw 지정 우선",
          L._law_nw(ns(), today) == "1,3" and L._law_nw(ns(), "29991231") == "1,2,3"
          and L._law_nw(ns("3"), "29991231") == "3" and L._law_nw(ns(), None) == "1,3")


# ---------------------------------------------------------------------------
# 라이브
# ---------------------------------------------------------------------------

def run(args: list[str], cwd: str | None = None, env: dict | None = None,
        python: str = sys.executable) -> subprocess.CompletedProcess:
    return subprocess.run([python, SCRIPT, *args], cwd=cwd, env=env or BASE_ENV, capture_output=True, text=True)


def live() -> None:
    key = L.resolve_oc(None)
    cache = tempfile.mkdtemp(prefix="law_api_regress_cache_")
    env = {**BASE_ENV, "WK_LEGAL_CACHE_DIR": cache, "LAW_API_TODAY": "20260925"}

    def leaks(*texts: str) -> int:
        return sum(t.count(key) for t in texts)

    try:
        # B1 — 조례: 부분일치 검색어면 계통 목록과 함께 중단, --lid면 옥외광고물 조례를 고른다.
        r = run(["get-asof", "--target", "ordin", "--query", "가평군 옥외광고물", "--date", "20260501"], env=env)
        check("B1 조례 모호 → exit 2", r.returncode == 2 and "2019869" in r.stderr and "2019870" in r.stderr,
              r.stderr[-300:])
        r = run(["get-asof", "--target", "ordin", "--query", "가평군 옥외광고물", "--lid", "2019869",
                 "--date", "20260501", "--jo", "1"], env=env)
        check("B1 조례 --lid 선택", r.returncode == 0 and "옥외광고물 등의 관리와" in r.stderr
              and "발전기금" not in r.stderr.split("선택본:")[1].split("\n")[0], r.stderr[-300:])
        # B1 — 행정규칙: 정확명이면 규정 계통 자동 선택, 시행세칙 행이 판례식·시행기간에 섞이지 않는다.
        r = run(["get-asof", "--target", "admrul", "--query", "전자금융감독규정", "--date", "20190301",
                 "--jo", "7"], env=env)
        sel = r.stderr.split("선택본:")[1].split("\n")[0] if "선택본:" in r.stderr else ""
        check("B1 행정규칙 계통 자동 선택", r.returncode == 0 and "시행세칙" not in sel and "21828" in sel, sel)
        check("B1 행정규칙 시행기간·판례식에 세칙 없음",
              "20221231" in r.stderr and "세칙" not in r.stderr.split("판례식:")[-1]
              and "금융위원회고시 제2022-44호" in r.stderr, r.stderr[-400:])
        check("admrul --jo 발췌 크기", r.returncode == 0 and len(r.stdout) < 20000 and "제7조" in r.stdout,
              f"{len(r.stdout)}자")
        # M1 — 단계적 시행분 판례식
        r = run(["get-asof", "--lid", "001706", "--date", "20250601", "--jo", "162"], env=env)
        check("M1 단계적 시행 판례식", "개정되어 2026. 1. 1. 시행되기 전의 것" in r.stderr, r.stderr[-300:])
        # B2 — 공포본 미시행 조문
        r = run(["get", "--target", "law", "--mst", "280277", "--jo", "9"], env=env)
        check("B2 law → 시행본 대체", r.returncode == 0 and "<시행일자>20251216</시행일자>" in r.stdout
              and "20261217" in r.stderr, r.stderr[-300:])
        r = run(["get", "--target", "law", "--mst", "280277", "--jo", "25의4"], env=env)
        check("B2 미시행 신설 조문 → exit 2", r.returncode == 2 and "시행되지 않은 신설" in r.stderr, r.stderr[-300:])
        r = run(["get", "--target", "law", "--mst", "280277", "--jo", "25의4", "--promulgated"], env=env)
        check("B2 --promulgated 공포본", r.returncode == 0 and "<시행일자>20261217</시행일자>" in r.stdout)
        r = run(["get", "--target", "law", "--mst", "283839", "--jo", "32의2"], env=env)
        check("B2 조문별 미시행(개인정보 보호법)", r.returncode == 0 and "20270701" in r.stderr, r.stderr[-300:])
        # 출력 축약(2.4.14)
        r = run(["search", "--target", "law", "--query", "민법", "--display", "5"], env=env)
        check("search 표 출력(원시 XML 아님)", r.returncode == 0 and r.stdout.startswith("# law '민법'")
              and "법령ID" in r.stdout and "</" not in r.stdout, r.stdout[:200])
        r = run(["search", "--target", "eflaw", "--query", "행정소송법", "--nw", "1", "--display", "5", "--sort", "dasc",
                 "--page", "2"], env=env)
        check("LAW-01 search 표 공포번호 정규화(06627 → 6627)", r.returncode == 0 and "| 6627 |" in r.stdout
              and "| 06627 |" not in r.stdout, r.stdout[:400])
        r = run(["get", "--target", "eflaw", "--lm", "민법", "--jo", "390", "--text"], env=env)
        check("get --text 법령", r.returncode == 0 and "제390조(채무불이행과 손해배상)" in r.stdout
              and "법령ID 001706" in r.stdout and "</" not in r.stdout and len(r.stdout) < 600, r.stdout[:200])
        r = run(["get-asof", "--target", "admrul", "--query", "전자금융감독규정", "--date", "20241225", "--jo", "7",
                 "--text"], env=env)
        check("get-asof --text 행정규칙", r.returncode == 0 and r.stdout.splitlines()[0].startswith("전자금융감독규정 · 고시")
              and "제7조(" in r.stdout and "</" not in r.stdout, r.stdout[:200] + r.stderr[-200:])
        r = run(["get", "--target", "eflaw", "--lm", "민법", "--jo", "390"], env=env)
        check("eflaw --lm 현행 1회 조회", r.returncode == 0 and "<조문번호>390</조문번호>" in r.stdout)
        # 시점 의존: 공소청법(MST 285045)은 2026-10-02 시행 — 그 전까지는 현행 시행본이 없다.
        from datetime import date
        if date.today() < date(2026, 10, 2):
            r = run(["get", "--target", "law", "--mst", "285045", "--jo", "1"], env=env)
            check("B2 시행 전 제정 법령 → 안내와 exit 2", r.returncode == 2 and "아직 시행 전인 법령" in r.stderr,
                  r.stderr[-300:])
        r = run(["get-asof", "--lid", "001706", "--date", "2020. 6. 1.", "--jo", "1"], env=env)
        check("get-asof '2020. 6. 1.' → 2018-02-01 시행본", r.returncode == 0 and "시행 20180201" in r.stderr,
              r.stderr[-300:])
        r = run(["get", "--target", "eflaw", "--id", "001706", "--type", "HTML", "--jo", "390"], env=env)
        h = run(["search", "--target", "law", "--query", "민법", "--type", "HTML", "--display", "1"], env=env)
        check("type=HTML 정상 출력·키 노출 0", r.returncode == 0 and len(r.stdout) > 200 and h.returncode == 0
              and leaks(r.stdout, h.stdout, h.stderr) == 0, h.stderr[-200:])
        r = run(["get", "--target", "law", "--mst", "280277", "--jo", "999"], env=env)
        check("공포본·시행본 모두 없는 조문 안내", r.returncode == 2 and "모두에 없는 조문" in r.stderr, r.stderr[-200:])
        r = run(["get-asof", "--target", "admrul", "--id", "2100000282622", "--date", "20241225", "--max-steps", "3"],
                env=env)
        check("행정규칙 체인 경로 판례식 표기", "금융위원회고시 제" in r.stderr, r.stderr[-300:])
        r = run(["get-asof", "--target", "ordin", "--query", "가평군 옥외광고물", "--lid", "2019869",
                 "--date", "20150101"], env=env)
        check("조례 판례식 표기(경기도가평군조례)", "경기도가평군조례 제" in r.stderr, r.stderr[-300:])
        # 일치 없음 — exit 2, 캐시하지 않음
        r1 = run(["get", "--target", "law", "--mst", "246234"], env=env)
        r2 = run(["get", "--target", "law", "--mst", "246234"], env=env)
        check("일치 없음 → exit 2·미캐시", r1.returncode == 2 and r2.returncode == 2 and "CACHE: hit" not in r2.stderr)
        # 날짜 검증
        r = run(["get-asof", "--lid", "001706", "--date", "20151340"], env=env)
        check("존재하지 않는 날짜 거부", r.returncode == 2)
        # M2 — 출력·저장·dry-run 키 노출 0
        with tempfile.TemporaryDirectory() as t:
            save = os.path.join(t, "s.xml")
            r = run(["search", "--target", "ordin", "--query", "옥외광고물", "--no-cache", "--save-to", save], env=env)
            with open(save, encoding="utf-8") as f:
                saved = f.read()
            d = run(["--dry-run", "get", "--target", "law", "--mst", "284415"], env=env)
            v = run(["--dry-run", "versions", "--target", "admrul", "--query", "전자금융감독규정"], env=env)
            check("M2 키 노출 0 (stdout·저장·dry-run)",
                  r.returncode == 0 and leaks(r.stdout, r.stderr, saved, d.stdout, v.stdout) == 0
                  and "target=admrul" in v.stdout)
        # 공포(발령)번호 정규화 — 판례식·선택본 머리행·--text 머리행
        r = run(["get-asof", "--lid", "001218", "--date", "20000101", "--jo", "20", "--text"], env=env)
        check("번호 정규화: 행정소송법 판례식·머리행", r.returncode == 0 and "법률 제6627호로 개정되기 전의 것" in r.stderr
              and "제4770호" in r.stderr and "· 제4770호 ·" in r.stdout and "제0" not in r.stderr + r.stdout,
              r.stderr[-300:] + r.stdout[:200])
        r = run(["get-asof", "--lid", "006358", "--date", "20230301", "--jo", "1"], env=env)
        check("번호 정규화: 부령(보건복지부령 제968호)", "보건복지부령 제968호" in r.stderr and "제00968호" not in r.stderr,
              r.stderr[-300:])
        r = run(["get-asof", "--lid", "001692", "--date", "20050101", "--jo", "1"], env=env)
        check("번호 정규화: 형법 법률 제7623호", "법률 제7623호로 개정되기 전의 것" in r.stderr, r.stderr[-300:])
        r = run(["get-asof", "--target", "admrul", "--query", "전자금융감독규정시행세칙", "--date", "20230101",
                 "--jo", "1", "--text"], env=env)
        check("번호 정규화: 금감원 세칙 자리표시 9999 생략", r.returncode == 0 and "제9999호" not in r.stderr + r.stdout
              and "(2024. 9. 13. 개정되기 전의 것)" in r.stderr, r.stderr[-300:])
        # 단계적 시행·공포 순서 역전 판례식
        for lid, day in (("001248", "20230715"), ("001702", "20260410"), ("007079", "20260718"), ("001638", "20260314")):
            r = run(["get-asof", "--lid", lid, "--date", day, "--jo", "1"], env=env)
            tail = r.stderr.split("판례식:")[-1].split("\n")[0]
            check(f"판례식 B·C 실측({lid} {day})", "개정되어" in tail and "시행되기 전의 것" in tail, tail)
        r = run(["get-asof", "--lid", "001692", "--date", "20240410", "--jo", "1"], env=env)
        check("판례식 일반형 유지(형법 20240410)", "(2025. 3. 18. 법률 제20795호로 개정되기 전의 것)" in r.stderr,
              r.stderr[-300:])
        # get --target law → 언제나 시행일 기준 본문(뒤에 공포·먼저 시행된 개정 반영)
        r = run(["get", "--target", "law", "--lm", "민사소송법", "--jo", "402의2", "--text"], env=env)
        check("law → eflaw: 민사소송법 제402조의2", r.returncode == 0 and "항소이유서의 제출" in r.stdout, r.stderr[-300:])
        r = run(["get", "--target", "law", "--lm", "민사소송법", "--jo", "421", "--text"], env=env)
        check("law → eflaw: 제421조 개정 문언", r.returncode == 0 and "제402조의3에 따른 결정" in r.stdout, r.stdout[-300:])
        r = run(["get", "--target", "law", "--lm", "민사소송법", "--jo", "421", "--text", "--promulgated"], env=env)
        check("law --promulgated 공포본 유지", r.returncode == 0 and "제402조의 규정에 따른 명령" in r.stdout, r.stdout[-300:])
        r = run(["get", "--target", "law", "--mst", "258669", "--jo", "163", "--text"], env=env)
        check("law --mst → 그 버전 시행 기준(뒤에 시행된 개정 미혼입)", r.returncode == 0 and "제163조" in r.stdout
              and "개인정보 기재부분" not in r.stdout, r.stdout[-300:])
        # --byl — 기준일 별표
        r = run(["get-asof", "--lid", "004273", "--date", "20250801", "--byl", "4", "--text"], env=env)
        check("--byl 기준일 별표(외국환거래법 시행령 20250801)", r.returncode == 0 and r.stdout.startswith(
              "[별표 4] 과태료 부과기준(제41조 관련) · 별표시행일자 20231212") and len(r.stdout) > 3000, r.stdout[:200])
        r = run(["get-asof", "--lid", "004273", "--date", "20260926", "--byl", "4"], env=env)
        check("--byl 현행 별표", r.returncode == 0 and "<개정 2025. 12. 30.>" in r.stdout, r.stdout[:300])
        r = run(["get-asof", "--lid", "007634", "--date", "20260301", "--byl", "23"], env=env)
        check("--byl 식품위생법 시행규칙 [별표 23]", r.returncode == 0 and "행정처분 기준(제89조 관련)" in r.stdout,
              r.stdout[:200])
        r = run(["get-asof", "--lid", "004273", "--date", "20250801", "--byl", "99"], env=env)
        check("--byl 없는 번호 → exit 2·목록", r.returncode == 2 and "[별표 3의2]" in r.stderr, r.stderr[-300:])
        r = run(["get-asof", "--target", "admrul", "--query", "전자금융감독규정", "--date", "20241225", "--byl", "1"], env=env)
        check("--byl 행정규칙 별표", r.returncode == 0 and r.stdout.startswith("[별표 1] 정보기술부문"), r.stdout[:200])
        # 조문 단위 대조·조문 기준 판례식(LAW-04)
        r = run(["get-asof", "--lid", "001692", "--date", "20250106", "--jo", "356", "--text"], env=env)
        check("LAW-04 문언이 현행과 같으면 통상 표기 안내", r.returncode == 0 and "현행과 같습니다" in r.stderr
              and "구법 표기 필수" not in r.stderr and "조문 기준 판례식" not in r.stderr, r.stderr[-400:])
        r = run(["get-asof", "--lid", "001692", "--date", "20240410", "--jo", "347", "--text"], env=env)
        art = r.stderr.split("조문 기준 판례식:")[-1].split("\n")[0]
        check("LAW-04 형법 제347조: 법령 기준 제20795호·조문 기준 제21231호", r.returncode == 0
              and "판례식: 구 형법(2025. 3. 18. 법률 제20795호로 개정되기 전의 것)" in r.stderr
              and "구 형법(2025. 12. 23. 법률 제21231호로 개정되기 전의 것)" in art and "구법 표기 필수" in r.stderr, art)
        r = run(["get-asof", "--lid", "001638", "--date", "20190901", "--jo", "148의2", "--text"], env=env)
        art = r.stderr.split("조문 기준 판례식:")[-1].split("\n")[0]
        check("LAW-04 도로교통법 제148조의2 = 대법원 2022도3929 특정(제17371호)", r.returncode == 0
              and "구 도로교통법(2020. 6. 9. 법률 제17371호로 개정되기 전의 것)" in art, art)
        r = run(["get-asof", "--lid", "001638", "--date", "20190901", "--jo", "148의2", "--max-steps", "2"], env=env)
        check("LAW-04 탐색 상한 도달 안내", r.returncode == 0 and "--max-steps를 늘리세요" in r.stderr, r.stderr[-300:])
        # 자리표시 연혁 행 제외(LAW-05)
        gq = ["--target", "ordin", "--query", "서울특별시 강남구 옥외광고물 등의 관리와 옥외광고산업 진흥에 관한 조례",
              "--org", "6110000", "--sborg", "3220000"]
        r = run(["get-asof", *gq, "--date", "20251001", "--jo", "2", "--text"], env=env)
        check("LAW-05 자리표시 행이 직후 개정으로 붙지 않음", r.returncode == 0
              and not any(x in r.stderr for x in ("0. 0. 0.", "제none호", "99991231", "직후 개정:", "직후 변동:"))
              and "기준일 현재 시행본이 현행과 동일" in r.stderr, r.stderr[-400:])
        r = run(["versions", *gq], env=env)
        check("LAW-05 versions 계통표 최신 시행일·자리표시 표지", r.returncode == 0
              and "2072455 | 20250321 |" in r.stderr and "(자리표시 — 선택 제외)" in r.stdout, r.stderr[-300:])
        # 자리표시·폐지 판본(검증 보정) — 경기도옥외광고물등관리조례 계통 2024478
        gg = ["--target", "ordin", "--query", "경기도옥외광고물등관리조례", "--lid", "2024478"]
        r = run(["get-asof", *gg, "--date", "20000101"], env=env)
        check("자리표시: 기준일 전 공포 행 → '제정 전' 대신 시행일 미상 안내", r.returncode == 2
              and "특정할 수 없습니다" in r.stderr and "1991. 2. 7. 제2104호" in r.stderr
              and "제정 전 시점" not in r.stderr, r.stderr[-400:])
        r = run(["get-asof", *gg, "--date", "19900101"], env=env)
        check("자리표시: 첫 공포 전이면 '제정 전'", r.returncode == 2 and "제정 전 시점" in r.stderr, r.stderr[-300:])
        r = run(["get-asof", *gg, "--date", "20260926"], env=env)
        check("폐지: 선택본이 폐지 판본이면 '⚠ 폐지본'", r.returncode == 0 and "⚠ 폐지본" in r.stderr
              and "시행기간" not in r.stderr, r.stderr[-300:])
        r = run(["versions", *gg], env=env)
        check("폐지: versions 표 '(폐지)' 표지", "873196 | 2024478 | 연혁 (폐지) |" in r.stdout, r.stdout[-300:])
        pq = ["--target", "law", "--query", "공공기관의 개인정보보호에 관한 법률", "--jo", "2", "--text"]
        r = run(["get-asof", *pq, "--date", "20110929"], env=env)
        check("폐지 계통: 현행 대조 생략·판례식 '폐지되기 전의 것'", r.returncode == 0 and "조문 대조를 생략" in r.stderr
              and "제10465호로 폐지되기 전의 것" in r.stderr and "일치하는 법령" not in r.stderr, r.stderr[-400:])
        r = run(["get-asof", *pq, "--date", "20120101"], env=env)
        check("폐지 계통: 폐지 판본 + --jo → exit 2·폐지 전날 안내", r.returncode == 2 and "⚠ 폐지본" in r.stderr
              and "--date 20110929" in r.stderr and "신설" not in r.stderr, r.stderr[-400:])
        # 경과조치(기준일 뒤 개정의 부칙) 안내·조회(LAW-R02)
        r = run(["get-asof", "--lid", "001671", "--date", "20071220", "--jo", "249", "--text"], env=env)
        line = next((ln for ln in r.stderr.splitlines() if "경과조치 확인" in ln), "")
        import re as _re
        m = _re.search(r"--mst (\d+) --efyd (\d+) --addenda (\d+)", line)
        check("R02 연혁본 헤더에 경과조치 확인·직후 MST", r.returncode == 0 and m is not None
              and "MST" in r.stderr.split("직후 개정:")[-1].split("\n")[0], line or r.stderr[-300:])
        if m:
            a = run(["get", "--target", "eflaw", "--mst", m.group(1), "--efyd", m.group(2), "--addenda", m.group(3)],
                    env=env)
            check("R02 --addenda 8730 부칙 제3조(종전의 규정)", a.returncode == 0 and "종전의 규정을 적용한다" in a.stdout
                  and len(a.stdout) < 3000 and "13454" not in a.stdout, f"{len(a.stdout)}자 " + a.stderr[-200:])
        r = run(["get-asof", "--lid", "001671", "--date", "20150730", "--jo", "253의2", "--text"], env=env)
        check("R02 신설 조문 없음 → 부칙 적용례 안내(exit 2)", r.returncode == 2 and "부칙" in r.stderr
              and "적용례" in r.stderr and "--addenda" in r.stderr, r.stderr[-300:])
        r = run(["get-asof", "--lid", "001671", "--date", "20150730", "--addenda", "13454"], env=env)
        check("R02 get-asof --addenda 13454 부칙 제2조", r.returncode == 0 and "제253조의2의 개정규정은" in r.stdout
              and "부칙 <제8730호" not in r.stdout, r.stdout[:200] + r.stderr[-200:])
        r = run(["get-asof", "--lid", "001671", "--date", "20150730", "--jo", "249", "--addenda"], env=env)
        check("R02 --addenda와 --jo 함께 → exit 2", r.returncode == 2 and "--addenda" in r.stderr)
        r = run(["get-asof", "--target", "ordin", "--query", "가평군 옥외광고물", "--lid", "2019869",
                 "--date", "20150101"], env=env)
        check("R02 자치법규 연혁본 경과조치 안내", "경과조치 확인" in r.stderr and "get --target ordin --mst" in r.stderr,
              r.stderr[-300:])
        # LAW-06 --display 150(서버는 100건·100건 단위 쪽) — 요청을 100으로 낮춰 다음 쪽 안내가 맞다
        r = run(["search", "--target", "licbyl", "--query", "*", "--display", "150", "--page", "2"], env=env)
        head = (r.stdout.splitlines() or [""])[0]
        check("LAW-06 --display 150 → 100건·다음 쪽 안내", r.returncode == 0 and "page 2, 100건 (다음 쪽: --page 3)" in head
              and "상한은 100" in r.stderr, head)
        # LAW-06 빈 쪽에서 다음 쪽 안내 없음
        r = run(["search", "--target", "law", "--query", "전자금융", "--display", "4", "--page", "2"], env=env)
        check("LAW-06 빈 쪽: 다음 쪽 안내 없음", r.returncode == 0 and "다음 쪽" not in r.stdout.splitlines()[0],
              r.stdout[:200])
        # LAW-07 편·장식 조문번호·한 블록 발췌
        r = run(["get-asof", "--target", "admrul", "--query", "외국환거래규정", "--date", "20250801", "--jo", "7-14",
                 "--text"], env=env)
        check("LAW-07 외국환거래규정 제7-14조 발췌", r.returncode == 0 and "제7-14조(거주자의 외화자금차입)" in r.stdout
              and "제7-15조(" not in r.stdout and "제7-14조의2(" not in r.stdout and len(r.stdout) < 20000,
              f"{len(r.stdout)}자 " + r.stderr[-200:])
        # LAW-08 영문본
        r = run(["get", "--target", "law", "--id", "001248", "--lang", "EN", "--jo", "3", "--text"], env=env)
        check("LAW-08 --lang EN → Article 3 영문 발췌·번역본 NOTE", r.returncode == 0 and "Article 3 (" in r.stdout
              and "Article 4 (" not in r.stdout and "영문 번역본(참고용·법적 효력 없음)" in r.stderr, r.stderr[-200:])
        r = run(["get", "--target", "eflaw", "--lm", "형법", "--lang", "EN", "--jo", "347", "--text"], env=env)
        check("LAW-08 --lm 형법(elaw LM 불일치 → 법령ID 재조회)", r.returncode == 0 and "Article 347 (Fraud)" in r.stdout
              and "CHAPTER" not in r.stdout, r.stderr[-200:])
        # LAW-10 download 가짜 성공·트레이스백
        with tempfile.TemporaryDirectory() as t:
            r = run(["download", "--url", "/LSW/flDownload.do?flSeq=987654321987", "--filename", "nofile",
                     "--out-dir", t], env=env)
            check("LAW-10 없는 파일 → exit 3 FAILED·파일 없음", r.returncode == 3 and "FAILED" in r.stderr
                  and "SAVED" not in r.stdout and os.listdir(t) == [], r.stdout + r.stderr[-200:])
            r = run(["download", "--url", "https://www.law.go.kr/DRF/no_such_file_xyz.pdf", "--out-dir", t], env=env)
            check("LAW-10 HTTP 404 → exit 3·트레이스백 없음", r.returncode == 3 and "Traceback" not in r.stderr
                  and "HTTP 404" in r.stderr, r.stderr[-200:])
            # LAW-11 api_reference 6-A 예시(관련법령명 검색) → 목록·download
            x = os.path.join(t, "byl_search_licbyl.xml")
            r = run(["search", "--target", "licbyl", "--query", "건축법", "--search", "2", "--display", "20",
                     "--save-to", x], env=env)
            d = run(["download", "--from-search-xml", x, "--limit", "5", "--out-dir", os.path.join(t, "o")], env=env)
            check("LAW-11 licbyl 건축법 --search 2 → 목록·download 5건", r.returncode == 0
                  and "totalCnt 0," not in r.stdout.splitlines()[0] and d.returncode == 0
                  and d.stdout.count("SAVED") == 5, r.stdout[:120] + d.stderr[-200:])
        # 기준일이 오늘 뒤 — 시행예정본 선택(전기통신금융사기피해환급법 제21503호, 시행 2026. 10. 1.)
        if L._today() < "20261001":
            r = run(["get-asof", "--lid", "011359", "--date", "20261015", "--jo", "1", "--text"], env=env)
            check("get-asof 미래 기준일 → 시행예정본(MST 285053)·'시행예정본' 안내·구법 표기 안내 없음",
                  r.returncode == 0 and "MST 285053" in r.stderr and "시행예정본" in r.stderr
                  and "구법 표기" not in r.stderr, r.stderr[-300:])
        cowork(key, env)
    finally:
        shutil.rmtree(cache, ignore_errors=True)


def cowork(key: str, env: dict) -> None:
    """Cowork VM 조건 모사: 읽기 전용 스킬 폴더, ~/.config 키 없음, 작업 폴더의 .law_api.env,
    스킬 기준 경로와 다른 위치에서 절대경로로 실행, 설치 폴더에서 download."""
    with tempfile.TemporaryDirectory() as t:
        home = os.path.join(t, "home")                       # Cowork: HOME=/sessions/<id>, 마운트는 그 아래 mnt/
        plug = os.path.join(home, "mnt", ".remote-plugins", "plugin_x")
        shutil.copytree(ROOT, plug, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "evals"))
        skill = os.path.join(plug, "skills", "ko-law-api")
        script = os.path.join(skill, "scripts", "law_api.py")
        work = os.path.join(home, "mnt", "10_진행사건", "사건A")
        tmp = os.path.join(t, "tmp")
        os.makedirs(work)
        os.makedirs(tmp)
        with open(os.path.join(home, "mnt", "10_진행사건", ".law_api.env"), "w") as f:
            f.write(f"LAW_GO_KR_OC={key}\n")
        for dp, dns, fns in os.walk(plug):
            for n in fns + dns:
                os.chmod(os.path.join(dp, n), 0o555 if n in dns or n.endswith(".py") else 0o444)
        os.chmod(plug, 0o555)
        venv = {k: v for k, v in env.items() if k not in ("LAW_GO_KR_OC", "LAW_API_DOTENV")}
        venv.update({"HOME": home, "TMPDIR": tmp})
        venv.pop("WK_LEGAL_CACHE_DIR", None)                   # 기본 캐시(~/.cache) 경로가 쓰기 가능한지도 본다
        # SKILL.md 2.1의 경로 찾기 한 줄
        r = subprocess.run(["bash", "-c", "find \"$HOME\" -maxdepth 8 -path '*/ko-law-api/scripts/law_api.py' "
                            "2>/dev/null | head -1"], env=venv, capture_output=True, text=True)
        check("Cowork 모사: SKILL.md 경로 찾기 한 줄", r.stdout.strip() == script, r.stdout)
        pythons = [p for p in (sys.executable, "/usr/bin/python3") if os.path.exists(p)]
        for py in pythons:
            ver = subprocess.run([py, "-c", "import sys;print('%d.%d'%sys.version_info[:2])"],
                                 capture_output=True, text=True).stdout.strip()
            r = subprocess.run([py, script, "get", "--target", "eflaw", "--lm", "민법", "--jo", "162"],
                               cwd=work, env=venv, capture_output=True, text=True)
            check(f"Cowork 모사 {ver}: 작업 폴더 키 파일·읽기 전용 스킬", r.returncode == 0
                  and "<조문번호>162</조문번호>" in r.stdout and r.stdout.count(key) == 0, r.stderr[-200:])
            r = subprocess.run([py, script, "get-asof", "--target", "admrul", "--query", "전자금융감독규정",
                                "--date", "20241225", "--jo", "2"], cwd=work, env=venv, capture_output=True, text=True)
            check(f"Cowork 모사 {ver}: get-asof admrul", r.returncode == 0 and "제2조" in r.stdout, r.stderr[-200:])
        r = subprocess.run([sys.executable, script, "search", "--target", "licbyl", "--query", "위탁지정신청서",
                            "--display", "1", "--save-to", os.path.join(work, "b.xml")],
                           cwd=work, env=venv, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, script, "download", "--from-search-xml", os.path.join(work, "b.xml"),
                             "--limit", "1"], cwd=skill, env=venv, capture_output=True, text=True)
        check("Cowork 모사: 설치 폴더에서 download → 임시 폴더",
              r.returncode == 0 and r2.returncode == 0 and os.path.join(tmp, "law_api_byl") in r2.stdout,
              (r2.stderr or r2.stdout)[-200:])
        check("Cowork 모사: 기본 캐시가 HOME 아래 생성", os.path.isdir(os.path.join(home, ".cache", "wk-legal", "law-api")))
        for dp, dns, fns in os.walk(t):
            for n in dns + fns:
                try:
                    os.chmod(os.path.join(dp, n), 0o755)
                except OSError:
                    pass


def main() -> None:
    offline()
    if "--live" in sys.argv:
        live()
    width = max(len(n) for n, _, _ in RESULTS)
    fails = 0
    for name, ok, detail in RESULTS:
        fails += not ok
        line = f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}"
        if not ok and detail:
            line += f"  — {detail.strip()[:300]}"
        print(line)
    print(f"\n{len(RESULTS) - fails}/{len(RESULTS)} 통과")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
