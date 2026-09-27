#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
국가법령정보 OPEN API CLI
========================

법제처 「국가법령정보 공동활용」 OPEN API(law.go.kr/DRF)를 호출하여
법령(law) / 행정규칙(admrul) / 자치법규(ordin)의 목록 검색 및 본문 조회를 수행한다.

사용 예
-------
    # 현행 시행 조문 (정식 법령명을 알 때 — 1회 호출)
    python3 law_api.py get --target eflaw --lm "민법" --jo 390

    # 법령 검색 → 법령ID로 현행 시행본
    python3 law_api.py search --target law --query "전자금융거래법" --display 5
    python3 law_api.py get --target eflaw --id 010199 --jo 9

    # 행정규칙 검색·본문(특정 조만)
    python3 law_api.py search --target admrul --query "전자금융감독규정"
    python3 law_api.py get --target admrul --id <행정규칙일련번호> --jo 7

    # 자치법규 검색·본문
    python3 law_api.py search --target ordin --query "서울특별시 옥외광고물"
    python3 law_api.py get --target ordin --mst <자치법규일련번호>

OC(인증키) 처리
---------------
- 우선순위: `--oc` 인자 > 환경변수 `LAW_GO_KR_OC` > 키 파일 자동 탐색(resolve_oc 참조).
- `--oc`·명령줄 환경변수 지정은 세션 기록에 키를 남기므로 키 파일(`.law_api.env`)을 권장한다.
- 모두 없으면 안내 메시지와 함께 오류 종료.

응답 처리
---------
- 기본 응답 포맷은 XML. `--type JSON` 또는 `--type HTML`로 변경 가능.
- 응답 본문을 stdout으로 출력한다. law.go.kr가 본문에 echo하는 OC는 출력·저장 전에 마스킹한다.
- `--pretty` 플래그를 주면 XML/JSON을 보기 좋게 정렬한다.
- `--save-to <경로>`를 주면 응답 본문을 파일로도 저장한다.

종속성
------
표준 라이브러리만 사용 (urllib, xml.etree, json, argparse). Python 3.9 이상.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
import urllib.error
import urllib.parse
import urllib.request
import xml.dom.minidom
import xml.etree.ElementTree as ET
import xml.etree.ElementTree as ET

BASE_SEARCH = "https://www.law.go.kr/DRF/lawSearch.do"
BASE_SERVICE = "https://www.law.go.kr/DRF/lawService.do"

# 로컬 응답 캐시 (lawSearch.do·lawService.do GET 공통, TTL 24h).
# 인증키(OC)가 디스크에 남지 않도록 캐시 키는 URL에서 OC 파라미터를 제거한 뒤 해시한다.
CACHE_TTL_SECONDS = 24 * 60 * 60

# versions/get-asof의 내부 검색 순회(_search_pages 등)까지 --no-cache를 전파하기 위한 플래그.
# 명령 진입점(cmd_*)에서 args.no_cache로 1회 설정한다(헬퍼 시그니처 변경 최소화).
_NO_CACHE = False


def _cache_dir() -> str:
    """캐시 디렉터리. 기본 ~/.cache/wk-legal/law-api/, WK_LEGAL_CACHE_DIR로 재정의."""
    override = os.environ.get("WK_LEGAL_CACHE_DIR")
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), ".cache", "wk-legal", "law-api")


def _strip_oc_from_url(url: str) -> str:
    """URL에서 OC 파라미터만 제거한 문자열을 반환(캐시 키 산출용, 인증키 비저장)."""
    try:
        parts = urllib.parse.urlsplit(url)
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        pairs = [(k, v) for (k, v) in pairs if k != "OC"]
        new_query = urllib.parse.urlencode(pairs, doseq=True)
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
        )
    except ValueError:
        # 파싱 실패 시에도 OC 노출을 막기 위해 정규식으로 제거
        return re.sub(r"([?&])OC=[^&]*", r"\1", url)


def _cache_key(url: str) -> str:
    """OC를 제거한 URL의 sha256 hex 다이제스트를 캐시 키로 사용."""
    canonical = _strip_oc_from_url(url)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_path(url: str) -> str:
    return os.path.join(_cache_dir(), _cache_key(url) + ".body")


def _cache_read(url: str) -> str | None:
    """유효한 캐시가 있으면 응답 본문을, 없거나 만료·손상이면 None을 반환."""
    path = _cache_path(url)
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return None
    if age > CACHE_TTL_SECONDS:
        return None  # TTL 경과 — 무효
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        # 손상 파일은 무시하고 재요청하도록 None 반환
        return None


def _cache_write(url: str, body: str) -> None:
    """성공 응답 본문만 캐시에 기록. 쓰기 실패는 조용히 무시(캐시는 최적화일 뿐)."""
    path = _cache_path(url)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)  # 원자적 교체 — 부분 기록 파일이 캐시에 남지 않도록
    except OSError:
        return
    _cache_prune()


def _cache_prune() -> None:
    """TTL이 지난 캐시 파일을 하루 한 번 정리한다(표식 파일 mtime 기준). 실패는 무시."""
    d = _cache_dir()
    marker = os.path.join(d, ".last_prune")
    now = time.time()
    try:
        if now - os.path.getmtime(marker) < CACHE_TTL_SECONDS:
            return
    except OSError:
        pass
    try:
        for name in os.listdir(d):
            if not (name.endswith(".body") or name.endswith(".tmp")):
                continue
            p = os.path.join(d, name)
            try:
                if now - os.path.getmtime(p) > CACHE_TTL_SECONDS:
                    os.remove(p)
            except OSError:
                continue
        with open(marker, "w", encoding="utf-8") as f:
            f.write(str(int(now)))
    except OSError:
        return


def _today() -> str:
    """오늘(한국 시간) YYYYMMDD. 시험용으로 LAW_API_TODAY 환경변수로 고정할 수 있다."""
    fixed = os.environ.get("LAW_API_TODAY", "").strip()
    if len(fixed) == 8 and fixed.isdigit():
        return fixed
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")


def _display_url(url: str) -> str:
    """사람·모델에게 보여줄 URL — OC 값을 ***로 가린다(--dry-run 등)."""
    return re.sub(r"([?&]OC=)[^&]*", r"\1***", url)


def _mask_oc_in_body(body: str, url: str) -> str:
    """캐시 저장 전 응답 본문의 OC 값을 마스킹.

    law.go.kr가 검색 응답의 <법령상세링크> 등에 요청 OC를 그대로 echo하므로,
    본문을 그대로 저장하면 인증키가 디스크에 남는다. 'OC=<값>'만 치환하며,
    스크립트는 상세링크를 파싱하지 않으므로 동작에 영향이 없다.
    """
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        oc = (q.get("OC") or [""])[0]
    except Exception:
        return body
    if not oc:
        return body
    masked = body.replace("OC=" + oc, "OC=MASKED")
    quoted = urllib.parse.quote(oc, safe="")
    if quoted != oc:
        masked = masked.replace("OC=" + quoted, "OC=MASKED")
    # HTML 응답은 키를 <input type="hidden" id="OC" value="<키>"> 등 'OC=' 없이도 싣는다 — 값 자체를 가린다.
    # (너무 짧은 값은 본문 오염 위험이 있어 제외)
    if len(oc) >= 4:
        for v in {oc, quoted}:
            masked = masked.replace(v, "MASKED")
    return masked


def looks_like_html_error(body: str) -> bool:
    """응답이 XML/JSON이 아니라 HTML 오류 페이지인지 판정."""
    head = body.lstrip()[:512].lower()
    return head.startswith("<!doctype") or head.startswith("<html")


# API 오류 필드 (references/api_reference.md: resultCode 00=성공, resultMsg=success).
_RESULT_CODE_RE = re.compile(r"<resultCode>\s*([^<]*?)\s*</resultCode>")
_RESULT_MSG_RE = re.compile(r"<resultMsg>\s*([^<]*?)\s*</resultMsg>")

# 사용자 검증 실패 봉투 — OC 오타·미등록, 미승인 IP·도메인 등이면 resultCode 없이
#   XML : <Response><result>사용자 정보 검증에 실패하였습니다.</result><msg>…</msg></Response>
#   JSON: {"result": "…", "msg": "…"}
# 형태로 반환된다. 정상 응답의 루트는 <LawSearch>·<법령> 등이며 <Response>를 쓰지 않으므로
# 루트 + 필드 구조만으로 판별한다(메시지 문구에는 의존하지 않음).
_ENVELOPE_ROOT_RE = re.compile(r"^\ufeff?\s*(?:<\?xml[^>]*\?>)?\s*<Response[\s>]")
_ENVELOPE_RESULT_RE = re.compile(r"<result>\s*(.*?)\s*</result>", re.DOTALL)
_ENVELOPE_MSG_RE = re.compile(r"<msg>\s*(.*?)\s*</msg>", re.DOTALL)


def _find_validation_envelope_error(body: str) -> str | None:
    """resultCode 없이 반환되는 사용자 검증 실패 봉투를 감지해 메시지를 반환, 아니면 None.

    회귀 방지(2.1.1): 잘못된 OC의 <Response><result>/<msg> 오류 본문이 HTML 검사·
    resultCode 검사를 모두 통과해 exit 0으로 정상 결과처럼 출력·캐시되던 문제.
    """
    if _ENVELOPE_ROOT_RE.match(body):
        m = _ENVELOPE_RESULT_RE.search(body)
        if not m:
            return None
        parts = [f'result="{m.group(1).strip()}"']
        mm = _ENVELOPE_MSG_RE.search(body)
        if mm and mm.group(1).strip():
            parts.append(f'msg="{mm.group(1).strip()}"')
        return " ".join(parts)
    # JSON 변형 — 봉투는 항상 소형이므로 큰 정상 본문은 파싱 없이 통과시킨다
    if body.lstrip().startswith("{") and len(body) <= 4096:
        try:
            obj = json.loads(body)
        except ValueError:
            return None
        if isinstance(obj, dict) and "result" in obj and set(obj) <= {"result", "msg"}:
            parts = [f'result="{str(obj["result"]).strip()}"']
            jmsg = str(obj.get("msg", "")).strip()
            if jmsg:
                parts.append(f'msg="{jmsg}"')
            return " ".join(parts)
    return None


def _find_not_found(body: str) -> str | None:
    """본문 조회의 '일치하는 …이 없습니다' 응답이면 그 문구를, 아니면 None.

    식별자(MST·ID·LM)가 틀리면 API는 HTTP 200으로
      XML : <Law>일치하는 법령이 없습니다.  법령명을 확인하여 주십시오.</Law>
      JSON: {"Law": "일치하는 법령이 없습니다. …"}
    를 준다(행정규칙·자치법규·해석례도 같은 형태). 자식 요소 없는 단일 루트 + '없습니다'로 판별한다.
    검색 0건(<LawSearch>…<totalCnt>0…)은 자식이 있으므로 해당하지 않는다.
    """
    s = body.strip()
    if len(s) > 1000 or "없습니다" not in s:
        return None
    if s.startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            return None
        if isinstance(obj, dict) and len(obj) == 1:
            val = next(iter(obj.values()))
            if isinstance(val, str) and "없습니다" in val:
                return val.strip()
        return None
    try:
        root = ET.fromstring(s)
    except ET.ParseError:
        return None
    if len(root) == 0 and "없습니다" in (root.text or ""):
        return " ".join((root.text or "").split())
    return None


def find_api_error(body: str) -> str | None:
    """응답 본문에 오류 필드(resultCode≠00 등)가 있으면 사람이 읽을 메시지를 반환, 정상이면 None.

    XML/JSON 양쪽을 관대하게 훑는다. 성공 코드 '00'(및 관용적 '0'/공백)이면 정상으로 본다.
    resultCode 자체가 없는 본문은 사용자 검증 실패 봉투(<Response><result>/<msg>)인지
    추가로 확인한다.
    """
    code = None
    msg = ""
    m = _RESULT_CODE_RE.search(body)
    if m:
        code = m.group(1).strip()
        mm = _RESULT_MSG_RE.search(body)
        if mm:
            msg = mm.group(1).strip()
    else:
        # JSON 응답 대비 (예: "resultCode":"00")
        mj = re.search(r'"resultCode"\s*:\s*"?\s*([0-9]+)\s*"?', body)
        if mj:
            code = mj.group(1).strip()
            mmj = re.search(r'"resultMsg"\s*:\s*"([^"]*)"', body)
            if mmj:
                msg = mmj.group(1).strip()
    if code is None:
        return _find_validation_envelope_error(body)
    if code in ("00", "0", ""):
        return None
    return f"resultCode={code}" + (f' resultMsg="{msg}"' if msg else "")

VALID_TARGETS = {
    "law": "법령(법률·시행령·시행규칙)",
    "eflaw": "현행법령(시행일) — 연혁 포함 시점별 버전 (검색·본문)",
    "admrul": "행정규칙(고시·훈령·예규·감독규정 등)",
    "admrulOldAndNew": "행정규칙 신구법비교 (직전 개정 전후 조문 대비)",
    "ordin": "자치법규(조례·규칙)",
    "licbyl": "법령 별표·서식",
    "admbyl": "행정규칙 별표·서식",
    "ordinbyl": "자치법규 별표·서식",
    "expc": "법령해석례 (법제처 유권해석)",
}

# target별 검색 결과 기본 개수.
# - 좁은 검색이 일반적인 target은 20 (법령·행정규칙·법령해석례)
# - 분야 키워드/wildcard로 결과가 흔히 수백~수만 건 나오는 target은 50
DISPLAY_DEFAULTS: dict[str, int] = {
    "law": 20,
    "eflaw": 100,
    "admrulOldAndNew": 20,
    "admrul": 20,
    "expc": 20,
    "ordin": 50,
    "licbyl": 50,
    "admbyl": 50,
    "ordinbyl": 50,
}
DISPLAY_FALLBACK = 20

# 별표·서식 검색 target은 검색만 가능 (본문은 검색 결과의 다운로드 URL을 통해 가져옴)
SEARCH_ONLY_TARGETS = {"licbyl", "admbyl", "ordinbyl"}

VALID_TYPES = ("XML", "JSON", "HTML")


# ---------------------------------------------------------------------------
# .env 자동 로드 (옵션 D)
# ---------------------------------------------------------------------------

def _candidate_dotenv_paths() -> list[str]:
    """
    키 파일 탐색 후보를 우선순위 순으로 반환한다.
    우선순위:
      1) 환경변수 LAW_API_DOTENV로 사용자가 명시한 경로
      2) 현재 작업 디렉터리부터 루트까지 거슬러 올라가며 찾은 .law_api.env
         (Cowork 등 샌드박스: 마운트된 작업 폴더 최상위에 한 번 두면 하위 어디서든 발견)
      3) 현재 작업 디렉터리의 .env
      4) 스크립트가 위치한 폴더(scripts/)의 .env
      5) 스크립트 폴더의 부모(=skill 폴더)의 .env
      6) ~/.law_api.env
      7) ~/.config/korean-law-api/.env
    """
    candidates: list[str] = []

    explicit = os.environ.get("LAW_API_DOTENV")
    if explicit:
        candidates.append(os.path.expanduser(explicit))

    d = os.path.abspath(os.getcwd())
    while True:
        candidates.append(os.path.join(d, ".law_api.env"))
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent

    candidates.append(os.path.join(os.getcwd(), ".env"))

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_dir = os.getcwd()
    candidates.append(os.path.join(script_dir, ".env"))
    candidates.append(os.path.abspath(os.path.join(script_dir, "..", ".env")))

    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, ".law_api.env"))
    candidates.append(os.path.join(home, ".config", "korean-law-api", ".env"))

    # 중복 제거(절대경로 기준)하면서 순서 유지
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        c_abs = os.path.abspath(c)
        if c_abs in seen:
            continue
        seen.add(c_abs)
        out.append(c_abs)
    return out


def _parse_dotenv(path: str) -> dict[str, str]:
    """
    단순 .env 파서.
    - 빈 줄과 '#'으로 시작하는 주석은 무시.
    - 'export KEY=VALUE'와 'KEY=VALUE' 모두 허용.
    - 양쪽 끝의 짝맞는 따옴표(", ')는 제거.
    - 줄 끝 인라인 주석( # 앞에 공백)은 따옴표가 없을 때만 잘라낸다.
    """
    result: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n").strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if not key:
                    continue
                value = value.strip()
                # 따옴표로 감싸진 경우 그대로 보존(후처리 후 제거), 그 외에는 인라인 주석 분리
                if value and value[0] in ("'", '"'):
                    quote = value[0]
                    end = value.find(quote, 1)
                    if end > 0:
                        value = value[1:end]
                else:
                    # 비-따옴표: 인라인 주석 ' #' 이전까지만 사용
                    hash_pos = value.find(" #")
                    if hash_pos >= 0:
                        value = value[:hash_pos].rstrip()
                result[key] = value
    except FileNotFoundError:
        # 탐색 경로 순회 중 없는 파일은 정상 — 조용히 건너뜀
        return {}
    except (PermissionError, OSError) as e:
        # 존재하는 .env 를 못 읽는 것은 설정 문제 — 침묵하면 OC 누락 오류가 늦게 발생
        print(f"WARN: .env exists but unreadable ({path}): {e}", file=sys.stderr)
        return {}
    return result


def _load_dotenv_into_environ(verbose: bool = False) -> str | None:
    """
    탐색 경로를 순서대로 보며 LAW_GO_KR_OC가 들어 있는 첫 파일에서 그 키만 os.environ에 채운다.
    키가 없는 파일(다른 프로젝트의 .env 등)은 건너뛴다 — 무관한 cwd의 .env가 뒤의 키 파일을
    가리지 않게 하기 위함. 다른 키는 환경에 주입하지 않는다.
    반환: 키를 읽은 파일의 절대 경로(없으면 None).
    """
    for path in _candidate_dotenv_paths():
        if not os.path.isfile(path):
            continue
        value = _parse_dotenv(path).get("LAW_GO_KR_OC", "").strip()
        if not value:
            if verbose:
                sys.stderr.write(f"INFO: LAW_GO_KR_OC 없는 파일 건너뜀: {path}\n")
            continue
        os.environ["LAW_GO_KR_OC"] = value
        if verbose:
            sys.stderr.write(f"INFO: 키 파일 로드: {path}\n")
        return path
    return None


def resolve_oc(cli_oc: str | None) -> str:
    """
    OC 우선순위: --oc 인자 > LAW_GO_KR_OC 환경변수 > 키 파일 자동 로드(_candidate_dotenv_paths).
    """
    if cli_oc:
        sys.stderr.write(
            "NOTE: --oc로 넘긴 키는 명령 기록(세션 로그)에 남습니다 — "
            "작업 폴더의 .law_api.env(LAW_GO_KR_OC=…) 또는 ~/.config/korean-law-api/.env를 쓰세요.\n"
        )
        return cli_oc

    oc = os.environ.get("LAW_GO_KR_OC")
    if oc:
        return oc

    # 환경변수도 인자도 없으면 키 파일 자동 로드 시도
    verbose = os.environ.get("LAW_API_VERBOSE", "").lower() in ("1", "true", "yes")
    _load_dotenv_into_environ(verbose=verbose)
    oc = os.environ.get("LAW_GO_KR_OC")
    if oc:
        return oc

    sys.stderr.write(
        "ERROR: OC(인증키)를 찾지 못했습니다. 'LAW_GO_KR_OC=<your_id>' 한 줄짜리 키 파일을 다음 중 한 곳에 두세요.\n"
        "  • 작업 폴더(또는 그 상위 폴더)의 .law_api.env  ← Cowork 등 샌드박스 VM은 이것을 권장\n"
        "  • ~/.config/korean-law-api/.env 또는 ~/.law_api.env\n"
        "  • 임의 경로 + 환경변수 LAW_API_DOTENV=<경로>\n"
        f"  (현재 작업 폴더: {os.getcwd()})\n"
        "  키 값을 명령줄(--oc, LAW_GO_KR_OC=…)·echo·cat으로 드러내지 마세요 — 세션 기록에 남습니다.\n"
    )
    sys.exit(2)


