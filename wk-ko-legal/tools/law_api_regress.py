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
    tab = (L._search_table(srch, "law") or "").splitlines()
    check("search 표: 머리 줄·다음 쪽 안내", tab[:1] == ["# law '민법' — totalCnt 9, page 1, 2건 (다음 쪽: --page 2)"], tab[:1])
    check("search 표: 태그 이름 열·빈 열 생략·링크 제외", tab[1:2] == ["법령명한글 | 법령ID | 법령일련번호 | 현행연혁코드"]
          and tab[2] == "민법 | 001706 | 284415 | 현행" and tab[3].startswith("난민¦법"), tab)
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