def build_url(base: str, params: dict[str, str]) -> str:
    """None/빈 값 제거 후 querystring 조립."""
    cleaned = {k: v for k, v in params.items() if v is not None and v != ""}
    qs = urllib.parse.urlencode(cleaned, doseq=True, encoding="utf-8")
    return f"{base}?{qs}"


def http_get(url: str, timeout: int = 20, no_cache: bool = False,
             strict_errors: bool = False, fmt: str = "XML") -> str:
    """HTTP GET. UTF-8 디코딩 실패 시 EUC-KR 폴백.

    캐시(TTL 24h)를 http_get 계층에서 처리한다 — lawSearch.do·lawService.do 공통.
    적중 시 stderr에 'CACHE:' 접두 한 줄을 남긴다. --no-cache는 no_cache=True로 전달된다.

    strict_errors=True면 HTML 오류 페이지(OC 미등록 등)나 API 오류 필드(resultCode≠00)를
    감지해 명확한 메시지와 함께 비정상 종료한다. 응답 본문을 스스로 파싱·검사하는
    내부 호출부(_search_pages 등)는 False로 두어 관대하게 처리한다.
    fmt='HTML'(사용자가 HTML을 요청)이면 HTML 본문을 오류로 보지 않고, 오류 페이지와 구별할 수 없으므로 캐시하지 않는다.
    """
    html = fmt.upper() == "HTML"
    # 1) 캐시 조회
    if not no_cache:
        cached = None if html else _cache_read(url)
        if cached is not None:
            sys.stderr.write(f"CACHE: hit {_strip_oc_from_url(url)}\n")
            if strict_errors:
                _enforce_response_ok(cached, url)
            return cached

    # 2) 실제 HTTP 요청
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "claude-korean-law-api/1.0 (+skill:korean-law-api)",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTPError {e.code}: {e.reason}\nURL(OC 제외): {_strip_oc_from_url(url)}\n")
        sys.exit(3)
    except urllib.error.URLError as e:
        sys.stderr.write(f"URLError: {e.reason}\nURL(OC 제외): {_strip_oc_from_url(url)}\n")
        sys.exit(3)

    body = None
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            body = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if body is None:
        body = raw.decode("utf-8", errors="replace")

    # 3) 오류 검사 (엄격 모드) — 오류면 캐시하지 않고 종료
    if strict_errors:
        _enforce_response_ok(body, url, html_ok=html)

    # 4) 성공 응답만 캐시 — HTTP 200 & HTML 오류 페이지 아님 & API 오류 필드 없음 & '일치 없음' 아님.
    #    (비-strict 내부 순회에서 resultCode≠00 XML을 24h 캐시하는 것을 방지)
    if (not no_cache and not html and not looks_like_html_error(body) and find_api_error(body) is None
            and _find_not_found(body) is None):
        _cache_write(url, _mask_oc_in_body(body, url))

    return body


def _enforce_response_ok(body: str, url: str, html_ok: bool = False) -> None:
    """HTML 오류 페이지·API 오류 필드·'일치 없음' 응답을 감지하면 안내와 함께 비정상 종료."""
    nf = _find_not_found(body)
    if nf:
        sys.stderr.write(
            f"ERROR: 조회 결과 없음 — API 응답: \"{nf}\"\n"
            f"  URL(OC 제외): {_strip_oc_from_url(url)}\n"
            "  → 식별자를 search로 다시 확인하세요. 행정규칙 --id는 긴 <행정규칙일련번호>, "
            "자치법규 --mst는 <자치법규일련번호>, --lm은 약칭이 아닌 정식 명칭(띄어쓰기 무관)이어야 합니다.\n"
        )
        sys.exit(2)
    if not html_ok and looks_like_html_error(body):
        nf = _json_html_not_found(url)
        if nf:
            sys.stderr.write(
                f"ERROR: 조회 결과 없음 — 같은 조회를 XML로 재확인한 API 응답: \"{nf}\"\n"
                f"  URL(OC 제외): {_strip_oc_from_url(url)}\n"
                "  (JSON 요청의 '일치 없음'을 서버가 HTML 페이지로 돌려준 사례 — 인증키 문제가 아닙니다)\n"
                "  → 식별자를 search로 다시 확인하세요. --lm은 약칭이 아닌 정식 명칭(띄어쓰기 무관)이어야 합니다.\n"
            )
            sys.exit(2)
        sys.stderr.write(
            "ERROR: API가 XML/JSON이 아닌 HTML 페이지를 반환했습니다 — "
            "OC(인증키) 미등록·오타, 일일 호출 한도 초과, 또는 파라미터 조합 오류일 수 있습니다.\n"
            f"  URL(OC 제외): {_strip_oc_from_url(url)}\n"
            "  → open.law.go.kr에서 OC 등록 상태를 확인하고, 파라미터를 점검하세요.\n"
        )
        if "미신청된 목록/본문" in body:
            sys.stderr.write("  → 응답 문구 '미신청된 목록/본문': 식별자가 없거나 이 target이 JSON을 지원하지 않을 수 "
                             "있습니다 — --type XML로 재확인하세요.\n")
        sys.exit(3)
    err = find_api_error(body)
    if err:
        sys.stderr.write(
            f"ERROR: API가 오류를 반환했습니다 ({err}).\n"
            f"  URL(OC 제외): {_strip_oc_from_url(url)}\n"
        )
        if _find_validation_envelope_error(body) is not None:
            sys.stderr.write(
                "  → 사용자 검증 실패: OC(인증키) 오타·미등록 또는 미승인 IP·도메인일 수 "
                "있습니다. open.law.go.kr에서 OC 등록 상태를 확인하세요.\n"
            )
        sys.exit(3)


def _json_html_not_found(url: str) -> str | None:
    """lawService.do JSON 요청이 HTML 페이지로 돌아왔을 때 같은 요청을 XML로 한 번 다시 받아 '일치 없음'이면 그 문구.

    eflaw JSON은 없는 ID·LM에 '일치하는 법령이 없습니다' 대신 '미신청된 목록/본문' HTML을 준다(2026-09-27 실측 —
    같은 식별자의 XML·law JSON은 '일치 없음'). 그대로 두면 인증키 오류(exit 3)로 오분류된다. 오류 경로에서만 1회 호출한다.
    """
    parts = urllib.parse.urlsplit(url)
    if not parts.path.endswith("/lawService.do"):
        return None
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not any(k == "type" and v.upper() == "JSON" for k, v in query):
        return None
    xq = [(k, "XML" if k == "type" else v) for k, v in query]
    xurl = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(xq, encoding="utf-8")))
    return _find_not_found(http_get(xurl, no_cache=True))


def _strip_ws_nodes(node) -> None:
    """공백뿐인 텍스트 노드를 재귀로 지운다 — lawService XML의 줄바꿈 공백에 toprettyxml이 빈 줄을 더하지 않도록."""
    for child in list(node.childNodes):
        if child.nodeType == child.TEXT_NODE and not child.data.strip():
            node.removeChild(child)
        elif child.hasChildNodes():
            _strip_ws_nodes(child)


def maybe_pretty(body: str, fmt: str) -> str:
    """XML/JSON이면 pretty-print, 그 외(HTML 등)는 원본 반환.

    XML은 minidom을 쓴다(CDATA 보존 — ET.indent는 CDATA를 풀어 쓴다). 선언의 encoding="UTF-8"을 유지한다.
    """
    fmt = fmt.upper()
    try:
        if fmt == "XML":
            dom = xml.dom.minidom.parseString(body)
            _strip_ws_nodes(dom)
            return dom.toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")
        if fmt == "JSON":
            return json.dumps(json.loads(body), ensure_ascii=False, indent=2)
    except Exception:
        # 응답이 오류 페이지(HTML)인 경우 등 — 그대로 반환
        return body
    return body


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace) -> None:
    oc = resolve_oc(args.oc)
    # 사용자가 --display를 명시하지 않았으면 target별 기본값을 적용
    display = args.display
    if display is None:
        display = DISPLAY_DEFAULTS.get(args.target, DISPLAY_FALLBACK)
    if display > 100:
        # 서버는 쪽당 100건으로 자르고 쪽도 100건 단위로 센다(licbyl --display 150 --page 2 = 101~200번째, 2026-09-27
        # 실측) — 요청 값으로 다음 쪽을 판정하면 끝 무렵 안내가 빠진다.
        sys.stderr.write(f"NOTE: --display 상한은 100입니다 — {display} 대신 100으로 요청합니다.\n")
        display = 100
    params: dict[str, str] = {
        "OC": oc,
        "target": args.target,
        "type": args.type,
        "query": args.query,
        "display": str(display),
        "page": str(args.page),
    }
    # 선택 파라미터
    if args.search:
        params["search"] = args.search          # 1=법령명, 2=본문(법령 검색용)
    if args.org:
        params["org"] = args.org                # 소관부처(행정규칙) / 지자체 시·도(자치법규)
    if args.nw:
        params["nw"] = args.nw                  # eflaw: 1연혁,2시행예정,3현행(조합) / ordin: 1현행,2연혁
    if args.sborg:
        params["sborg"] = args.sborg            # 자치법규 시·군·구(org 필수 동반)
    if args.knd:
        params["knd"] = args.knd                # 종류(법령·행정규칙·자치법규별 의미 다름)
    if args.lid_search:
        params["LID"] = args.lid_search
    if args.efyd:
        params["efYd"] = args.efyd              # 시행일자 범위 (YYYYMMDD~YYYYMMDD)
    if args.ancyd:
        params["ancYd"] = args.ancyd            # 공포일자 범위
    if args.sort:
        params["sort"] = args.sort              # 정렬 (lasc/ldes/dasc/ddes/ndes 등)

    url = build_url(BASE_SEARCH, params)
    if args.dry_run:
        print(_display_url(url))
        return

    body = http_get(url, no_cache=args.no_cache, strict_errors=True, fmt=args.type)
    _emit(body, url, args, None if args.raw else (lambda b: _search_table(b, args.target, display)))


def _emit(body: str, url: str, args: argparse.Namespace, render=None, raw: str | None = None) -> None:
    """응답 출력·저장 공통 경로 — 본문에 echo된 OC(상세링크 등)를 가린 뒤 내보낸다.

    render가 있으면(search 표·get --text) 화면에는 그 결과를, --save-to 파일에는 원시 응답을 쓴다
    (download --from-search-xml 등 원시 XML을 읽는 경로와의 호환). raw가 있으면(admrul·ordin --jo 발췌)
    화면에는 발췌본(body)을, --save-to 파일에는 발췌 전 원시 응답(raw — 부칙·별표 포함)을 쓴다.
    """
    body = _mask_oc_in_body(body, url)
    output = maybe_pretty(body, args.type) if args.pretty else body
    if raw is not None:
        raw = _mask_oc_in_body(raw, url)
        saved = maybe_pretty(raw, args.type) if args.pretty else raw
    else:
        saved = output
    shown = None
    if render is not None:
        if args.type.upper() != "XML":
            sys.stderr.write(f"NOTE: 표·평문 출력은 XML 응답에서만 합니다 — type={args.type} 원문을 출력합니다.\n")
        else:
            shown = render(body)
            if shown is None:
                sys.stderr.write("NOTE: 응답을 해석하지 못해 원문을 그대로 출력합니다.\n")
    print(shown if shown is not None else output)
    if args.save_to:
        with open(args.save_to, "w", encoding="utf-8") as f:
            f.write(saved)


# ---------------------------------------------------------------------------
# 출력 축약 — search 표(기본), get·get-asof --text
# ---------------------------------------------------------------------------

# search 표의 열 = 응답 태그 이름 그대로(SKILL·api_reference의 필드명과 같다). 모든 행이 빈 열은 뺀다.
SEARCH_COLUMNS: dict[str, tuple[str, ...]] = {
    "law": ("법령명한글", "법령약칭명", "법령구분명", "법령ID", "법령일련번호", "시행일자", "공포일자", "공포번호",
            "제개정구분명", "현행연혁코드", "소관부처명"),
    "admrul": ("행정규칙명", "행정규칙종류", "행정규칙일련번호", "행정규칙ID", "시행일자", "발령일자", "발령번호",
               "제개정구분명", "현행연혁구분", "소관부처명"),
    "admrulOldAndNew": ("신구법명", "법령구분명", "신구법일련번호", "신구법ID", "시행일자", "발령일자", "발령번호",
                        "제개정구분명", "현행연혁코드", "소관부처명"),
    "ordin": ("자치법규명", "자치법규종류", "자치법규일련번호", "자치법규ID", "시행일자", "공포일자", "공포번호",
              "제개정구분명", "지자체기관명"),
    "expc": ("안건번호", "안건명", "법령해석례일련번호", "질의기관명", "회신기관명", "회신일자"),
    "licbyl": ("별표명", "별표종류", "별표번호", "관련법령명", "관련법령ID", "별표일련번호", "공포일자",
               "별표서식파일링크", "별표서식PDF파일링크"),
    "admbyl": ("별표명", "별표종류", "별표번호", "관련행정규칙명", "관련행정규칙일련번호", "별표일련번호",
               "발령일자", "별표서식파일링크"),
    "ordinbyl": ("별표명", "별표종류", "별표번호", "관련자치법규명", "지자체기관명", "관련자치법규일련번호",
                 "별표일련번호", "자치법규시행일자", "별표서식파일링크"),
}
SEARCH_COLUMNS["eflaw"] = SEARCH_COLUMNS["law"]


def _xml_root(body: str):
    try:
        return ET.fromstring(body[body.find("<"):]) if "<" in body else None
    except ET.ParseError:
        return None


def _cell(text: str | None) -> str:
    """표·평문용 한 줄 값 — 태그(검색어 강조 <strong> 등) 제거, 공백 정리, 열 구분자 치환."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip().replace("|", "¦")


def _search_table(body: str, target: str, display: int | None = None) -> str | None:
    """검색 응답 XML → 머리 한 줄(건수·쪽) + 표. 해석할 수 없으면 None(원문 출력).

    다음 쪽 판정의 쪽 크기는 요청한 display다 — 응답의 numOfRows는 이번 쪽의 실제 건수라(마지막 쪽·빈 쪽에서
    display보다 작고 빈 쪽이면 0) 쪽 크기로 쓰면 빈 쪽을 끝없이 안내한다.
    """
    root = _xml_root(body)
    if root is None:
        return None
    items = [c for c in root if len(c)]
    meta = {c.tag: (c.text or "").strip() for c in root if not len(c)}
    cols = SEARCH_COLUMNS.get(target) or tuple(dict.fromkeys(
        e.tag for it in items for e in it if not e.tag.endswith("상세링크")))
    rows = [[_cell(it.findtext(c)) for c in cols] for it in items]
    no_target = "admrul" if target.startswith("admrul") else target
    for r in rows:                              # 자치법규 별표 링크의 긴 파일명 파라미터는 다운로드에 필요 없다(실측)
        for i, c in enumerate(cols):
            if c.endswith("링크"):
                r[i] = re.sub(r"&flNm=[^&]*", "", r[i])
            elif c in ("공포번호", "발령번호") and r[i]:   # 서면 표기(앞자리 0 없음) — 원값은 --raw
                r[i] = _norm_prom_no(r[i], no_target) or f"없음(원값 {r[i]})"
    keep = [i for i in range(len(cols)) if any(r[i] for r in rows)]
    total, page = meta.get("totalCnt", "?"), meta.get("page", "1")
    head = f"# {target} '{meta.get('키워드', '')}' — totalCnt {total}, page {page}, {len(items)}건"
    try:
        per = display or len(items) or 1
        if items and int(total) > (int(page) - 1) * per + len(items):
            head += f" (다음 쪽: --page {int(page) + 1})"
        elif not items and int(total) > 0:
            head += f" (마지막 쪽을 지났습니다 — 전체 {-(-int(total) // per)}쪽)" if display else " (마지막 쪽을 지났습니다)"
    except ValueError:
        pass
    lines = [head]
    if items:
        lines.append(" | ".join(cols[i] for i in keep))
        lines += [" | ".join(r[i] for i in keep) for r in rows]
    return "\n".join(lines)


def _body_text(body: str) -> str | None:
    """본문 XML → 평문(기본정보 한 줄 + 조문 텍스트). 부칙·별표·연락처 등은 뺀다. 해석할 수 없으면 None."""
    root = _xml_root(body)
    if root is None:
        return None
    out: list[str] = []

    def info_line(block: str, fields: tuple[tuple[str, str], ...], target: str) -> str:
        b = root.find(block)
        vals = [(fmt, _cell(b.findtext(tag)) if b is not None else "") for fmt, tag in fields]
        vals = [(fmt, _norm_prom_no(v, target) if tag in ("공포번호", "발령번호", "ancNo") else v)
                for (fmt, v), (_, tag) in zip(vals, fields)]
        return " · ".join(fmt.format(v) for fmt, v in vals if v)

    if root.tag == "법령":                                         # law·eflaw
        out.append(info_line("기본정보", (("{}", "법령명_한글"), ("{}", "법종구분"), ("법령ID {}", "법령ID"),
                                          ("시행 {}", "시행일자"), ("공포 {}", "공포일자"), ("제{}호", "공포번호"),
                                          ("{}", "제개정구분"), ("{}", "소관부처")), "law"))
        for u in root.iter("조문단위"):
            main = (u.findtext("조문내용") or "").strip()
            if u.findtext("조문여부") != "조문":                    # 편·장·절 제목
                if main:
                    out.append("\n" + main)
                continue
            out.append("\n" + main)
            for e in u.iter():
                if e.tag in ("항내용", "호내용", "목내용") and (e.text or "").strip():
                    out.append({"항내용": "", "호내용": "  ", "목내용": "    "}[e.tag] + e.text.strip())
    elif root.tag == "AdmRulService":                              # admrul
        out.append(info_line("행정규칙기본정보", (("{}", "행정규칙명"), ("{}", "행정규칙종류"),
                                                  ("일련번호 {}", "행정규칙일련번호"), ("계통ID {}", "행정규칙ID"),
                                                  ("시행 {}", "시행일자"), ("발령 {}", "발령일자"), ("제{}호", "발령번호"),
                                                  ("{}", "소관부처명"), ("현행여부 {}", "현행여부")), "admrul"))
        out += ["\n" + (e.text or "").strip() for e in root.findall("조문내용") if (e.text or "").strip()]
    elif root.tag == "Law" and root.find("InfSection") is not None:  # 영문 번역본(elaw)
        out.append(info_line("InfSection", (("{}", "lsNmEng"), ("법령ID {}", "lsId"), ("번역 기준 공포 {}", "ancYd"),
                                            ("제{}호", "ancNo")), "law"))
        out += ["\n" + (e.text or "").strip() for e in root.iter("joCts") if (e.text or "").strip()]
    elif root.tag == "LawService":                                 # ordin
        out.append(info_line("자치법규기본정보", (("{}", "자치법규명"), ("{}", "지자체기관명"),
                                                  ("MST {}", "자치법규일련번호"), ("계통ID {}", "자치법규ID"),
                                                  ("시행 {}", "시행일자"), ("공포 {}", "공포일자"), ("제{}호", "공포번호"),
                                                  ("{}", "제개정정보")), "ordin"))
        out += ["\n" + (e.text or "").strip() for e in root.iter("조내용") if (e.text or "").strip()]
    else:                                                          # 해석례 등 — 짧은 값은 한 줄, 긴 값은 단락
        for e in root.iter():
            t = (e.text or "").strip()
            if e is root or len(e) or not t or e.tag.endswith(("링크", "코드")):
                continue
            out.append(f"{e.tag}: {t}" if len(t) <= 80 and "\n" not in t else f"\n[{e.tag}]\n{t}")
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# --byl — 본문 응답의 <별표단위>에서 별표·서식 하나를 뽑는다(기준일 별표)
# ---------------------------------------------------------------------------
# 별표 검색(licbyl·admbyl)은 별표마다 현행 1행만 준다. 기준일 판본의 별표는 그 버전 본문(eflaw MST+efYd,
# admrul 일련번호)의 <별표단위>에 있다 — JO를 주면 빠진다(외국환거래법 시행령 MST 256657 실측).
# 필드: 별표번호(4자리)·별표가지번호(2자리)·별표구분(법령 별표/서식, 행정규칙 별표/별지)·별표제목·
# 별표시행일자(법령만)·별표서식파일링크·별표내용. 자치법규 <별표단위>는 첨부 묶음이라 본문이 비어 있어 받지 않는다.
BYL_TARGETS_GET = ("law", "eflaw", "admrul")
BYL_KINDS = {"별표": ("별표",), "서식": ("서식", "별지")}


def _parse_byl(raw: str) -> tuple[int, int]:
    """'23'·'4의2'·'[별표 4의2]'·'별지 제3호서식' → (번호, 가지번호). 해석할 수 없으면 exit 2."""
    nums = re.findall(r"\d+", raw or "")
    if len(nums) == 1 or (len(nums) == 2 and "의" in raw):
        return int(nums[0]), int(nums[1]) if len(nums) == 2 else 0
    sys.stderr.write(f"ERROR: --byl '{raw}'를 해석할 수 없습니다(예: 23, 4의2, '별표 4의2').\n")
    sys.exit(2)


def _byl_label(kind: str, n: int, g: int) -> str:
    return f"[별표 {n}" + (f"의{g}" if g else "") + "]" if kind == "별표" else \
        f"[별지 제{n}호" + (f"의{g}" if g else "") + "서식]"


def _byl_content(text: str) -> str:
    """별표내용 정리 — 줄 끝 공백 제거, 줄 사이 빈 줄 1개(원문 줄바꿈 잔재)는 빼고 공백 행은 빈 줄 하나로."""
    lines = [ln.rstrip() for ln in (text or "").replace("\r", "").split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            continue
        if out and blank >= 2:
            out.append("")
        out.append(ln)
        blank = 0
    return "\n".join(out)


def _byl_extract(body: str, raw: str, kind: str) -> str:
    """본문 XML에서 --byl 별표(서식)를 찾아 '머리 한 줄 + 별표내용'으로. 없으면 그 버전의 목록을 보이고 exit 2."""
    n, g = _parse_byl(raw)
    root = _xml_root(body)
    units = list(root.iter("별표단위")) if root is not None else []
    kinds = BYL_KINDS[kind]

    def num(u, tag: str) -> int:
        v = (u.findtext(tag) or "").strip()
        return int(v) if v.isdigit() else -1

    hit = [u for u in units if (u.findtext("별표구분") or "").strip() in kinds
           and num(u, "별표번호") == n and max(num(u, "별표가지번호"), 0) == g]
    if not hit:
        same = [u for u in units if (u.findtext("별표구분") or "").strip() in kinds]
        listing = "\n".join(f"  {_byl_label(kind, num(u, '별표번호'), max(num(u, '별표가지번호'), 0))} "
                            f"{_cell(u.findtext('별표제목'))}" for u in same)
        sys.stderr.write(f"ERROR: 이 버전 본문에 {_byl_label(kind, n, g)}이(가) 없습니다 — "
                         f"{kind} {len(same)}건(전체 별표단위 {len(units)}건):\n" + (listing or "  (없음)") + "\n")
        sys.exit(2)
    out = []
    for u in hit:
        head = [_byl_label(kind, n, g) + " " + _cell(u.findtext("별표제목"))]
        if (u.findtext("별표시행일자") or "").strip():
            head.append(f"별표시행일자 {u.findtext('별표시행일자').strip()}")
        link = (u.findtext("별표서식파일링크") or "").strip()
        if link:
            head.append(_abs_url(link))
        out.append(" · ".join(head) + "\n\n" + _byl_content(u.findtext("별표내용") or ""))
    return "\n\n".join(out)


def _byl_check(args: argparse.Namespace, targets: tuple[str, ...]) -> None:
    """--byl 사용 조건 — target·--jo·type 검사(본문을 받기 전에)."""
    if not getattr(args, "byl", None):
        return
    if args.target not in targets:
        sys.stderr.write(f"ERROR: --byl은 target {'·'.join(targets)}에서만 씁니다."
                         + (" 자치법규 별표는 본문에 첨부 묶음만 있으므로 'search --target ordinbyl' → download로 받으세요."
                            if args.target == "ordin" else "") + "\n")
        sys.exit(2)
    if args.jo:
        sys.stderr.write("ERROR: --byl은 --jo와 함께 쓸 수 없습니다 — 조문을 지정하면 응답에 별표가 빠집니다.\n")
        sys.exit(2)
    if args.type.upper() != "XML":
        sys.stderr.write("ERROR: --byl은 type=XML에서만 동작합니다.\n")
        sys.exit(2)
    _parse_byl(args.byl)


def _emit_byl(body: str, url: str, args: argparse.Namespace) -> None:
    """--byl 출력 — 화면에는 별표 한 건, --save-to 파일에는 원시 응답."""
    body = _mask_oc_in_body(body, url)
    if args.save_to:
        with open(args.save_to, "w", encoding="utf-8") as f:
            f.write(body)
    print(_byl_extract(body, args.byl, args.byl_kind))


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

def cmd_get(args: argparse.Namespace) -> None:
    oc = resolve_oc(args.oc)

    # 별표·서식 target은 본문 조회를 지원하지 않음 (search + download 사용)
    if args.target in SEARCH_ONLY_TARGETS:
        sys.stderr.write(
            f"ERROR (target={args.target}): 별표·서식은 별도 조회 API가 없습니다.\n"
            f"  → 현행 파일: 'search --target {args.target} --query ...'로 목록을 조회하고,\n"
            f"     'download --from-search-xml <저장한 XML>' 또는 'download --url <파일URL>'로 다운로드하세요"
            "(검색은 별표마다 현행 1행만 줍니다).\n"
            "  → 기준일 별표: 'get-asof --lid <법령ID> --date <기준일> --byl <번호>' (행정규칙은 --target admrul).\n"
        )
        sys.exit(2)
    if args.ancyd:
        sys.stderr.write(
            "ERROR: lawService는 공포일자(--ancyd) 지정을 지원하지 않습니다 — 보내도 현행본을 돌려줍니다(2026-09-26 실측).\n"
            "  → 과거 본문은 'versions' → 'get-asof --date <기준일>', 또는 'get --target eflaw --mst <버전 MST> "
            "--efyd <시행일자>'. 공포일자 범위 검색은 'search --ancyd'.\n")
        sys.exit(2)
    if args.lang == "EN":
        _get_english(args, oc)
        return

    # target별 필요한 식별자 검증
    # - law:    MST(=법령일련번호) 또는 ID(=법령ID) 또는 LM — 공포일 기준 본문(공포본 전용 — XML/JSON은
    #           --promulgated가 없으면 언제나 시행일 기준 본문(eflaw)으로 대체)
    # - eflaw:  ID(=법령ID) 또는 LM → 현행 시행본 / MST+efYd → 특정 시행 버전
    # - admrul: ID(=행정규칙일련번호, 긴 숫자) 또는 LID 또는 LM
    # - ordin:  MST(=자치법규일련번호) 또는 ID(=자치법규ID) — LM(명칭)은 API가 지원하지 않는다(정식 명칭도 '일치 없음')
    # - expc:   ID(=법령해석례일련번호) — LM(안건명)도 지원하지 않는다(2026-09-27 실측)
    if args.target == "eflaw":
        if args.id or args.lm:
            if args.efyd:
                # ID·LM은 현행 시행본을 준다(공식 가이드). ID+efYd 조합은 서버가 HTTP 500을 낸다(실측).
                sys.stderr.write(
                    "NOTE (target=eflaw): --id/--lm은 현행 시행본을 조회하므로 --efyd를 무시합니다. "
                    "과거 시행본은 get-asof 또는 --mst <버전 MST> --efyd <시행일자>.\n"
                )
                args.efyd = None
        elif not (args.mst and args.efyd):
            sys.stderr.write(
                "ERROR (target=eflaw): 현행 시행본은 --id <법령ID> 또는 --lm <정식 법령명>, "
                "특정 시행 버전은 --mst <버전 MST> --efyd <그 버전의 시행일자>가 필요합니다.\n"
                "  버전 목록은 'versions --lid <법령ID>' 명령으로 확인하세요.\n"
            )
            sys.exit(2)
    elif args.target == "admrulOldAndNew":
        if not (args.id or args.lid or args.lm):
            sys.stderr.write(
                "ERROR (target=admrulOldAndNew): --id(행정규칙일련번호) / --lid(행정규칙ID) / --lm 중 하나는 필수.\n"
            )
            sys.exit(2)
    elif args.target == "law":
        if not (args.mst or args.id or args.lm):
            sys.stderr.write(
                "ERROR (target=law): --mst(=법령일련번호) / --id(=법령ID) / --lm 중 하나는 필수.\n"
            )
            sys.exit(2)
    elif args.target == "ordin":
        if args.lm or not (args.mst or args.id):
            sys.stderr.write(
                "ERROR (target=ordin): --mst(=자치법규일련번호) 또는 --id(=자치법규ID)가 필요합니다 — 명칭 조회(--lm)는 "
                "API가 지원하지 않습니다(정식 명칭도 '일치 없음').\n"
                "  → 'search --target ordin --query <조례명>'으로 자치법규일련번호를 확보하세요.\n"
            )
            sys.exit(2)
    elif args.target == "expc":
        if args.lm or not args.id:
            sys.stderr.write(
                "ERROR (target=expc): --id(=법령해석례일련번호)가 필요합니다 — 안건명 조회(--lm)는 API가 지원하지 않습니다.\n"
                "  → 'search --target expc --query <검색어>'로 법령해석례일련번호를 확보하세요.\n"
            )
            sys.exit(2)
    elif args.target == "admrul":
        if not (args.id or args.lid or args.lm):
            sys.stderr.write(
                "ERROR (target=admrul): --id(=행정규칙일련번호) / --lid / --lm 중 하나는 필수.\n"
            )
            sys.exit(2)
    else:
        sys.stderr.write(f"ERROR: 알 수 없는 target: {args.target}\n")
        sys.exit(2)

    params: dict[str, str] = {
        "OC": oc,
        "target": args.target,
        "type": args.type,
    }
    if args.mst:
        params["MST"] = args.mst
    if args.id:
        params["ID"] = args.id
    if args.lid:
        params["LID"] = args.lid
    if args.lm:
        params["LM"] = args.lm
    if args.jo:                     # 조회 전에 검증 — 해석 불가 입력이면 본문(최대 수십만 자)을 받기 전에 끝낸다
        _check_jo(args.jo, args.target)
    _byl_check(args, BYL_TARGETS_GET)
    _addenda_check(args, ("law", "eflaw"))
    # 행정규칙·자치법규는 API가 JO를 받지 않는다(보내도 전문 반환) — 스크립트가 받은 뒤 발췌한다.
    if args.jo and args.target in API_JO_TARGETS:
        params["JO"] = encode_jo(args.jo)
    if args.efyd:
        params["efYd"] = args.efyd
    # ancYd·LANG은 lawService가 무시한다(2026-09-26 실측) — 보내지 않는다. 영문본은 _get_english(target=elaw).

    url = build_url(BASE_SERVICE, params)
    if args.dry_run:
        print(_display_url(url))
        return

    law_xml = args.target == "law" and args.type.upper() in ("XML", "JSON")
    # law는 조문이 비어도 곧바로 끝내지 않는다 — 시행일 기준 본문에는 있을 수 있다(미시행 개정·공포 순서 역전).
    body, url = _fetch_with_jo(args.target, params, args.jo, args.type, args.no_cache,
                               exit_on_missing=not law_xml)

    if law_xml:
        pending = _pending_info(body)
        if not args.promulgated:
            in_promulgated = not (args.jo and args.type.upper() == "XML" and _jo_result_empty(body))
            body, url = _law_to_effective(body, url, oc, args, pending, in_promulgated)
        else:
            if pending:
                sys.stderr.write(f"⚠ 공포본 출력(--promulgated) — 아직 시행되지 않은 내용 포함: {pending}.\n")
            if args.jo and args.type.upper() == "XML" and _jo_result_empty(body):
                _jo_missing_exit(args.jo, params.get("JO"), "")
    elif args.target == "law":
        sys.stderr.write("NOTE: type=HTML은 공포본을 그대로 출력합니다 — 미시행 내용이 섞이거나 뒤에 공포됐으나 먼저 "
                         "시행된 개정이 빠질 수 있습니다. 현행 인용은 XML/JSON 또는 target=eflaw.\n")

    raw = None
    if args.jo and args.target in LOCAL_JO_TARGETS:
        raw, body = body, _extract_articles(body, args.target, args.jo, args.type)   # --save-to는 발췌 전 전문

    if args.byl:
        _emit_byl(body, url, args)
        return
    if args.addenda is not None:
        _emit_addenda(body, url, args, _addenda_nums(args.addenda))
        return
    _emit(body, url, args, _body_text if args.text else None, raw=raw)


def _elaw_articles(body: str, jo: str) -> tuple[list[str], str]:
    """영문본(elaw) 응답에서 --jo 조의 <Jo> 블록만(joNo 4자리·joBrNo 2자리 일치, 장 제목 제외). 서버는 JO를 무시한다."""
    n, g = _jo_pattern(jo)
    hit = [m.group(0) for m in re.finditer(r"<Jo\b[^>]*>.*?</Jo>", body, re.DOTALL)
           if f"<joNo>{n:04d}</joNo>" in m.group(0) and f"<joBrNo>{g:02d}</joBrNo>" in m.group(0)
           and "<joYn>N</joYn>" not in m.group(0)]          # joYn=N은 같은 번호에 붙은 편·장 제목
    return hit, f"Article {n}" + (f"-{g}" if g else "")


def _get_english(args: argparse.Namespace, oc: str) -> None:
    """get --lang EN — 영문 번역본은 lawService의 LANG이 아니라 target=elaw(ID=법령ID 또는 LM)로만 받는다.

    elaw 응답: <Law><InfSection>(lsId·ancYd·ancNo·lsNmEng)<JoSection><Jo>(joNo·joBrNo·joTtl·joCts)…
    <ArSection>(부칙)<BylSection>. JO를 무시하고 전문을 주므로 --jo는 로컬에서 발췌한다.
    """
    bad = ("--mst — 영문본은 법령ID(--id)·법령명(--lm)으로만 조회됩니다" if args.mst else
           "--efyd·--promulgated — 영문본은 번역 기준본 하나뿐입니다" if args.efyd or args.promulgated else
           "--byl·--addenda" if args.byl or args.addenda is not None else "")
    if args.target not in ("law", "eflaw") or not (args.id or args.lm) or bad:
        sys.stderr.write("ERROR: --lang EN은 target law·eflaw에서 --id <법령ID> 또는 --lm <법령명>으로만 씁니다"
                         + (f" — 함께 쓸 수 없는 옵션: {bad}" if bad else "") + ".\n")
        sys.exit(2)
    if args.jo:
        encode_jo(args.jo)
        _jo_note(args.jo)
    params = {"OC": oc, "target": "elaw", "type": args.type, "ID": args.id or "", "LM": "" if args.id else args.lm}
    url = build_url(BASE_SERVICE, params)
    if args.dry_run:
        print(_display_url(url))
        return
    try:
        body = http_get(url, no_cache=args.no_cache, fmt=args.type)
        if not args.id and _find_not_found(body):
            # elaw의 LM은 한글 법령명이 맞아도 못 찾는 일이 있다(형법 실측) — 법령ID로 다시 조회한다.
            lid = _law_id_by_name(oc, args.lm)
            if lid:
                url = build_url(BASE_SERVICE, {**params, "ID": lid, "LM": ""})
                body = http_get(url, no_cache=args.no_cache, fmt=args.type)
        _enforce_response_ok(body, url, html_ok=args.type.upper() == "HTML")
    except SystemExit as e:
        if e.code in (2, 3):
            sys.stderr.write("  → 영문 번역본이 없는 법령일 수 있습니다(영문본은 일부 법령만 제공). 한글 본문은 --lang 없이.\n")
        raise
    xml = args.type.upper() == "XML"
    if xml:
        root = _xml_root(body)
        inf = root.find("InfSection") if root is not None else None
        anc = (inf.findtext("ancYd") or "").strip() if inf is not None else ""
        no = _norm_prom_no(inf.findtext("ancNo") if inf is not None else "", "law")
        sys.stderr.write(f"NOTE: 영문 번역본(참고용·법적 효력 없음) — 번역 기준 공포 {anc or '미상'}"
                         + (f" 제{no}호" if no else "") + ", 현행 한글본과 다를 수 있음.\n")
    else:
        sys.stderr.write("NOTE: 영문 번역본(참고용·법적 효력 없음) — 현행 한글본과 다를 수 있음.\n")
    if args.jo:
        if not xml:
            sys.stderr.write("NOTE: 영문본의 --jo 발췌는 XML에서만 동작합니다 — 전문을 출력합니다.\n")
        else:
            hit, label = _elaw_articles(body, args.jo)
            if not hit:
                sys.stderr.write(f"ERROR: 영문본에 {label}이(가) 없습니다 — 번역 기준본 뒤에 신설됐거나 번역에서 빠졌을 수 "
                                 "있습니다. 한글 본문으로 확인하세요.\n")
                sys.exit(2)
            inf_xml = re.search(r"<InfSection>.*?</InfSection>", body, re.DOTALL)
            body = ('<?xml version="1.0" encoding="UTF-8"?><Law>' + (inf_xml.group(0) if inf_xml else "")
                    + "<JoSection>" + "".join(hit) + "</JoSection></Law>")
    _emit(body, url, args, _body_text if args.text else None)


def _law_id_by_name(oc: str, name: str) -> str:
    """법령명(띄어쓰기 무관) → 법령ID. 정확히 같은 이름이 없으면 ''."""
    body = http_get(build_url(BASE_SEARCH, {"OC": oc, "target": "law", "type": "XML", "query": name,
                                            "display": "100"}), no_cache=_NO_CACHE)
    root = _xml_root(body)
    key = re.sub(r"\s+", "", name or "")
    for el in (root.iter("law") if root is not None else []):
        if re.sub(r"\s+", "", el.findtext("법령명한글") or "") == key:
            return (el.findtext("법령ID") or "").strip()
    return ""


def _law_effective_params(body: str, args: argparse.Namespace) -> dict[str, str] | None:
    """공포본(target=law) 응답 → 같은 법령의 시행일 기준 본문(eflaw) 요청 파라미터. 만들 수 없으면 None.

    --mst 요청이면 그 버전의 시행 기준 문언(MST + efYd=<본문 시행일자>), --lm·--id 요청이면 현행(ID=<본문 법령ID>).
    조문이 빈 JO 응답에도 <기본정보>의 법령ID·시행일자는 들어 있다(민사소송법 MST 252393 JO=040202 실측).
    """
    if args.mst:
        eff = _first_tag_value(body, "시행일자")
        return {"target": "eflaw", "MST": args.mst, "efYd": eff} if _real_date(eff) else None
    law_id = _first_tag_value(body, "법령ID")
    return {"target": "eflaw", "ID": law_id} if law_id else None


def _law_to_effective(body: str, url: str, oc: str, args: argparse.Namespace,
                      pending: str, in_promulgated: bool = True) -> tuple[str, str]:
    """공포일 기준 본문(target=law)을 시행일 기준 본문(eflaw)으로 바꿔 받는다(--promulgated가 없으면 언제나).

    공포본은 그 버전이 공포된 시점의 문언이라 (1) 공포됐으나 아직 시행되지 않은 개정이 섞이고(pending),
    (2) 뒤에 공포됐으나 먼저 시행된 개정이 빠진다 — 민사소송법 법률 제19516호(2023. 7. 11. 공포, 2025. 7. 12. 시행)
    공포본에는 법률 제20003호(2024. 1. 16. 공포, 2025. 3. 1. 시행)의 제402조의2·제402조의3이 없다(2026-09-27 실측).
    (2)는 본문만으로 판별할 수 없으므로 판별하지 않고 언제나 바꾼다. pending이면 현행본(ID=법령ID)으로 바꾼다.
    """
    if pending:
        law_id = _first_tag_value(body, "법령ID")
        if not law_id:
            sys.stderr.write(f"⚠ 아직 시행되지 않은 내용 포함({pending}) — 법령ID를 찾지 못해 공포본을 그대로 출력합니다.\n")
            return body, url
        sys.stderr.write(
            f"⚠ 공포일 기준 본문(target=law)에 아직 시행되지 않은 내용이 있습니다 — {pending}.\n"
            f"  → 시행일 기준 현행본(target=eflaw, 법령ID {law_id})으로 대체해 출력합니다. "
            "공포본 그대로가 필요하면 --promulgated.\n"
        )
        ep: dict[str, str] | None = {"target": "eflaw", "ID": law_id}
    else:
        ep = _law_effective_params(body, args)
        if not ep:
            sys.stderr.write("⚠ 시행일 기준 본문으로 바꿀 식별자(법령ID·시행일자)를 찾지 못해 공포본을 그대로 출력합니다 — "
                             "현행 인용은 get --target eflaw.\n")
            if args.jo and args.type.upper() == "XML" and _jo_result_empty(body):
                _jo_missing_exit(args.jo, encode_jo(args.jo), "")
            return body, url
        sys.stderr.write(
            "NOTE: target=law(공포본)는 뒤에 공포됐으나 먼저 시행된 개정이 빠지거나 아직 시행되지 않은 개정이 섞일 수 있어 "
            f"시행일 기준 본문(eflaw {', '.join(f'{k}={v}' for k, v in ep.items() if k != 'target')})으로 출력합니다 "
            "— 공포본은 --promulgated\n")
    eparams = {"OC": oc, "type": args.type, **ep}
    if args.jo:
        eparams["JO"] = encode_jo(args.jo)
    eurl = build_url(BASE_SERVICE, eparams)
    probe = http_get(eurl, no_cache=args.no_cache, strict_errors=False, fmt=args.type)
    if _find_not_found(probe):
        head = _first_tag_value(body, "시행일자")
        if pending:
            sys.stderr.write(
                f"ERROR: 현행 시행본이 없습니다 — 아직 시행 전인 법령입니다(시행일 {head}). "
                "공포본은 --promulgated로 받고, 인용 시 시행 예정임을 밝히세요.\n")
        else:
            sys.stderr.write(f"ERROR: 시행일 기준 본문(eflaw)을 찾지 못했습니다(시행일 {head}) — "
                             "versions로 버전을 확인하고, 공포본은 --promulgated로 받으세요.\n")
        sys.exit(2)
    _enforce_response_ok(probe, eurl)
    if not (args.jo and args.type.upper() == "XML" and _jo_result_empty(probe)):
        return probe, eurl
    if not in_promulgated:
        hint = "공포본·시행본 모두에 없는 조문입니다 — 조문번호를 확인하세요."
    elif pending:
        hint = ("현행 시행본에 이 조문이 없습니다 — 공포됐으나 아직 시행되지 않은 신설 조문일 수 있습니다"
                "(공포본 확인: --promulgated).")
    else:
        hint = ("공포본에는 있으나 시행일 기준 본문에는 없는 조문입니다 — 뒤에 공포됐으나 먼저 시행된 개정으로 "
                "삭제·이동됐을 수 있습니다(공포본: --promulgated).")
    return _fetch_with_jo("eflaw", eparams, args.jo, args.type, args.no_cache, missing_hint=hint)


# API가 JO 파라미터를 지원하는 target / 스크립트가 본문에서 조를 발췌하는 target
API_JO_TARGETS = {"law", "eflaw"}
LOCAL_JO_TARGETS = {"admrul", "ordin"}


def _fetch_with_jo(target: str, params: dict[str, str], jo: str | None, fmt: str,
                   no_cache: bool, missing_hint: str = "",
                   exit_on_missing: bool = True) -> tuple[str, str]:
    """본문 조회 + JO 자동 폴백. (본문, 최종 URL)을 반환한다.

    법령(law/eflaw) 본문에서 JO를 지정했는데 결과가 비면(조문 컨테이너 부재), 대체 인코딩으로
    1회 자동 재시도한다(예: 039000 → 0390). type=XML일 때만 판정 가능하므로 그 경우에만
    폴백한다. 그래도 비면 exit_on_missing이면 조문 없음으로 exit 2, 아니면 빈 본문을 돌려준다.
    """
    url = build_url(BASE_SERVICE, params)
    body = http_get(url, no_cache=no_cache, strict_errors=True, fmt=fmt)
    if not (jo and target in API_JO_TARGETS and fmt.upper() == "XML" and _jo_result_empty(body)):
        return body, url
    alt = alt_encode_jo(jo)
    if alt and alt != params.get("JO"):
        sys.stderr.write(
            f"NOTE: JO={params.get('JO')} 결과가 비어 대체 인코딩 JO={alt}로 자동 재시도합니다.\n"
        )
        retry_params = dict(params)
        retry_params["JO"] = alt
        retry_url = build_url(BASE_SERVICE, retry_params)
        retry_body = http_get(retry_url, no_cache=no_cache, strict_errors=True, fmt=fmt)
        if not _jo_result_empty(retry_body):
            return retry_body, retry_url
    if not exit_on_missing:
        return body, url
    _jo_missing_exit(jo, params.get("JO"), missing_hint)
    return body, url  # unreachable


def _jo_missing_exit(jo: str, code: str | None, hint: str) -> None:
    sys.stderr.write(
        f"ERROR: 조문 {jo}을(를) 찾지 못했습니다(JO={code}). "
        + (hint or "조문번호·가지조 표기('제10조의2')를 확인하세요.") + "\n"
    )
    sys.exit(2)


def _first_tag_value(body: str, tag: str) -> str:
    """XML·JSON 본문에서 tag의 첫 값(CDATA 포함)을 찾아 반환. 없으면 ''."""
    m = re.search(rf"<{tag}>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{tag}>", body, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(rf'"{tag}"\s*:\s*"([^"]*)"', body)
    return m.group(1).strip() if m else ""


def _pending_info(body: str, today: str | None = None) -> str:
    """공포일 기준 법령 본문에 기준일(기본: 오늘) 현재 미시행 내용이 있으면 요약 문자열, 없으면 ''.

    - 기본정보 <시행일자>가 오늘보다 뒤: 본문 전체가 시행 전 공포본(예: 전자금융거래법 MST 280277,
      검색 행은 '20251216 현행'인데 본문 시행일자 20261217).
    - <조문시행일자문자열>에 오늘보다 뒤인 날짜: 일부 조항만 시행 전
      (예: 개인정보 보호법 '20270701:제32조의2제1항 단서,제75조제2항제15호').
    """
    today = today or _today()
    parts: list[str] = []
    head = _first_tag_value(body, "시행일자")
    if len(head) == 8 and head.isdigit() and head > today:
        parts.append(f"본문 시행일자 {head}")
    for tag, what in (("조문시행일자문자열", ""), ("별표시행일자문자열", "별표 ")):
        jstr = _first_tag_value(body, tag)
        for m in re.finditer(r"(\d{8})\s*:\s*(.*?)(?=\s*[/;|]?\s*\d{8}\s*:|$)", jstr, re.DOTALL):
            if m.group(1) > today:
                parts.append(f"{m.group(1)} 시행: {what}{m.group(2).strip().rstrip(',;/')}")
    return "; ".join(parts)


def _jo_pattern(jo: str) -> tuple[int, int]:
    """조문번호 입력 → (조, 가지조). encode_jo의 6자리 결과를 해석한다."""
    code = encode_jo(jo)
    if not (len(code) == 6 and code.isdigit()):
        sys.stderr.write(f"ERROR: 조문번호 '{jo}'를 해석할 수 없습니다(예: 7, 제7조, 제10조의2).\n")
        sys.exit(2)
    return int(code[:4]), int(code[4:])


_ADMRUL_JO_RE = re.compile(r"^\s*제?\s*(\d{1,4}(?:\s*-\s*\d{1,4})+)\s*조?\s*(?:의\s*(\d{1,2})\s*조?)?")
# 한 블록짜리 행정규칙 본문의 절단 경계 — 줄머리 조문 표제('제7-14조(', '제3조의2(', '제5조 삭제')와 편·장·절·관 제목 줄.
_ADMRUL_ART_HEAD_RE = re.compile(r"(?m)^[ \t\u3000]*(제\s*\d+(?:\s*-\s*\d+)*\s*조(?:\s*의\s*\d+)?\s*(?:\(|삭제))")
_ADMRUL_TITLE_RE = re.compile(r"(?m)^[ \t\u3000]*제\s*\d+\s*[편장절관](?:\s*의\s*\d+)?(?=\s|$)")


def _admrul_jo(raw: str) -> tuple[str, str]:
    """행정규칙 조문번호 → (표제 정규식, 표시 라벨).

    금융 감독규정 등은 편·장식 번호('제7-14조', '제10-21조의2')를 쓴다. '7-14'·'제7-14조'·'제7-14조의2'·
    '10-21의2'와 기존 형식('7', '제7조의2')을 받는다. 정규식은 표제 첫머리에 맞추며 가지조 경계를 지킨다
    ('제7-14조'는 '제7-14조의2'·'제7-141조'를 잡지 않는다). 해석할 수 없으면 exit 2.
    """
    s = (raw or "").strip()
    m = _ADMRUL_JO_RE.match(s)
    if m and (not s[m.end():].strip() or _JO_TAIL_RE.fullmatch(s[m.end():])):
        nums = [int(x) for x in re.split(r"\s*-\s*", m.group(1))]
        g = int(m.group(2) or 0)
        num_re = r"\s*-\s*".join(str(x) for x in nums)
        label = f"제{'-'.join(map(str, nums))}조" + (f"의{g}" if g else "")
    elif m:
        sys.stderr.write(f"ERROR: 조문번호 '{raw}'를 해석할 수 없습니다 — 행정규칙은 '7', '제7조의2', '7-14', "
                         "'제7-14조의2'처럼 조까지만 쓰세요.\n")
        sys.exit(2)
    else:
        n, g = _jo_pattern(s)
        num_re, label = str(n), f"제{n}조" + (f"의{g}" if g else "")
    tail = rf"\s*의\s*{g}(?!\d)" if g else r"(?!\s*의\s*\d)"
    return rf"제\s*{num_re}\s*조{tail}", label


def _admrul_jo_note(raw: str) -> None:
    """행정규칙 --jo 선검증 — 해석 불가면 exit 2, 항·호가 붙어 있으면 조 전체를 발췌한다고 알린다."""
    _admrul_jo(raw)
    m = _ADMRUL_JO_RE.match(raw.strip())
    if not m:
        _jo_note(raw)
    elif re.search(r"\d", raw.strip()[m.end():]):
        sys.stderr.write(f"NOTE: '{raw}'의 항·호 부분은 받지 않아 조 전체를 발췌합니다 — 해당 항·호는 본문에서 확인하세요.\n")


def _check_jo(raw: str, target: str) -> None:
    """--jo 선검증 — 행정규칙은 편·장식 번호('7-14')도 받고, 그 밖의 target은 encode_jo 규칙(해석 불가 exit 2)."""
    if target == "admrul":
        _admrul_jo_note(raw)
    else:
        encode_jo(raw)
        _jo_note(raw)


def _admrul_cut(text: str, head_re: re.Pattern) -> list[str]:
    """한 블록에 여러 조가 이어진 행정규칙 텍스트에서 head_re로 시작하는 조의 구간만 잘라 낸다."""
    heads = [m.start(1) for m in _ADMRUL_ART_HEAD_RE.finditer(text)]
    bounds = sorted(set(heads + [m.start() for m in _ADMRUL_TITLE_RE.finditer(text)] + [len(text)]))
    return [text[s:next(b for b in bounds if b > s)].rstrip() for s in heads if head_re.match(text, s)]


def _extract_articles(body: str, target: str, jo: str, fmt: str) -> str:
    """행정규칙·자치법규 본문에서 지정한 조만 남긴다(XML 원문을 잘라 CDATA 보존).

    API는 두 target에서 JO를 받지 않아 --jo를 주어도 전문(수만~수십만 자)을 돌려준다.
      - admrul: <조문내용><![CDATA[제7조(…)…]]></조문내용> 이 평탄하게 반복 → 첫머리 '제N조(의M)' 일치.
                조 여럿이 한 블록에 이어진 규정(외국환거래규정 — 1블록 18만 자)은 줄머리 조문 표제·편장절관 제목
                줄을 경계로 지정 조의 구간만 잘라 CDATA로 감싼다. 편·장식 번호('7-14')는 _admrul_jo.
      - ordin : <조 조문번호='000700'>…</조> → 조문번호 일치
    기본정보 블록은 유지한다. 해당 조가 없으면 exit 2.
    """
    if fmt.upper() != "XML":
        sys.stderr.write(f"NOTE: {target}의 --jo 발췌는 XML에서만 동작합니다 — 전문을 출력합니다.\n")
        return body
    if target == "admrul":
        pat, label = _admrul_jo(jo)
        info = re.search(r"<행정규칙기본정보>.*?</행정규칙기본정보>", body, re.DOTALL)
        head_re = re.compile(pat)
        blocks = []
        for m in re.finditer(r"<조문내용>.*?</조문내용>", body, re.DOTALL):
            text = _cdata_text(m.group(0), "조문내용")
            if len(_ADMRUL_ART_HEAD_RE.findall(text)) > 1:     # 조 여럿이 한 블록 — 지정 조 구간만 잘라 낸다
                blocks += ["<조문내용><![CDATA[" + seg.replace("]]>", "]]]]><![CDATA[>") + "]]></조문내용>"
                           for seg in _admrul_cut(text, head_re)]
            elif head_re.match(text.lstrip()):
                blocks.append(m.group(0))                     # 한 블록 = 한 조(통상 형식) — 원문 블록 그대로
        root_open, root_close = "<AdmRulService>", "</AdmRulService>"
    else:
        n, g = _jo_pattern(jo)
        label = f"제{n}조" + (f"의{g}" if g else "")
        info = re.search(r"<자치법규기본정보>.*?</자치법규기본정보>", body, re.DOTALL)
        code = f"{n:04d}{g:02d}"
        blocks = [m.group(0) for m in re.finditer(r"<조\s[^>]*>.*?</조>", body, re.DOTALL)
                  if f"<조문번호>{code}</조문번호>" in m.group(0)]
        root_open, root_close = "<LawService>", "</LawService>"
    if not blocks:
        sys.stderr.write(f"ERROR: 본문에서 {label}을(를) 찾지 못했습니다 — 조문번호를 확인하세요"
                         "(행정규칙 일부는 조문 형식이 아닙니다).\n")
        sys.exit(2)
    sys.stderr.write(f"NOTE: {target} 본문에서 {label}만 발췌했습니다(API는 {target}의 JO를 지원하지 않음).\n")
    return ('<?xml version="1.0" encoding="UTF-8"?>' + root_open
            + (info.group(0) if info else "") + "".join(blocks) + root_close)


def _cdata_text(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{tag}>", block, re.DOTALL)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# download — 별표·서식 파일 다운로드 + (PDF) 텍스트 추출
# ---------------------------------------------------------------------------

# 별표서식 검색 응답에서 다운로드 URL을 담는 후보 태그.
# 우선순위: PDF 변환본 > 원본(HWP 등). PDF는 텍스트 추출에 유리하므로 가능하면 PDF를 받는다.
BYL_LINK_TAG_HINTS_PDF = (
    "별표서식PDF파일링크", "PDF파일링크", "PDF링크",
)
BYL_LINK_TAG_HINTS_RAW = (
    "별표서식파일링크", "별표파일링크", "별지파일링크", "서식파일링크",
    "filelink", "fileLink", "서식링크", "별표링크",
)
# 통합(매칭 검사용)
BYL_LINK_TAG_HINTS = BYL_LINK_TAG_HINTS_PDF + BYL_LINK_TAG_HINTS_RAW

BYL_NAME_TAG_HINTS = (
    "별표명", "별지명", "서식명", "별표서식명", "별표제목",
)
# 별표명이 비어 있을 때 폴백 이름을 만들기 위한 보조 필드
BYL_FALLBACK_NAME_FIELDS = (
    "관련법령명", "관련자치법규명", "관련행정규칙명",
)
BYL_NUMBER_FIELDS = (
    "별표번호",
)


def _abs_url(url: str) -> str:
    """상대 경로(/DRF/...)는 https://www.law.go.kr 호스트로 보정."""
    url = url.strip()
    if url.startswith("/"):
        return "https://www.law.go.kr" + url
    if url.startswith("http://"):
        # 강제 HTTPS로 승격
        return "https://" + url[len("http://"):]
    return url


def _strip_html_tags(s: str) -> str:
    """별표명 CDATA에 끼는 <strong class="..."> 같은 검색어 강조 태그 제거."""
    import re as _re
    if not s:
        return s
    s = _re.sub(r"<[^>]+>", "", s)
    # HTML entity 정리 (필요시 더 추가)
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    return s.strip()


def _safe_filename(name: str, default: str = "byl_file") -> str:
    """파일명에서 OS-금지 문자 제거 + HTML 태그 제거."""
    bad = '<>:"/\\|?*\n\r\t'
    cleaned_name = _strip_html_tags(name) if name else default
    cleaned = "".join("_" if c in bad else c for c in cleaned_name).strip()
    # 공백 압축
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned[:200] or default


def _guess_ext(url: str, content_type: str = "") -> str:
    url_lower = url.lower()
    for ext in (".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xlsx", ".xls", ".png", ".jpg", ".jpeg"):
        if ext in url_lower:
            return ext
    ct = content_type.lower()
    if "pdf" in ct:
        return ".pdf"
    if "hwp" in ct:
        return ".hwp"
    if "image/png" in ct:
        return ".png"
    if "image/jp" in ct:
        return ".jpg"
    return ".bin"


class DownloadError(Exception):
    """별표·서식 다운로드 실패(HTTP 오류·빈 응답·오류 페이지) — 파일을 쓰지 않는다."""


def _download_payload_error(data: bytes, content_type: str) -> str | None:
    """받은 내용이 파일이 아니면 사유, 파일이면 None.

    없는 flSeq에도 서버는 HTTP 200으로 59B짜리 `<script>alert(' 파일이 없습니다. ');</script>`(text/html)를 주고,
    0바이트 image/gif를 주기도 한다(2026-09-26 실측) — 그대로 저장하면 가짜 성공이 된다.
    """
    if not data:
        return "빈 응답(0바이트)"
    head = data[:4096]
    if any(m.encode(enc) in head for m in ("파일이 없습니다", "파일이 존재하지") for enc in ("utf-8", "cp949")):
        return "서버 안내: 파일이 없습니다"
    if "text/html" in (content_type or "").lower():
        return f"파일이 아닌 HTML 응답({len(data)}B)"
    return None


def _http_download(url: str, out_path: str, timeout: int = 30) -> tuple[str, str]:
    """파일 바이너리 다운로드. (저장경로, content_type) 반환. 실패·가짜 파일이면 DownloadError(파일 안 씀)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "claude-korean-law-api/1.0 (+skill:korean-law-api)",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise DownloadError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise DownloadError(f"연결 실패: {e.reason}") from None
    except (TimeoutError, OSError) as e:
        raise DownloadError(f"{type(e).__name__}: {e}") from None
    err = _download_payload_error(data, ct)
    if err:
        raise DownloadError(err)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path, ct


def _extract_pdf_text(pdf_path: str) -> str | None:
    """pdftotext가 있으면 사용해 텍스트 추출. 없거나 실패하면 None."""
    import shutil
    import subprocess
    if not shutil.which("pdftotext"):
        print("WARN: pdftotext(poppler-utils) 미설치 — PDF 저장만 하고 텍스트 추출은 생략합니다.",
              file=sys.stderr)
        return None
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, "-"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return result.stdout
        print(f"WARN: pdftotext 실패 (exit {result.returncode}): {pdf_path}", file=sys.stderr)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"WARN: pdftotext 실행 오류: {e}", file=sys.stderr)
    return None


def _collect_links_from_xml(xml_text: str) -> list[dict]:
    """검색 응답 XML에서 별표·서식 항목별 (이름, 링크) 페어를 수집한다.

    동작:
      - 한 항목 내에 여러 다운로드 링크 후보가 있으면 PDF 변환본을 우선 선택.
      - <별표명>이 비어 있으면 (관련법령명/관련행정규칙명/관련자치법규명) + 별표번호로 폴백.
      - 다운로드 링크가 전혀 없는 항목은 결과에서 제외(스킵 사유 노출).

    반환: [{"name": ..., "link": ..., "is_pdf": bool, "raw": {...}}, ...]
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        sys.stderr.write(f"XML 파싱 실패: {e}\n")
        return []

    items: list[dict] = []
    skipped_no_link = 0

    for item in root.iter():
        children = list(item)
        if not children:
            continue

        record: dict[str, str] = {}
        link_pdf: str | None = None
        link_raw: str | None = None
        name: str | None = None

        for c in children:
            text = (c.text or "").strip() if c.text is not None else ""
            record[c.tag] = text
            if not text:
                continue
            # PDF 우선
            if any(h in c.tag for h in BYL_LINK_TAG_HINTS_PDF):
                if link_pdf is None:
                    link_pdf = text
            # 원본 (PDF가 없으면 사용)
            elif any(h in c.tag for h in BYL_LINK_TAG_HINTS_RAW):
                if link_raw is None:
                    link_raw = text
            # 별표명
            if any(h in c.tag for h in BYL_NAME_TAG_HINTS):
                if not name:  # 첫 매칭값을 채택
                    name = _strip_html_tags(text)

        # 다운로드 링크가 없는 항목은 별표·서식 자체가 아니거나 다운로드 미제공
        if not (link_pdf or link_raw):
            # 단, 항목으로 추정되는 경우(별표일련번호 같은 키 필드가 있으면) skip 카운트만 증가
            if "별표일련번호" in record:
                skipped_no_link += 1
            continue

        # 폴백 이름: 별표명이 빈 경우 관련규칙명 + 별표번호 조합
        if not name:
            base = ""
            for k in BYL_FALLBACK_NAME_FIELDS:
                if record.get(k):
                    base = _strip_html_tags(record[k])
                    break
            num = ""
            for k in BYL_NUMBER_FIELDS:
                if record.get(k):
                    num = record[k]
                    break
            if base and num:
                name = f"{base}_별표{num}"
            elif base:
                name = base
            elif record.get("별표일련번호"):
                name = f"byl_{record['별표일련번호']}"
            else:
                name = "byl_file"

        items.append({
            "name": name,
            "link": link_pdf or link_raw,  # PDF 우선
            "is_pdf": link_pdf is not None,
            "raw": record,
        })

    if skipped_no_link:
        sys.stderr.write(
            f"NOTE: {skipped_no_link}건의 별표·서식 항목은 다운로드 링크가 제공되지 않아 스킵.\n"
        )
    return items


def _default_out_dir() -> str:
    """download 기본 저장 폴더. 현재 폴더가 스킬 설치 폴더 안이거나 쓰기 불가면 임시 폴더를 쓴다.

    Cowork 등에서는 스킬이 읽기 전용으로 마운트되고, 설치 폴더에 파일을 떨어뜨리면 업데이트 때 사라진다.
    """
    cwd = os.path.realpath(os.getcwd())
    skill_dir = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    inside_skill = cwd == skill_dir or cwd.startswith(skill_dir + os.sep)
    if inside_skill or not os.access(cwd, os.W_OK):
        out = os.path.join(tempfile.gettempdir(), "law_api_byl")
        sys.stderr.write(f"NOTE: 현재 폴더가 스킬 설치 폴더이거나 쓰기 불가라 {out}에 저장합니다 — "
                         "작업 폴더에 두려면 --out-dir <작업 폴더>를 주세요.\n")
        return out
    return "./byl_downloads"


def _search_xml_empty(xml_text: str) -> bool:
    """검색 응답 XML이 0건(totalCnt 0 또는 항목 element 없음)이면 True."""
    root = _xml_root(xml_text)
    if root is None:
        return False
    total = (root.findtext("totalCnt") or "").strip()
    return total == "0" or not any(len(c) for c in root)


def _download_one(url: str, out_path: str, force_ext: str | None = None) -> tuple[str, str]:
    """1건 저장 + Content-Type 기반 확장자 보정. DownloadError는 호출부가 처리한다."""
    saved, ct = _http_download(url, out_path)
    new_ext = force_ext or _guess_ext(url, ct)
    if not saved.lower().endswith(new_ext):
        new_path = os.path.splitext(saved)[0] + new_ext
        os.replace(saved, new_path)
        saved = new_path
    return saved, ct


def _maybe_extract(saved: str, args: argparse.Namespace, indent: str = "") -> None:
    if args.extract_text and saved.lower().endswith(".pdf"):
        text = _extract_pdf_text(saved)
        if text is not None:
            txt_path = os.path.splitext(saved)[0] + ".txt"
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"{indent}TEXT: {txt_path}")
        else:
            sys.stderr.write(f"{indent}NOTE: pdftotext 미설치 또는 추출 실패 — PDF만 저장됨({saved}).\n")
    elif args.extract_text and saved.lower().endswith((".hwp", ".hwpx")):
        sys.stderr.write(f"{indent}NOTE: HWP/HWPX는 자동 텍스트 추출 미지원 — 파일만 저장됨({saved}).\n")


def cmd_download(args: argparse.Namespace) -> None:
    # 인자 검증을 먼저 — 인자가 없으면 저장 폴더를 만들지 않고 끝낸다(폴더는 저장 직전에 만든다).
    if not (args.url or args.from_search_xml):
        sys.stderr.write("ERROR: --url 또는 --from-search-xml 중 하나를 지정해야 합니다.\n")
        sys.exit(2)
    xml_text = ""
    if not args.url:                            # 검색 XML을 먼저 읽는다 — 못 읽으면 저장 폴더 안내 없이 끝낸다
        try:
            with open(args.from_search_xml, "r", encoding="utf-8") as f:
                xml_text = f.read()
        except (OSError, UnicodeDecodeError) as e:
            sys.stderr.write(f"ERROR: 검색 XML 파일을 읽을 수 없습니다: {args.from_search_xml} ({e.__class__.__name__})\n")
            sys.exit(2)
    out_dir = args.out_dir or _default_out_dir()

    # 1) URL 직접 지정 모드
    if args.url:
        url = _abs_url(args.url)
        ext = _guess_ext(url)
        fname_base = _safe_filename(args.filename or os.path.basename(urllib.parse.urlparse(url).path) or "byl_file")
        out_path = os.path.join(out_dir, fname_base if fname_base.endswith(ext) else fname_base + ext)
        if args.dry_run:
            print(_display_url(url))
            print(f"-> {out_path} (확장자는 받은 Content-Type으로 보정될 수 있음)")
            return
        try:
            saved, ct = _download_one(url, out_path)
        except (DownloadError, OSError) as e:
            sys.stderr.write(f"FAILED: {_display_url(url)} ({e})\n")
            sys.exit(3)
        print(f"SAVED: {saved} (Content-Type: {ct})")
        _maybe_extract(saved, args)
        return

    # 2) 검색 응답 XML 파싱 모드
    items = _collect_links_from_xml(xml_text)
    if not items:
        if _search_xml_empty(xml_text):
            sys.stderr.write("ERROR: 검색 결과가 0건입니다 — 별표 검색어는 별표명 기준이고, 모법별 목록은 "
                             "--search 2(관련법령명)로 검색하세요.\n")
        else:
            sys.stderr.write(
                "WARN: 응답 XML에서 다운로드 URL을 찾지 못했습니다. "
                "라이브 호출로 응답 구조를 확인 후 BYL_LINK_TAG_HINTS를 보강해야 합니다.\n"
            )
        sys.exit(1)
    limit = args.limit if args.limit > 0 else len(items)
    target_items = items[:limit]
    plan = []
    for idx, it in enumerate(target_items):
        url = _abs_url(it["link"])
        # is_pdf=True인 경우 확장자를 .pdf로 강제 (URL에서 확장자 추론이 어려울 때 안전)
        ext = ".pdf" if it.get("is_pdf") else _guess_ext(url)
        plan.append((url, os.path.join(out_dir, _safe_filename(f"{idx+1:03d}_{it['name']}") + ext), it))
    if args.dry_run:
        print(f"INFO: 총 {len(items)}건 중 {len(plan)}건 — 호출·저장하지 않음(dry-run)")
        for i, (url, out_path, _) in enumerate(plan):
            print(f"[{i+1}/{len(plan)}] {_display_url(url)} -> {out_path}")
        return
    print(f"INFO: 총 {len(items)}건 중 {len(plan)}건 다운로드 시작 "
          f"(PDF 변환본 우선 선택; PDF 없으면 원본 사용)")
    failed = 0
    for idx, (url, out_path, it) in enumerate(plan):
        try:
            saved, _ = _download_one(url, out_path, ".pdf" if it.get("is_pdf") else None)
        except (DownloadError, OSError) as e:
            failed += 1
            sys.stderr.write(f"[{idx+1}] FAILED: {_display_url(url)}  ({e})\n")
            continue
        kind = "PDF" if it.get("is_pdf") else "RAW"
        print(f"[{idx+1}/{len(plan)}] SAVED ({kind}): {saved}")
        _maybe_extract(saved, args, indent="        ")
    if failed:
        sys.stderr.write(f"{len(plan)}건 중 {failed}건 실패\n")
        sys.exit(3 if failed == len(plan) else 1)


_JO_HEAD_RE = re.compile(r"^\s*제?\s*(\d{1,4})\s*조?\s*(?:의\s*(\d{1,2})\s*조?)?")
_JO_TAIL_RE = re.compile(r"\s*(?:(?:제?\s*\d+\s*(?:항|호))|(?:[가-힣]\s*목))(?:\s*(?:제?\s*\d+\s*(?:항|호)|[가-힣]\s*목))*"
                         r"\s*(?:본문|단서|전단|후단)?\s*")


def encode_jo(raw: str) -> str:
    """
    조문번호 인코딩 — 조 4자리 + 가지조 2자리.
    - '390', '제390조' → 039000 / '제390조의2', '390의2' → 039002 (가지조를 숫자에 이어붙이지 않는다).
    - '제7조제1항'처럼 항·호가 붙으면 조까지만 쓴다(항·호는 API가 받지 않음 — _jo_note가 알림).
    - 숫자만 5자리 이상이면 이미 인코딩된 값으로 보고 그대로 보낸다(공식 JO는 6자리).
    - 해석할 수 없는 형태('390-2' 등)는 다른 조를 조회하지 않도록 exit 2.
    """
    raw = raw.strip()
    if raw.isdigit():
        return raw.zfill(4) + "00" if len(raw) <= 4 else raw
    m = _JO_HEAD_RE.match(raw)
    rest = raw[m.end():] if m else raw
    # '제1항'·'2호'처럼 '조' 없이 항·호만 쓴 입력을 조 번호로 읽지 않는다.
    no_jo_mark = bool(m) and "조" not in m.group(0) and "의" not in m.group(0) and rest.strip() != ""
    if not m or no_jo_mark or (re.search(r"\d", rest) and not _JO_TAIL_RE.fullmatch(rest)):
        if not re.search(r"\d", raw):
            return raw
        sys.stderr.write(
            f"ERROR: 조문번호 '{raw}'를 해석할 수 없습니다 — '390', '제390조', '제10조의2'처럼 조까지만 쓰세요"
            "(항·호는 받은 본문에서 확인).\n")
        sys.exit(2)
    return f"{int(m.group(1)):04d}{int(m.group(2) or 0):02d}"


def _jo_note(raw: str) -> None:
    """--jo에 항·호가 붙어 있으면 조 전체를 조회한다고 알린다."""
    if raw.strip().isdigit():
        return
    m = _JO_HEAD_RE.match(raw.strip())
    if m and re.search(r"\d", raw.strip()[m.end():]):
        sys.stderr.write(f"NOTE: '{raw}'의 항·호 부분은 API가 받지 않아 조 전체를 조회합니다 — 해당 항·호는 본문에서 확인하세요.\n")


def alt_encode_jo(raw: str) -> str | None:
    """encode_jo의 대체(폴백) 인코딩.

    기본 인코딩은 6자리(조 4 + 가지조 2)이지만, 일부 응답은 4자리(조 번호만) 형태를
    요구한다. 기본값이 6자리이고 가지조가 없으면(끝 두 자리 '00') 4자리 변형을 반환한다.
    대체할 형태가 없으면 None.
    """
    primary = encode_jo(raw)
    digits = "".join(ch for ch in primary if ch.isdigit())
    if len(digits) == 6 and digits.endswith("00"):
        alt = digits[:4]           # 예: 039000 -> 0390, 000200 -> 0002
        if alt != primary:
            return alt
    return None


# JO 지정 결과가 비었는지 판정할 때 찾는 '실제 조문' 태그.
_JO_CONTENT_TAGS = ("조문단위", "조내용", "조문내용")


def _jo_result_empty(body: str) -> bool:
    """JO를 지정한 본문 응답에 실제 조문 내용이 하나도 없으면 True.

    XML로 파싱해 조문 컨테이너 태그(조문단위/조내용/조문내용) 존재를 본다.
    파싱 실패(HTML 오류 등)나 판정 불가 시에는 '비었다'로 단정하지 않는다(False).
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return False
    for tag in _JO_CONTENT_TAGS:
        if next(root.iter(tag), None) is not None:
            return False
    return True



# ---------------------------------------------------------------------------
# versions / get-asof — 과거(연혁) 법령 조회 (행위시·처분시 기준)
# ---------------------------------------------------------------------------

def _dot_date(yyyymmdd: str) -> str:
    """20200324 → '2020. 3. 24.' (판례식 표기)."""
    s = (yyyymmdd or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{int(s[:4])}. {int(s[4:6])}. {int(s[6:])}."
    return s


def _prev_day(yyyymmdd: str) -> str:
    from datetime import datetime, timedelta
    try:
        return (datetime.strptime(yyyymmdd, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    except (ValueError, TypeError):
        return ""


def _search_pages(oc: str, target: str, base_params: dict[str, str], max_pages: int = 10,
                  item_tag: str = "law") -> list:
    """lawSearch.do 페이지 순회 — 항목 element 목록 반환 (law/eflaw/ordin은 <law>, admrul은 <admrul>)."""
    items: list = []
    page = 1
    while page <= max_pages:
        params = dict(base_params)
        params.update({"OC": oc, "target": target, "type": "XML",
                       "display": "100", "page": str(page)})
        body = http_get(build_url(BASE_SEARCH, params), no_cache=_NO_CACHE)
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            sys.stderr.write(f"WARN: 검색 응답이 XML이 아닙니다(page={page}) — OC·파라미터를 확인하세요.\n")
            break
        page_items = list(root.iter(item_tag))
        if not page_items:
            break
        items.extend(page_items)
        try:
            total = int(root.findtext("totalCnt") or "0")
        except ValueError:
            total = 0
        if page * 100 >= total:
            break
        page += 1
    return items


def _mst_num(r: dict) -> int:
    """MST 정렬 키 — 자릿수가 다른 일련번호(예: 1869 vs 2100000…)가 문자열 비교로 뒤바뀌지 않게 정수화."""
    s = (r.get("MST") or "").strip()
    return int(s) if s.isdigit() else -1


def _version_key(r: dict) -> tuple[str, int]:
    return (r.get("시행일자") or "", _mst_num(r))


def _dedupe_sort(rows: list[dict]) -> list[dict]:
    """(시행일자, MST) 중복 제거 + 시행일자 내림차순. 자리표시 행(_placeholder_row)에는 '자리표시': True를 붙인다."""
    seen: set = set()
    out: list[dict] = []
    for r in rows:
        k = (r.get("시행일자"), r.get("MST"))
        if k in seen:
            continue
        seen.add(k)
        if _placeholder_row(r):
            r["자리표시"] = True
        out.append(r)
    out.sort(key=_version_key, reverse=True)
    return out


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _group_lineages(rows: list[dict]) -> dict[str, list[dict]]:
    """계통ID(법령ID·행정규칙ID·자치법규ID)별로 버전을 묶는다. ID가 없으면 '' 한 묶음."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("계통ID", ""), []).append(r)
    return groups


def _real_rows(rs: list[dict]) -> list[dict]:
    """자리표시 행을 뺀 버전 행 — 모두 자리표시면 그대로(계통표가 비지 않게)."""
    return [r for r in rs if not _placeholder_row(r)] or rs


def _lineage_table(groups: dict[str, list[dict]]) -> str:
    """계통 목록 — 최신 시행일·최신 명칭·정렬은 자리표시 행(시행일자 99991231 등)을 빼고 정한다."""
    lines = ["계통ID | 최신 시행일 | 버전 수 | 최신 명칭 (옛 명칭)"]
    for lid, rs in sorted(groups.items(), key=lambda kv: max(_version_key(r) for r in _real_rows(kv[1])),
                          reverse=True):
        latest = max(_real_rows(rs), key=_version_key)
        olds = sorted({r["명칭"] for r in rs if r.get("명칭") and r["명칭"] != latest["명칭"]})
        extra = f" (옛 명칭: {', '.join(olds)})" if olds else ""
        where = f" [{latest['지자체']}]" if latest.get("지자체") else ""
        lines.append(f"{lid or '-'} | {latest['시행일자']} | {len(rs)} | {latest['명칭']}{where}{extra}")
    return "\n".join(lines)


def _select_lineage(rows: list[dict], target: str, query: str | None, lid: str | None) -> list[dict]:
    """부분일치 검색 결과에서 한 계통(같은 규정·조례의 버전들)만 남긴다.

    검색은 부분일치라 '전자금융감독규정'에 시행세칙이, '가평군 옥외광고물'에 발전기금 조례가 섞인다.
    계통을 가리지 않고 max{시행일자 ≤ 기준일}을 고르면 다른 규정의 본문·시행기간·판례식이 나온다.
      1) --lid(계통ID) 지정 → 그 계통
      2) 계통이 하나뿐 → 그 계통
      3) 어느 버전의 명칭이 --query와 정확히 같은(공백 무시) 계통이 하나 → 그 계통(나머지는 NOTE)
      4) 그 밖에는 계통 목록을 보여주고 exit 2 — --lid로 지정하게 한다.
    """
    groups = _group_lineages(rows)
    if lid:
        picked = groups.get(lid.strip())
        if not picked:
            sys.stderr.write(f"ERROR: 계통ID {lid}의 버전이 검색 결과에 없습니다. 검색된 계통:\n"
                             + _lineage_table(groups) + "\n")
            sys.exit(2)
        return picked
    if len(groups) <= 1:
        return rows
    exact = [k for k, rs in groups.items()
             if query and any(_norm_name(r.get("명칭", "")) == _norm_name(query) for r in rs)]
    if len(exact) == 1:
        others = [k for k in groups if k != exact[0]]
        sys.stderr.write(
            f"NOTE: 명칭이 '{query}'와 정확히 일치하는 계통({exact[0]})만 사용합니다 — "
            f"부분일치로 섞인 다른 계통 {len(others)}개 제외(--lid로 바꿀 수 있음).\n")
        return groups[exact[0]]
    sys.stderr.write(
        f"ERROR: 검색어 '{query}'에 서로 다른 {'규정' if target == 'admrul' else '조례·규칙'} "
        f"{len(groups)}개 계통이 섞여 있어 하나를 고를 수 없습니다. 아래에서 골라 --lid <계통ID>로 다시 실행하세요.\n"
        + _lineage_table(groups) + "\n")
    sys.exit(2)


def _law_versions(oc: str, lid: str | None, query: str | None, nw: str) -> list[dict]:
    """eflaw에서 법령의 시점별 버전 수집. LID 한정이 정공법(부분일치 오염 0)."""
    base: dict[str, str] = {}
    if nw:
        base["nw"] = nw
    if lid:
        base["LID"] = lid
    elif query:
        base["query"] = query
    else:
        sys.stderr.write("ERROR: target=law 버전 조회에는 --lid(권장) 또는 --query가 필요합니다.\n")
        sys.exit(2)
    rows = []
    for el in _search_pages(oc, "eflaw", base):
        name = (el.findtext("법령명한글") or "").strip()
        if (not lid) and query and name != query.strip():
            continue  # 부분일치 오염 제거(예: '민법' 검색 시 난민법)
        rows.append({
            "명칭": name,
            "시행일자": (el.findtext("시행일자") or "").strip(),
            "MST": (el.findtext("법령일련번호") or "").strip(),
            "구분": (el.findtext("현행연혁코드") or "").strip(),
            "공포일자": (el.findtext("공포일자") or "").strip(),
            "공포번호": (el.findtext("공포번호") or "").strip(),
            "법령구분": (el.findtext("법령구분명") or "").strip(),
            "계통ID": (el.findtext("법령ID") or "").strip(),
            "제개정": (el.findtext("제개정구분명") or "").strip(),
        })
    return _dedupe_sort(rows)


def _ordin_versions(oc: str, query: str, org: str | None, sborg: str | None) -> list[dict]:
    """자치법규 버전 수집: 현행(nw 생략) + 연혁(nw=2) 두 번 검색해 병합.

    조례는 개정 과정에서 명칭이 바뀌는 일이 잦으므로 정확명 필터를 걸지 않는다 —
    출력의 명칭을 보고 동일 조례 여부를 판단한다.
    """
    if not query:
        sys.stderr.write("ERROR: target=ordin 버전 조회에는 --query(자치법규명)가 필요합니다.\n")
        sys.exit(2)
    rows = []
    for nw, label in ((None, "현행"), ("2", "연혁")):
        base: dict[str, str] = {"query": query}
        if nw:
            base["nw"] = nw
        if org:
            base["org"] = org
        if sborg:
            base["sborg"] = sborg
        for el in _search_pages(oc, "ordin", base):
            rows.append({
                "명칭": (el.findtext("자치법규명") or "").strip(),
                "시행일자": (el.findtext("시행일자") or "").strip(),
                "MST": (el.findtext("자치법규일련번호") or "").strip(),
                "계통ID": (el.findtext("자치법규ID") or "").strip(),
                "구분": label,
                "공포일자": (el.findtext("공포일자") or "").strip(),
                "공포번호": (el.findtext("공포번호") or "").strip(),
                "지자체": (el.findtext("지자체기관명") or "").strip(),
                "법령구분": (el.findtext("자치법규종류") or "").strip(),
                "제개정": (el.findtext("제개정구분명") or "").strip(),
            })
    return _dedupe_sort(rows)


def _admrul_versions_nw(oc: str, query: str, org: str | None = None) -> list[dict]:
    """행정규칙 버전 수집 — 목록 조회의 nw 파라미터(1 현행, 2 연혁; 공식 가이드)로 직접 검색.

    부분일치 검색이므로(예: "전자금융감독규정" → 시행세칙 포함) 명칭을 그대로 노출한다.
    구분은 응답의 <현행연혁구분> 필드를 사용한다.
    """
    rows: list[dict] = []
    for nw in (None, "2"):
        base: dict[str, str] = {"query": query}
        if nw:
            base["nw"] = nw
        if org:
            base["org"] = org
        for el in _search_pages(oc, "admrul", base, item_tag="admrul"):
            rows.append({
                "명칭": (el.findtext("행정규칙명") or "").strip(),
                "시행일자": (el.findtext("시행일자") or "").strip(),
                "MST": (el.findtext("행정규칙일련번호") or "").strip(),
                "계통ID": (el.findtext("행정규칙ID") or "").strip(),
                "구분": (el.findtext("현행연혁구분") or "").strip() or "연혁",
                "공포일자": (el.findtext("발령일자") or "").strip(),
                "공포번호": (el.findtext("발령번호") or "").strip(),
                "법령구분": (el.findtext("행정규칙종류") or "").strip(),
                "소관부처": (el.findtext("소관부처명") or "").strip(),
                "제개정": (el.findtext("제개정구분명") or "").strip(),
            })
    return _dedupe_sort(rows)


def _admrul_bi_row(bi) -> dict:
    return {
        "명칭": (bi.findtext("행정규칙명") or "").strip(),
        "시행일자": (bi.findtext("시행일자") or "").strip(),
        "MST": (bi.findtext("행정규칙일련번호") or "").strip(),
        "계통ID": (bi.findtext("행정규칙ID") or "").strip(),
        "법령구분": (bi.findtext("행정규칙종류") or "").strip(),
        "소관부처": (bi.findtext("소관부처명") or "").strip(),
        "구분": "현행" if (bi.findtext("현행여부") or "").strip() == "Y" else "연혁",
        "공포일자": (bi.findtext("발령일자") or "").strip(),
        "공포번호": (bi.findtext("발령번호") or "").strip(),
    }


def _admrul_versions(oc: str, start_id: str, date: str | None = None, max_steps: int = 15) -> list[dict]:
    """행정규칙 버전 체인 역추적 (실측 발견 경로).

    admrulOldAndNew 응답의 <구조문_기본정보>가 직전 버전의 행정규칙일련번호를 주고,
    그 일련번호로 admrul 본문·admrulOldAndNew 재호출이 모두 가능하다 → 한 단계씩
    과거로 체인 추적. 한 단계 = API 1회이므로 max_steps로 상한을 둔다.
    date가 주어지면 시행일자 ≤ date 버전에 도달한 시점에 멈춘다.
    """
    rows: list[dict] = []
    cur = (start_id or "").strip()
    steps = 0
    # 신구법비교의 구·신조문 기본정보에는 소관부처명·행정규칙종류가 없다 — 판례식 표기용으로 본문에서 한 번 받는다.
    info_body = http_get(build_url(BASE_SERVICE, {"OC": oc, "target": "admrul", "type": "XML", "ID": cur}),
                         no_cache=_NO_CACHE) if cur else ""
    org = _first_tag_value(info_body, "소관부처명")
    kind = _first_tag_value(info_body, "행정규칙종류")
    while cur and steps < max_steps:
        body = http_get(build_url(BASE_SERVICE, {
            "OC": oc, "target": "admrulOldAndNew", "type": "XML", "ID": cur}),
            no_cache=_NO_CACHE)
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            break
        if steps == 0:
            new_bi = root.find("신조문_기본정보")
            if new_bi is not None and (new_bi.findtext("행정규칙일련번호") or "").strip():
                rows.append(_admrul_bi_row(new_bi))
        old_bi = root.find("구조문_기본정보")
        if old_bi is None:
            break
        row = _admrul_bi_row(old_bi)
        prev = row["MST"]
        if not prev or any(r["MST"] == prev for r in rows):
            break
        rows.append(row)
        if date and row["시행일자"] and row["시행일자"] <= date:
            break  # 기준일 도달 — 더 거슬러 갈 필요 없음
        cur = prev
        steps += 1
    if steps >= max_steps:
        sys.stderr.write(
            f"NOTE: 체인 추적이 상한({max_steps}단계)에 도달했습니다. 더 과거가 필요하면 --max-steps를 늘리세요.\n")
    for r in rows:
        r["소관부처"] = r.get("소관부처") or org
        r["법령구분"] = r.get("법령구분") or kind
    return _dedupe_sort(rows)


def _law_nw(args: argparse.Namespace, date: str | None) -> str:
    """eflaw 버전 검색의 nw — 기본 1,3(연혁+현행). 기준일이 오늘 뒤면 시행예정(2)도 넣는다(그날 시행될 판본을 고르도록)."""
    if args.nw:
        return args.nw
    return "1,2,3" if date and date > _today() else "1,3"


def _collect_versions(args: argparse.Namespace, oc: str, date: str | None = None) -> list[dict]:
    if args.target in ("ordin", "admrul") and args.lid and not (args.query or args.id):
        sys.stderr.write(
            f"ERROR: target={args.target}의 --lid(계통ID)는 --query 검색 결과를 한정하는 용도입니다 — "
            "--query(규정·조례명)도 함께 주세요.\n")
        sys.exit(2)
    if args.target == "ordin":
        return _ordin_versions(oc, args.query, args.org, args.sborg)
    if args.target == "admrul":
        if args.query:
            return _admrul_versions_nw(oc, args.query, org=args.org)   # 기본: nw=2 연혁 직접 검색
        if args.id:
            return _admrul_versions(oc, args.id, date=date, max_steps=args.max_steps or 15)  # 보조: 체인 역추적
        sys.stderr.write(
            "ERROR: target=admrul 버전 조회에는 --query(연혁 직접 검색, 권장) 또는 --id(체인 역추적, 보조)가 필요합니다.\n")
        sys.exit(2)
    return _law_versions(oc, args.lid, args.query, _law_nw(args, date))


def _versions_search_urls(args: argparse.Namespace, oc: str) -> list[str]:
    """버전 수집(_collect_versions)이 보낼 첫 요청 URL들(각 1쪽) — dry-run용, 호출하지 않는다.

    law: eflaw 검색(nw 기본 1,3) / ordin·admrul --query: 현행(nw 생략)과 연혁(nw=2) 두 번 /
    admrul --id: admrulOldAndNew 체인의 첫 단계. 인자 검증은 _collect_versions와 같다.
    """
    if args.target in ("ordin", "admrul") and args.lid and not (args.query or args.id):
        _collect_versions(args, oc)                     # 같은 안내로 exit 2(호출 전에 끝난다)
    page = {"OC": oc, "type": "XML", "display": "100", "page": "1"}
    if args.target == "law":
        if not (args.lid or args.query):
            sys.stderr.write("ERROR: target=law 버전 조회에는 --lid(권장) 또는 --query가 필요합니다.\n")
            sys.exit(2)
        date = _parse_date(args.date) if getattr(args, "date", None) else None
        base = {**page, "target": "eflaw", "nw": _law_nw(args, date)}
        base.update({"LID": args.lid} if args.lid else {"query": args.query})
        return [build_url(BASE_SEARCH, base)]
    if args.target == "admrul" and not args.query:
        if not args.id:
            _collect_versions(args, oc)                 # --query·--id 모두 없음 → 같은 안내로 exit 2
        return [build_url(BASE_SERVICE, {"OC": oc, "target": "admrulOldAndNew", "type": "XML", "ID": args.id})]
    if not args.query:
        sys.stderr.write(f"ERROR: target={args.target} 버전 조회에는 --query가 필요합니다.\n")
        sys.exit(2)
    base = {**page, "target": args.target, "query": args.query, "org": args.org or "",
            "sborg": (args.sborg or "") if args.target == "ordin" else ""}
    return [build_url(BASE_SEARCH, base), build_url(BASE_SEARCH, {**base, "nw": "2"})]


def cmd_versions(args: argparse.Namespace) -> None:
    oc = resolve_oc(args.oc)
    if args.dry_run:
        for u in _versions_search_urls(args, oc):
            print(_display_url(u))
        return
    rows = _collect_versions(args, oc)
    if args.target == "admrul" and rows and not args.query:
        sys.stderr.write("NOTE: 신구법비교 체인 역추적 결과입니다(한 단계 = API 1회). 통상은 --query(연혁 직접 검색)를 권장.\n")
    groups = _group_lineages(rows)
    if args.target in ("ordin", "admrul") and args.lid:
        rows = groups.get(args.lid.strip(), [])
    elif len(groups) > 1:
        sys.stderr.write(
            f"NOTE: 서로 다른 계통 {len(groups)}개가 섞여 있습니다(부분일치 검색). get-asof는 명칭이 검색어와 정확히 같은 "
            "계통을 고르거나, 없으면 --lid <계통ID>를 요구합니다.\n" + _lineage_table(groups) + "\n")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(f"# {args.target} 버전 {len(rows)}건 — 시행일자 내림차순, (시행일자, MST) 중복 제거 "
          "(공포번호는 앞자리 0 없이 표기 — 원값은 --json)")
    print("시행일자 | MST | 계통ID | 구분 | 공포일자 | 공포번호 | 명칭")
    for r in rows:
        extra = f" ({r['지자체']})" if r.get("지자체") else ""
        no = _prom_no(r, args.target) or (f"없음(원값 {r['공포번호']})" if r.get("공포번호") else "")
        kind = (r["구분"] + (f" ({r['제개정']})" if _repealed(r) else "")
                + (" (자리표시 — 선택 제외)" if _placeholder_row(r) else ""))
        print(f"{r['시행일자']} | {r['MST']} | {r.get('계통ID', '')} | {kind} | {r['공포일자']} | "
              f"{no} | {r['명칭']}{extra}")
    if not rows:
        sys.stderr.write("NOTE: 0건 — target=law면 --lid 사용을, 명칭 변경(조례)·법령명 표기를 확인하세요.\n")


def _parse_date(raw: str | None) -> str:
    """기준일 → YYYYMMDD. '20190601', '2019-06-01', '2019. 6. 1.'을 받는다(월·일 0 채움).

    자릿수를 채우지 않고 숫자만 이어붙이면 '2020. 6. 1.' → '202061'이 되어, 문자열 비교로 버전을 고를 때
    엉뚱한(기준일보다 뒤의) 시행본이 선택된다.
    """
    groups = re.findall(r"\d+", raw or "")
    if len(groups) == 1 and len(groups[0]) == 8:
        date = groups[0]
    elif len(groups) == 3 and len(groups[0]) == 4 and all(1 <= len(g) <= 2 for g in groups[1:]):
        date = f"{groups[0]}{int(groups[1]):02d}{int(groups[2]):02d}"
    else:
        date = ""
    try:
        datetime.strptime(date, "%Y%m%d")
    except ValueError:
        sys.stderr.write(f"ERROR: --date는 실제 날짜여야 합니다(예: 20190601, 2019-06-01, '2019. 6. 1.'): {raw!r}\n")
        sys.exit(2)
    return date


def _promulgation_label(r: dict, target: str) -> str:
    """판례식 괄호의 법규 종류 표기 — law.go.kr 표시 형식을 따른다.

    법령: '법률'·'대통령령' 등(법령구분명) / 행정규칙: '금융위원회고시'(소관부처명+종류) /
    자치법규: '경기도가평군조례'(지자체기관명 공백 제거+종류).
    """
    kind = (r.get("법령구분") or "").strip()
    if target == "admrul":
        return f"{(r.get('소관부처') or '').replace(' ', '')}{kind or '고시'}"
    if target == "ordin":
        return f"{(r.get('지자체') or '').replace(' ', '')}{kind or '조례'}"
    return kind or "법률"


def _norm_prom_no(value: str | None, target: str) -> str:
    """공포(발령)번호 표기 정규화 — 서면·판례는 앞자리 0 없이 적는다(API 원값 '06627' → '6627').

    ''·'none'·'null' → ''. 행정규칙 발령번호 '9999'는 자리표시값이라 ''(전자금융감독규정시행세칙 22개 버전 전부
    9999, 2026-09-27 실측). 법령·자치법규의 9999는 실제 번호일 수 있어 유지한다(법률 제9999호 실재).
    숫자가 아닌 번호('2022-44')는 그대로 둔다.
    """
    v = (value or "").strip()
    if v.lower() in ("", "none", "null"):
        return ""
    if target == "admrul" and v == "9999":
        return ""
    return str(int(v)) if v.isdigit() else v


def _prom_no(r: dict, target: str) -> str:
    """버전 행의 공포(발령)번호 — 정규화 값(_norm_prom_no)."""
    return _norm_prom_no(r.get("공포번호"), target)


def _real_date(s: str | None) -> bool:
    """8자리 실제 날짜인가 — 자리표시값('00000000'·'99991231')과 빈 값은 False."""
    s = (s or "").strip()
    if not (len(s) == 8 and s.isdigit()) or s in ("00000000", "99991231"):
        return False
    try:
        datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return False
    return True


def _placeholder_row(r: dict) -> bool:
    """연혁 검색의 자리표시 행(시행일자 99991231·공포일자 00000000 등)인가."""
    return not (_real_date(r.get("시행일자")) and _real_date(r.get("공포일자")))


def _staged_kind(pick: dict, nxt: dict, target: str, rows: list[dict] | None = None) -> str:
    """직후 버전(nxt)을 시행 기준('개정되어 … 시행되기 전의 것')으로 적어야 하는 경우의 종류.

    A 같은 개정의 나머지 시행분 — (공포일자·공포번호)가 선택본과 같다.
    B 선택본 전에 일부 시행된 개정의 나머지 시행분 — rows에 시행일자 ≤ 선택본 시행일자이면서 (공포일자·공포번호)가
      nxt와 같은 행이 있다(주택임대차보호법 제19356호: 20230418·20230719 두 행, 사이에 제19520호).
    C 선택본보다 먼저 공포되고 뒤에 시행된 개정 — nxt 공포일자 < 선택본 공포일자, 또는 공포일자·법령구분이 같고
      두 번호가 모두 숫자이며 nxt 번호가 작다(도로교통법 제20864호 공포 20250401 시행 20260402 vs 제21016호).
    그 밖('')은 nxt가 선택본보다 뒤에 공포된 일반 개정 — '개정되기 전의 것'.
    동일 개정 비교는 원값끼리, 번호 대소 비교는 정규화 값끼리 한다. 자리표시 행·날짜는 판정에서 뺀다.
    """
    key = lambda r: ((r.get("공포일자") or "").strip(), (r.get("공포번호") or "").strip())
    if key(nxt) == key(pick):
        return "A"
    if rows and _real_date(nxt.get("공포일자")):
        p_eff = (pick.get("시행일자") or "").strip()
        if any(not _placeholder_row(r) and (r.get("시행일자") or "") <= p_eff and key(r) == key(nxt) for r in rows):
            return "B"
    n_d, p_d = (nxt.get("공포일자") or "").strip(), (pick.get("공포일자") or "").strip()
    if _real_date(n_d) and _real_date(p_d):
        if n_d < p_d:
            return "C"
        n_no, p_no = _prom_no(nxt, target), _prom_no(pick, target)
        if (n_d == p_d and (nxt.get("법령구분") or "") == (pick.get("법령구분") or "")
                and n_no.isdigit() and p_no.isdigit() and int(n_no) < int(p_no)):
            return "C"
    return ""


def _amend_by(nxt: dict, target: str) -> tuple[str, str]:
    """(공포 정보, '…로 개정') — 판례식 괄호의 개정 부분. 번호가 없으면(행정규칙 9999 등) 날짜만."""
    date = _dot_date(nxt["공포일자"])
    no = _prom_no(nxt, target)
    if no:
        promulgation = f"{date} {_promulgation_label(nxt, target)} 제{no}호"
        return promulgation, f"{promulgation}로 개정"
    return date, f"{date} 개정"


def _precedent_phrase(pick: dict, nxt: dict, target: str,
                      rows: list[dict] | None = None) -> tuple[str, str, str]:
    """(헤더 한 줄, 판례식, 종류) — 종류 A·B·C(_staged_kind)면 '…로 개정되어 ○. ○. ○. 시행되기 전의 것'.

    rows(선택 계통의 버전 행)를 주지 않으면 A만 판정한다. 공포번호는 _prom_no로 정규화하고, 행정규칙 자리표시
    번호(9999)면 종류·번호 없이 '(2024. 9. 13. 개정되기 전의 것)'으로 적는다.
    """
    promulgation, by = _amend_by(nxt, target)
    if not _prom_no(nxt, target) and target == "admrul" and (nxt.get("공포번호") or "").strip() == "9999":
        sys.stderr.write("[get-asof] NOTE: 발령번호가 자리표시값(9999)이라 판례식에서 종류·번호를 생략했습니다 — "
                         "발령기관·번호는 원문으로 확인하세요.\n")
    eff = f"(시행 {nxt['시행일자']}" + (f", MST {nxt['MST']})" if nxt.get("MST") else ")")
    if _repealed(nxt):              # 판례 표기 '…로 폐지되기 전의 것'(대법원 2018도1966 — 공공기관개인정보보호법)
        return f"{nxt['제개정']} {promulgation} {eff}", f"구 {pick['명칭']}({promulgation}로 폐지되기 전의 것)", ""
    kind = _staged_kind(pick, nxt, target, rows)
    if kind:
        phrase = f"구 {pick['명칭']}({by}되어 {_dot_date(nxt['시행일자'])} 시행되기 전의 것)"
        head = {"A": f"같은 개정({promulgation})의 나머지 부분 시행 {eff}",
                "B": f"선택본 전에 일부 시행된 개정({promulgation})의 나머지 부분 시행 {eff}",
                "C": f"선택본보다 먼저 공포되고 뒤에 시행된 개정({promulgation}) {eff}"}[kind]
        return head, phrase, kind
    return f"공포 {promulgation} {eff}", f"구 {pick['명칭']}({by}되기 전의 것)", ""


def _repealed(r: dict) -> bool:
    """폐지 판본 행인가 — 연혁 검색의 제개정구분명이 '폐지'·'타법폐지'(조문이 비어 있다)."""
    return (r.get("제개정") or "").endswith("폐지")


def _placeholder_before(rows: list[dict], date: str) -> list[dict]:
    """공포일자는 실제 날짜이고 기준일 이전인 자리표시 행(시행일만 99991231 등) — 공포일자 오름차순.

    선택본이 없을 때 '제정 전'과 '시행일 미상 판본'을 가른다(경기도옥외광고물등관리조례: 1991. 2. 7. 제2104호부터
    공포 6건, 모두 시행일자 99991231 — 2026-09-27 실측).
    """
    return sorted((r for r in rows if _placeholder_row(r) and _real_date(r.get("공포일자"))
                   and r["공포일자"] <= date), key=lambda r: (r["공포일자"], r.get("MST", "")))


def _pick_versions(rows: list[dict], date: str) -> tuple[dict | None, dict | None, int]:
    """(선택본, 직후 버전, 제외한 자리표시 행 수) — 선택본은 max{시행일자 ≤ 기준일}, 직후 버전은 그 뒤 첫 버전.

    연혁 검색의 자리표시 행(시행일자 99991231·공포일자 00000000 등 — 서울특별시 강남구 옥외광고물 조례 MST 915155,
    경기도옥외광고물등관리조례 공포일자 19910207·시행일자 99991231)은 선택·직후 판정에서 뺀다. 선택본이 없으면 None.
    """
    real = [r for r in rows if not _placeholder_row(r)]
    eligible = [r for r in real if r["시행일자"] <= date]
    pick = max(eligible, key=_version_key) if eligible else None
    newer = [r for r in real if pick and r["시행일자"] > pick["시행일자"]]
    nxt = min(newer, key=_version_key) if newer else None
    return pick, nxt, len(rows) - len(real)


# 조문 대조용 — 개정 표지(<개정 1995.12.29>·[본조신설 2018.3.30] 등 연도가 든 괄호)와 공백을 빼고 비교한다.
_ANNOT_RE = re.compile(r"<[^<>]*\d{4}[^<>]*>|\[[^\[\]]*\d{4}[^\[\]]*\]")


def _article_sig(body: str) -> str | None:
    """JO 응답(법령 XML)의 조문 문언 서명 — 조문 단위만(편·장 제목 제외), 개정 표지·공백 제거. 조문이 없으면 None."""
    root = _xml_root(body)
    if root is None:
        return None
    parts: list[str] = []
    for u in root.iter("조문단위"):
        if u.findtext("조문여부") != "조문":
            continue
        parts.append(u.findtext("조문내용") or "")
        parts += [e.text or "" for e in u.iter() if e.tag in ("항내용", "호내용", "목내용")]
    if not parts:
        return None
    return re.sub(r"\s+", "", _ANNOT_RE.sub("", "\n".join(parts)))


def _jo_label(jo: str) -> str:
    n, g = _jo_pattern(jo)
    return f"제{n}조" + (f"의{g}" if g else "")


def _eflaw_article_sig(params: dict[str, str], jo: str, no_cache: bool) -> str | None:
    body, _ = _fetch_with_jo("eflaw", params, jo, "XML", no_cache, exit_on_missing=False)
    return None if _jo_result_empty(body) else _article_sig(body)


def _article_change(oc: str, pick: dict, rows: list[dict], jo: str, sig0: str, max_steps: int,
                    no_cache: bool) -> tuple[dict | None, dict | None, int, int]:
    """선택본 뒤 버전을 시행일 오름차순으로 걸으며 조문 문언이 처음 달라진 버전을 찾는다(순차 — 바뀌었다 되돌아온 경우 대비).

    반환 (바꾼 버전, 그 직전 버전, 조회 수, 남은 버전 수). 같은 시행일에 버전이 여럿이면(도로교통법 20180425 두 행)
    MST가 가장 큰 행(그날의 시행 문언)이 달라졌을 때만 바뀐 것으로 보고, 그날 행 중 MST 오름차순으로 처음 달라진 행을 고른다.
    한 번 조회 = API 1회(캐시 적용), 상한 max_steps.
    """
    code = encode_jo(jo)
    later = sorted((r for r in rows if not _placeholder_row(r) and _version_key(r) > _version_key(pick)),
                   key=_version_key)
    days: dict[str, list[dict]] = {}
    for r in later:
        days.setdefault(r["시행일자"], []).append(r)
    prev, calls = pick, 0
    for i, day in enumerate(sorted(days)):
        group = days[day]
        sigs: dict[str, str | None] = {}
        for r in reversed(group):                      # 그날의 시행 문언(MST 최대)부터
            if calls >= max_steps:
                return None, None, calls, len(days) - i
            calls += 1
            sigs[r["MST"]] = _eflaw_article_sig({"OC": oc, "target": "eflaw", "type": "XML", "MST": r["MST"],
                                                 "efYd": r["시행일자"], "JO": code}, jo, no_cache)
            if r is group[-1] and sigs[r["MST"]] == sig0:
                break
        if sigs[group[-1]["MST"]] != sig0:
            changer = next(r for r in group if r["MST"] in sigs and sigs[r["MST"]] != sig0)
            return changer, prev, calls, 0
        prev = group[-1]
    return None, None, calls, 0


def _addenda_units(body: str, nums: list[str] | None = None) -> list[str]:
    """본문 XML의 <부칙단위>에서 부칙공포번호가 nums에 드는 단위를 평문으로(nums가 비면 전부).

    번호는 정수로 비교한다('08730' = '8730'). <부칙내용>은 줄마다 CDATA로 나뉘어 있어 모두 이어 붙이고 빈 줄은 뺀다.
    JO를 지정한 응답에는 부칙이 없다(형사소송법 MST 74887 JO=024900 실측) — 전문 응답에서 뽑는다.
    """
    want = {_norm_prom_no(n, "law") for n in (nums or [])}
    return [text for no, text in _addenda_pairs(body) if not want or no in want]


def _addenda_pairs(body: str) -> list[tuple[str, str]]:
    """본문 XML의 부칙 전부 → [(정규화 공포번호, 평문)]."""
    root = _xml_root(body)
    if root is None:
        return []
    out: list[tuple[str, str]] = []
    for u in root.iter("부칙단위"):
        el = u.find("부칙내용")
        lines = [ln.strip() for ln in "".join(el.itertext()).splitlines()] if el is not None else []
        text = "\n".join(ln for ln in lines if ln)
        if text:
            out.append((_norm_prom_no(u.findtext("부칙공포번호"), "law"), text))
    return out


def _addenda_nums(raw: list[str] | None) -> list[str]:
    """--addenda 값 → 공포번호 목록('8730 13454'·'8730,13454'·'제8730호' 모두 받는다)."""
    return [n for v in (raw or []) for n in re.findall(r"\d+", v)]


def _addenda_check(args: argparse.Namespace, targets: tuple[str, ...]) -> None:
    """--addenda 사용 조건 — target·--jo·--byl·type 검사(본문을 받기 전에)."""
    if getattr(args, "addenda", None) is None:
        return
    if args.target not in targets:
        sys.stderr.write(f"ERROR: --addenda는 target {'·'.join(targets)}에서만 씁니다 — 행정규칙·자치법규 부칙은 "
                         "--text 없이 받은 본문에서 확인하세요.\n")
        sys.exit(2)
    if args.jo or getattr(args, "byl", None):
        sys.stderr.write("ERROR: --addenda는 --jo·--byl과 함께 쓸 수 없습니다 — 조문을 지정한 응답에는 부칙이 없습니다.\n")
        sys.exit(2)
    if args.type.upper() != "XML":
        sys.stderr.write("ERROR: --addenda는 type=XML에서만 동작합니다.\n")
        sys.exit(2)


def _emit_addenda(body: str, url: str, args: argparse.Namespace, nums: list[str]) -> None:
    """--addenda 출력 — 화면에는 해당 부칙만, --save-to 파일에는 원시 응답. 없는 번호면 그 본문의 부칙 목록과 exit 2."""
    body = _mask_oc_in_body(body, url)
    if args.save_to:
        with open(args.save_to, "w", encoding="utf-8") as f:
            f.write(body)
    units = _addenda_units(body, nums)
    if not units:
        heads = [t.splitlines()[0] for _, t in _addenda_pairs(body)]
        sys.stderr.write(f"ERROR: 이 본문에 공포번호 {', '.join(nums) or '(전부)'}의 부칙이 없습니다 — 부칙 {len(heads)}건"
                         + (f"(최근 10건: {' / '.join(heads[-10:])})" if heads else "") + ".\n")
        sys.exit(2)
    have = {no for no, _ in _addenda_pairs(body)}
    lost = [n for n in dict.fromkeys(_norm_prom_no(n, "law") for n in nums) if n not in have]
    if lost:
        sys.stderr.write(f"NOTE: 공포번호 {', '.join(lost)}의 부칙은 이 본문에 없습니다 — 다른 법률에 의한 개정이면 그 법률의 "
                         "부칙을 확인하세요.\n")
    print("\n\n".join(units))


def cmd_get_asof(args: argparse.Namespace) -> None:
    oc = resolve_oc(args.oc)
    date = _parse_date(args.date)
    if args.jo:
        _check_jo(args.jo, args.target)
    _byl_check(args, ("law", "admrul"))
    _addenda_check(args, ("law",))
    if args.dry_run:
        # 본문 URL(버전 MST)은 버전 검색 결과로 정해진다 — 호출하지 않으므로 검색 URL만 보인다.
        for u in _versions_search_urls(args, oc):
            print(_display_url(u))
        sys.stderr.write("NOTE: 본문 URL은 버전 선택 뒤 정해지므로 dry-run은 버전 검색 URL만 보입니다.\n")
        return

    rows = _collect_versions(args, oc, date=date)
    if not rows:
        sys.stderr.write("ERROR: 버전을 찾지 못했습니다 — target=law면 --lid 사용, 명칭·표기를 확인하세요.\n")
        sys.exit(2)
    lid_filter = args.lid if args.target in ("ordin", "admrul") else None
    rows = _select_lineage(rows, args.target, args.query, lid_filter)
    names = {r["명칭"] for r in rows if r.get("명칭") and not _placeholder_row(r)}
    if len(names) > 1:
        sys.stderr.write(
            f"[get-asof] NOTE: 이 계통은 개정으로 명칭이 바뀌었습니다({len(names)}종) — 인용에는 선택본의 명칭을 씁니다.\n")

    pick, nxt, excluded = _pick_versions(rows, date)
    if excluded:
        sys.stderr.write(f"[get-asof] NOTE: 시행일·공포일이 자리표시값인 연혁 행 {excluded}건은 선택·직후 개정 판정에서 "
                         "뺐습니다(versions 표의 '자리표시' 행).\n")
    if pick is None:
        real = [r["시행일자"] for r in rows if not _placeholder_row(r)]
        early = _placeholder_before(rows, date)
        if early:
            first, last = early[0], early[-1]
            no = lambda r: f" 제{_prom_no(r, args.target)}호" if _prom_no(r, args.target) else ""
            sys.stderr.write(
                f"ERROR: 기준일 {date} 이전 시행일이 확인되는 버전이 없습니다(시행일이 확인되는 최초 버전: "
                f"{min(real) if real else '미상'}).\n"
                f"  그 시기 판본은 시행일이 자리표시값이라 특정할 수 없습니다 — 기준일 전 공포 {len(early)}건"
                f"(최초 {_dot_date(first['공포일자'])}{no(first)}, 마지막 {_dot_date(last['공포일자'])}{no(last)} "
                f"MST {last['MST']}). 원문·자치법규 연혁으로 시행일을 확인하세요(versions 표의 '자리표시' 행).\n"
            )
        else:
            sys.stderr.write(
                f"ERROR: 기준일 {date} 이전에 시행 중이던 버전이 없습니다(최초 시행일: {min(real) if real else '미상'}).\n"
                "  제정 전 시점입니다 — 기준일 또는 법령 특정을 재확인하세요.\n"
            )
        sys.exit(2)
    real_rows = [r for r in rows if not _placeholder_row(r)]

    # 선택 결과 헤더 — stderr (stdout은 응답 본문만 유지)
    lineage = f" | 계통ID {pick['계통ID']}" if pick.get("계통ID") else ""
    sys.stderr.write(
        f"[get-asof] 기준일 {date} → 선택본: {pick['명칭']} | 시행 {pick['시행일자']} | "
        f"MST {pick['MST']}{lineage} | {pick['구분']} | 공포 {pick['공포일자']}"
        + (f" 제{_prom_no(pick, args.target)}호" if _prom_no(pick, args.target) else "") + "\n"
    )

    # 본문 요청 파라미터
    if args.target == "ordin":
        params: dict[str, str] = {"OC": oc, "target": "ordin", "type": args.type, "MST": pick["MST"]}
    elif args.target == "admrul":
        params = {"OC": oc, "target": "admrul", "type": args.type, "ID": pick["MST"]}
    else:
        params = {"OC": oc, "target": "eflaw", "type": args.type,
                  "MST": pick["MST"], "efYd": pick["시행일자"]}
    if args.jo and args.target == "law":
        params["JO"] = encode_jo(args.jo)
    missing_hint = (f"선택본은 폐지 판본이라 조문이 없습니다 — 폐지 전 판본은 --date {_prev_day(pick['시행일자'])}."
                    if _repealed(pick) else
                    "기준일 시행본에 이 조문이 없습니다 — 기준일 뒤에 신설됐거나 조문번호가 바뀌었을 수 있습니다. "
                    "신설 조문도 그 개정의 부칙(적용례·경과조치)에 따라 기준일 전의 행위·사건에 적용될 수 있습니다 — "
                    "조문을 신설한 개정의 부칙을 --addenda로 확인하세요(예: 형사소송법 제253조의2 ← 법률 제13454호 부칙 제2조"
                    + (f"; 기준일 뒤 개정 부칙 전부: get-asof --lid {pick['계통ID']} --date {date} --addenda"
                       if args.target == "law" and pick.get("계통ID") else "") + ").")

    # 조문 단위 대조(target=law·--jo·XML) — 선택본 조문을 먼저 받아 현행 조문과 비교한다.
    body = url = None
    missing = False
    same: bool | None = None           # None = 대조하지 않음
    jo_label = _jo_label(args.jo) if args.jo and args.target == "law" else ""
    compare = args.target == "law" and args.jo and args.type.upper() == "XML" and pick["구분"] != "시행예정"
    # 폐지된 계통(현행 행 없음)은 현행 조회가 '일치하는 법령이 없습니다'로 끝난다 — 대조하지 않는다.
    no_current = bool(compare) and pick["구분"] != "현행" and not any(r.get("구분") == "현행" for r in rows)
    if compare:
        body, url = _fetch_with_jo("eflaw", params, args.jo, args.type, args.no_cache, exit_on_missing=False)
        missing = _jo_result_empty(body)
        if not missing and pick["구분"] != "현행" and pick.get("계통ID") and not no_current:
            sig0 = _article_sig(body)
            cur = _eflaw_article_sig({"OC": oc, "target": "eflaw", "type": "XML", "ID": pick["계통ID"],
                                      "JO": params["JO"]}, args.jo, args.no_cache)
            same = sig0 is not None and cur == sig0

    if _repealed(pick):
        sys.stderr.write(f"[get-asof] ⚠ 폐지본 — 기준일에 이 법규는 폐지 상태입니다({_dot_date(pick['시행일자'])} "
                         f"{pick['제개정']}). 조문이 비어 있습니다 — 폐지 전 판본은 --date를 {_prev_day(pick['시행일자'])}로, "
                         "대체 법규는 versions의 다른 계통으로 확인하세요.\n")
    elif pick["구분"] == "시행예정":
        sys.stderr.write(f"[get-asof] ⚠ 시행예정본 — 기준일 {date}이 오늘 뒤라 {_dot_date(pick['시행일자'])} 시행 예정인 "
                         f"본문을 골랐습니다(공포 {_dot_date(pick['공포일자'])}). 아직 시행 전이므로 인용할 때는 시행일을 밝히고, "
                         "그 사이 다른 개정이 공포되면 달라질 수 있습니다.\n")
    elif pick["구분"] != "현행":
        end = _prev_day(nxt["시행일자"]) if nxt else ""
        period = f"{pick['시행일자']} ~ {end}" if end else f"{pick['시행일자']} ~"
        if same:
            sys.stderr.write(f"[get-asof] 연혁본이지만 {jo_label} 문언은 현행과 같습니다 — 통상 표기 가능(서면 스킬 문체 "
                             f"가이드 기준). 시행기간: {period}.\n")
        elif same is False:
            sys.stderr.write(f"[get-asof] ⚠ 연혁본 — {jo_label} 문언이 현행과 다릅니다. 시행기간: {period}. "
                             "인용 시 구법 표기 필수.\n")
        else:
            sys.stderr.write(f"[get-asof] ⚠ 연혁본 — 현행 본문이 아닙니다. 시행기간: {period}. "
                             "인용 조문의 문언이 현행과 다르면 구법 표기"
                             + ("" if args.jo else "(조문 대조는 --jo)") + ".\n")
        if no_current:
            sys.stderr.write("[get-asof] NOTE: 이 계통에는 현행본이 없어(폐지된 법령) 조문 대조를 생략했습니다 — 인용은 "
                             "구법 표기.\n")
    else:
        sys.stderr.write("[get-asof] 기준일 현재 시행본이 현행과 동일합니다.\n")
    changer = None
    if nxt:
        head, phrase, kind = _precedent_phrase(pick, nxt, args.target, real_rows)
        if kind:
            # 단계적 시행·공포 순서 역전 — '그 개정 전'이라고 쓰면 선택본 자신이나 그보다 앞 버전을 가리키게 된다.
            sys.stderr.write(f"[get-asof] 직후 변동: {head} → 판례식: {phrase} — 공포와 시행 시점이 어긋나므로 "
                             "부칙의 시행일 규정을 확인하세요.\n")
        else:
            sys.stderr.write(f"[get-asof] 직후 개정: {head} → 판례식: {phrase}\n")
        if same is False:
            steps = args.max_steps or 10
            changer, base, calls, left = _article_change(oc, pick, real_rows, args.jo, _article_sig(body) or "",
                                                         steps, args.no_cache)
            if changer:
                _, by = _amend_by(changer, "law")
                eff = f"시행 {changer['시행일자']}, MST {changer['MST']}"
                sys.stderr.write(f"[get-asof] 조문 기준 판례식: 구 {pick['명칭']}({by}되기 전의 것) — "
                                 f"{jo_label}를 바꾼 개정({eff})"
                                 + (" — 법령 기준과 같음" if changer is nxt else "") + "\n")
                kind = _staged_kind({**base, "명칭": pick["명칭"]}, changer, "law", real_rows)
                if kind:
                    sys.stderr.write(f"[get-asof]   공포·시행 시점이 어긋남 — 혼동 우려 시 시행형: 구 {pick['명칭']}"
                                     f"({by}되어 {_dot_date(changer['시행일자'])} 시행되기 전의 것)\n")
            else:
                sys.stderr.write(f"[get-asof] 조문 기준 판례식: 뒤 버전 {calls}건을 대조했으나 {jo_label}를 바꾼 개정을 "
                                 f"찾지 못했습니다(상한 --max-steps {steps}"
                                 + (f", 남은 시행일 {left}개" if left else "") + ") — --max-steps를 늘리세요.\n")
        if same is not True and pick["구분"] != "현행":
            ref = changer or nxt
            if args.target == "law":
                no = _prom_no(ref, "law")
                cmd = (f"get --target eflaw --mst {ref['MST']} --efyd {ref['시행일자']}"
                       + (f" --addenda {no}" if no else " --addenda"))
                which = f"{jo_label}를 바꾼 개정" if changer else "직후 개정"
                tail = f" (기준일 뒤 개정 전부: 이 get-asof에서 --jo 대신 --addenda)"
            else:
                cmd = (f"get --target ordin --mst {ref['MST']}" if args.target == "ordin"
                       else f"get --target admrul --id {ref['MST']}")
                which, tail = "직후 버전", " — --text 없이 받아 부칙 확인"
            sys.stderr.write("[get-asof] 경과조치 확인: 기준일 뒤 개정의 부칙(적용례·경과조치)이 이 본문의 적용 여부를 "
                             f"정할 수 있습니다 — {which} 부칙: {cmd}{tail}\n")

    # 기준일 뒤 개정의 부칙(--addenda) — 현행 본문에 쌓인 부칙에서 뽑는다.
    if args.addenda is not None:
        nums = _addenda_nums(args.addenda) or sorted({_prom_no(r, "law") for r in real_rows
                                                      if r["시행일자"] > pick["시행일자"] and _prom_no(r, "law")},
                                                     key=int)
        aparams = {"OC": oc, "target": "eflaw", "type": "XML", "ID": pick.get("계통ID", "")}
        if not nums:
            sys.stderr.write("[get-asof] NOTE: 기준일 뒤 개정이 없습니다 — 볼 부칙이 없습니다.\n")
            return
        sys.stderr.write(f"[get-asof] 부칙 {len(nums)}건 대상(공포번호 {', '.join(nums)}) — 현행 본문의 부칙에서 뽑습니다.\n")
        abody, aurl = _fetch_with_jo("eflaw", aparams, None, "XML", args.no_cache)
        _emit_addenda(abody, aurl, args, nums)
        return

    # 본문 조회
    if missing:
        _jo_missing_exit(args.jo, params.get("JO"), missing_hint)
    if body is None:
        body, url = _fetch_with_jo(params["target"], params, args.jo, args.type, args.no_cache,
                                   missing_hint=missing_hint)
    raw = None
    if args.jo and args.target in LOCAL_JO_TARGETS:
        raw, body = body, _extract_articles(body, args.target, args.jo, args.type)   # --save-to는 발췌 전 전문
    if args.byl:
        _emit_byl(body, url, args)
        return
    _emit(body, url, args, _body_text if args.text else None, raw=raw)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def _add_common(p: argparse.ArgumentParser) -> None:
    """공통 옵션을 메인/서브 parser 양쪽에 추가하여 위치 자유도를 높인다.

    SUPPRESS를 default로 두어, 서브 parser의 기본값이 메인 parser에서 이미 받은
    값을 덮어쓰지 않도록 한다.
    """
    p.add_argument("--oc", default=argparse.SUPPRESS,
                   help="OC(인증키). 없으면 환경변수 LAW_GO_KR_OC 사용.")
    p.add_argument("--type", default=argparse.SUPPRESS, choices=VALID_TYPES,
                   help="응답 포맷 (기본 XML)")
    p.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS,
                   help="XML/JSON pretty-print")
    p.add_argument("--save-to", metavar="PATH", default=argparse.SUPPRESS,
                   help="응답을 파일로도 저장")
    p.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                   help="요청 URL만 출력하고 호출·저장하지 않음 (get-asof는 버전 검색 URL, download는 URL과 저장 예정 경로)")
    p.add_argument("--no-cache", dest="no_cache", action="store_true",
                   default=argparse.SUPPRESS,
                   help="로컬 응답 캐시(TTL 24h)를 우회하고 항상 새로 호출")


def _apply_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """SUPPRESS로 인해 누락된 속성에 기본값을 채운다."""
    for key, default in (
        ("oc", None),
        ("type", "XML"),
        ("pretty", False),
        ("save_to", None),
        ("dry_run", False),
        ("no_cache", False),
    ):
        if not hasattr(args, key):
            setattr(args, key, default)
    return args


def _org_code(value: str) -> str:
    """--org·--sborg는 기관 코드만 받는다 — 기관명을 넣으면 API가 행정규칙은 조용히 무시하고 자치법규는 0건을 돌려준다."""
    v = value.strip()
    if not v.isdigit():
        raise argparse.ArgumentTypeError(
            f"기관 코드(숫자)만 받는다 — '{v}'는 기관명이다(예: 금융위원회 1160100, 서울특별시 6110000). "
            "코드를 모르면 --org 없이 검색하고 결과의 소관부처·지자체명으로 고른다.")
    return v


def _add_byl(p: argparse.ArgumentParser) -> None:
    p.add_argument("--byl", metavar="번호",
                   help="그 버전 본문의 별표(서식) 하나를 평문으로 출력 (예: 23, 4의2) — get: law·eflaw·admrul / "
                        "get-asof: law·admrul, --jo와 함께 못 씀. --save-to 파일은 원시 XML")
    p.add_argument("--byl-kind", dest="byl_kind", choices=list(BYL_KINDS), default="별표",
                   help="--byl 종류: 별표(기본) / 서식(법령 별지 서식·행정규칙 별지)")


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="law_api.py",
        description="국가법령정보 OPEN API CLI (법령/행정규칙/자치법규 검색·본문 조회)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common(p)

    sub = p.add_subparsers(dest="cmd", required=True)

    # search
    s = sub.add_parser(
        "search",
        help="목록 검색 (lawSearch.do)",
        description="법령/행정규칙/자치법규 목록 검색",
    )
    _add_common(s)
    s.add_argument("--target", required=True, choices=list(VALID_TARGETS.keys()),
                   help="검색 대상: " + ", ".join(f"{k}({v})" for k, v in VALID_TARGETS.items()))
    s.add_argument("--query", required=True, help="검색어 (법령명·행정규칙명·자치법규명 등)")
    s.add_argument(
        "--display", type=int, default=None,
        help=("결과 개수 (최대 100). target별 기본값: "
              "law/admrul/expc=20, ordin/licbyl/admbyl/ordinbyl=50."),
    )
    s.add_argument("--page", type=int, default=1, help="페이지 번호 (기본 1)")
    s.add_argument("--search", help="검색범위 코드 — law 등: 1=법령명(기본), 2=본문 / 별표 target(licbyl·admbyl·ordinbyl): "
                                    "1=별표명(기본), 2=관련법령명(모법별 목록), 3=본문")
    s.add_argument("--org", type=_org_code, help="소관부처 코드 (행정규칙) / 지자체 시·도 코드 (자치법규) — 숫자 코드만")
    s.add_argument("--nw", help="현행/연혁 필터 — eflaw: 1연혁,2시행예정,3현행 조합(예: 1,3) / ordin: 1현행, 2연혁")
    s.add_argument("--sborg", type=_org_code, help="자치법규 시·군·구 코드 (org와 함께)")
    s.add_argument("--knd", help="종류 코드 (target에 따라 의미 다름)")
    s.add_argument("--lid-search", "--lid", dest="lid_search",
                   help="LID(법령ID)로 한정 검색 — eflaw에서 특정 법령의 버전만 조회할 때 권장")
    s.add_argument("--efyd", help="시행일자 범위 YYYYMMDD~YYYYMMDD")
    s.add_argument("--ancyd", help="공포일자 범위 YYYYMMDD~YYYYMMDD")
    s.add_argument("--sort", help="정렬: lasc/ldes(법령명), dasc/ddes(공포일), ndes 등")
    s.add_argument("--raw", action="store_true",
                   help="원시 응답(XML) 그대로 출력 — 기본은 표. --save-to 파일은 늘 원시 응답")
    s.set_defaults(func=cmd_search)

    # get
    g = sub.add_parser(
        "get",
        help="본문 조회 (lawService.do)",
        description="법령/행정규칙/자치법규 본문 조회",
    )
    _add_common(g)
    g.add_argument("--target", required=True, choices=list(VALID_TARGETS.keys()),
                   help="대상: " + ", ".join(f"{k}({v})" for k, v in VALID_TARGETS.items()))
    g.add_argument("--mst", help="법령일련번호(law) / 버전 MST(eflaw, --efyd와 함께) / 자치법규일련번호(ordin)")
    g.add_argument("--id", dest="id", help="법령ID(law·eflaw) / 행정규칙일련번호(admrul) / 자치법규ID(ordin) / 해석례일련번호(expc)")
    g.add_argument("--lid", help="행정규칙ID (admrul)")
    g.add_argument("--lm", help="정식 법령명·규칙명 (약칭 불가, 띄어쓰기 무관) — law·eflaw·admrul·admrulOldAndNew "
                                "전용(ordin·expc는 API 미지원 — search로 번호 확보)")
    g.add_argument("--jo", help="조문번호 (예: 390, '제390조', '제10조의2'; admrul은 편·장식 '7-14', '제7-14조의2'도). "
                                "admrul·ordin은 받은 본문에서 발췌")
    g.add_argument("--promulgated", action="store_true",
                   help="target=law: 공포본 전용 — 공포일 기준 본문 그대로 출력(기본은 언제나 시행일 기준 본문(eflaw)으로 대체)")
    g.add_argument("--efyd", help="시행일자 YYYYMMDD (eflaw --mst와 함께)")
    g.add_argument("--ancyd", help="지원 안 함 — 서버가 무시해 현행본을 준다. 주면 exit 2(과거 본문은 get-asof)")
    g.add_argument("--lang", choices=["KO", "EN"],
                   help="EN: 영문 번역본(target=elaw로 조회, 참고용·일부 법령만) — law·eflaw의 --id·--lm, --jo는 로컬 발췌")
    g.add_argument("--text", action="store_true",
                   help="평문 출력(기본정보 한 줄 + 조문 텍스트, 부칙·별표 제외) — XML에서만. --save-to 파일은 원시 XML")
    g.add_argument("--addenda", nargs="*", metavar="공포번호",
                   help="그 버전 본문의 부칙 중 공포번호가 맞는 개정의 부칙만 평문 출력(번호 없으면 전부, 예: 8730) — "
                        "law·eflaw, --jo·--byl과 함께 못 씀. --save-to 파일은 원시 XML")
    _add_byl(g)
    g.set_defaults(func=cmd_get)

    # download
    d = sub.add_parser(
        "download",
        help="별표·서식 파일 다운로드 (+ PDF 텍스트 추출)",
        description="별표·서식 파일을 로컬에 저장하고, PDF면 pdftotext로 텍스트도 추출한다. "
                    "검색(licbyl/admbyl/ordinbyl)으로 받은 응답 XML 파일을 넘기거나, "
                    "직접 파일 URL을 넘긴다.",
    )
    _add_common(d)
    d.add_argument("--url", help="단건 다운로드: 별표·서식 파일의 URL (또는 /DRF/...로 시작하는 상대경로)")
    d.add_argument("--from-search-xml", dest="from_search_xml",
                   help="다건 다운로드: search 명령으로 저장한 별표·서식 검색 응답 XML 파일")
    d.add_argument("--filename", help="--url 모드 시 저장 파일명(확장자는 자동)")
    d.add_argument("--out-dir", dest="out_dir", default=None,
                   help="저장 디렉터리 (기본: ./byl_downloads/ — 현재 폴더가 스킬 설치 폴더면 임시 폴더)")
    d.add_argument("--extract-text", dest="extract_text", action="store_true",
                   help="PDF 다운로드 후 pdftotext로 텍스트 추출(.txt 동시 저장)")
    d.add_argument("--limit", type=int, default=10,
                   help="--from-search-xml 모드에서 최대 다운로드 개수 (기본 10, 0=무제한)")
    d.set_defaults(func=cmd_download)

    # versions — 시점별 버전(연혁 포함) 목록
    v = sub.add_parser(
        "versions",
        help="법령/자치법규의 시점별 버전(연혁 포함) 목록 조회",
        description="법령은 eflaw(LID 한정 권장), 자치법규는 ordin 현행+연혁(nw=2) 병합으로 "
                    "버전 목록을 (시행일자, MST) 중복 제거·시행일자 내림차순으로 출력한다.",
    )
    _add_common(v)
    v.add_argument("--target", choices=["law", "ordin", "admrul"], default="law",
                   help="law(기본) / ordin(자치법규) / admrul(행정규칙)")
    v.add_argument("--id", help="행정규칙일련번호 — target=admrul 체인 역추적(보조 경로) 시작점")
    v.add_argument("--max-steps", dest="max_steps", type=int, default=15,
                   help="admrul 체인 역추적 상한 (기본 15단계, 단계당 API 1회)")
    v.add_argument("--lid", help="계통ID — law: 법령ID(권장, 부분일치 오염 차단) / admrul: 행정규칙ID / ordin: 자치법규ID(--query 결과 한정)")
    v.add_argument("--query", help="법령명(정확명 일치 필터) / 자치법규명·행정규칙명(부분일치 — 명칭·종류 확인용)")
    v.add_argument("--nw", help="eflaw nw 필터 (기본 1,3 = 연혁+현행, 시행예정 배제 — get-asof는 기준일이 오늘 뒤면 1,2,3)")
    v.add_argument("--org", type=_org_code, help="소관부처 코드 (행정규칙) / 시·도 코드 (자치법규) — 숫자 코드만")
    v.add_argument("--sborg", type=_org_code, help="자치법규 시·군·구 코드 (org와 함께)")
    v.add_argument("--json", action="store_true", help="JSON으로 출력")
    v.set_defaults(func=cmd_versions)

    # get-asof — 기준일 시행본 본문 (검색→선택→본문 일괄, 선택 로직 내장)
    a = sub.add_parser(
        "get-asof",
        help="기준일(행위일·처분일 등)에 시행 중이던 본문 조회 — 검색→선택→본문 일괄",
        description="버전 목록에서 max{시행일자 ≤ 기준일}을 결정론적으로 선택해 본문을 반환한다. "
                    "선택 결과(시행일자·MST·현행/연혁, 직후 개정의 공포 정보)는 stderr 헤더로 출력된다.",
    )
    _add_common(a)
    a.add_argument("--date", required=True, help="기준일 YYYYMMDD (예: 처분일 20190601)")
    a.add_argument("--target", choices=["law", "ordin", "admrul"], default="law",
                   help="law(기본) / ordin(자치법규) / admrul(행정규칙)")
    a.add_argument("--id", help="행정규칙일련번호 — target=admrul 체인 역추적(보조 경로) 시작점")
    a.add_argument("--max-steps", dest="max_steps", type=int, default=None,
                   help="단계 상한(단계당 API 1회) — admrul 체인 역추적 기본 15 / law --jo 조문 기준 판례식 탐색 기본 10")
    a.add_argument("--lid", help="계통ID — law: 법령ID(권장) / admrul: 행정규칙ID / ordin: 자치법규ID(--query 결과 한정)")
    a.add_argument("--query", help="법령명(정확명) / 행정규칙명·자치법규명(부분일치 — 정확명이면 계통 자동 선택)")
    a.add_argument("--nw", help="eflaw nw 필터 (기본 1,3)")
    a.add_argument("--org", type=_org_code, help="소관부처 코드 (행정규칙) / 시·도 코드 (자치법규) — 숫자 코드만")
    a.add_argument("--sborg", type=_org_code, help="자치법규 시·군·구 코드")
    a.add_argument("--jo", help="조문번호 (예: 46 또는 '제46조'; admrul은 편·장식 '7-14', '제7-14조의2'도). "
                                "admrul·ordin은 받은 본문에서 발췌")
    a.add_argument("--text", action="store_true",
                   help="평문 출력(기본정보 한 줄 + 조문 텍스트) — XML에서만. --save-to 파일은 원시 XML")
    a.add_argument("--addenda", nargs="*", metavar="공포번호",
                   help="기준일 뒤 개정의 부칙(적용례·경과조치)만 평문 출력 — 번호를 주면 그 개정만, 없으면 기준일 뒤 개정 "
                        "전부. 현행 본문의 부칙에서 뽑는다. target=law, --jo·--byl과 함께 못 씀")
    _add_byl(a)
    a.set_defaults(func=cmd_get_asof)

    return p


def main(argv: list[str] | None = None) -> None:
    global _NO_CACHE
    parser = make_parser()
    args = parser.parse_args(argv)
    args = _apply_defaults(args)
    _NO_CACHE = bool(getattr(args, "no_cache", False))
    args.func(args)


if __name__ == "__main__":
    main()
