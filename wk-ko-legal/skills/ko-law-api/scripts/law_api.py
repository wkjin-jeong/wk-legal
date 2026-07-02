#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
국가법령정보 OPEN API CLI
========================

법제처 「국가법령정보 공동활용」 OPEN API(law.go.kr/DRF)를 호출하여
법령(law) / 행정규칙(admrul) / 자치법규(ordin)의 목록 검색 및 본문 조회를 수행한다.

사용 예
-------
    # 법령 검색
    python law_api.py search --target law --query "민법" --display 5

    # 법령 본문 (MST 사용)
    python law_api.py get --target law --mst 246234

    # 법령의 특정 조문만 (제390조 -> JO=039000)
    python law_api.py get --target law --mst 246234 --jo 039000

    # 행정규칙 검색·본문
    python law_api.py search --target admrul --query "전자금융감독규정"
    python law_api.py get --target admrul --id 2100000200000

    # 자치법규 검색·본문
    python law_api.py search --target ordin --query "서울특별시 옥외광고물 조례"
    python law_api.py get --target ordin --mst 1234567

OC(인증키) 처리
---------------
- `--oc <값>` 인자가 1순위.
- 없으면 환경변수 `LAW_GO_KR_OC`.
- 둘 다 없으면 .env 자동 탐색(resolve_oc 참조). 모두 없으면 안내 메시지와 함께 오류 종료.

응답 처리
---------
- 기본 응답 포맷은 XML. `--type JSON` 또는 `--type HTML`로 변경 가능.
- 본 스크립트는 응답 본문을 stdout으로 그대로 출력한다(파싱은 호출자에게 위임).
- `--pretty` 플래그를 주면 XML/JSON을 보기 좋게 정렬한다.
- `--save-to <경로>`를 주면 응답 본문을 파일로도 저장한다.

종속성
------
표준 라이브러리만 사용 (urllib, xml.etree, json, argparse).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.dom.minidom
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
    .env 파일 탐색 후보를 우선순위 순으로 반환한다.
    우선순위:
      1) 환경변수 LAW_API_DOTENV로 사용자가 명시한 경로
      2) 현재 작업 디렉터리의 .env
      3) 스크립트가 위치한 폴더(scripts/)의 .env
      4) 스크립트 폴더의 부모(=skill 폴더)의 .env
      5) ~/.law_api.env
      6) ~/.config/korean-law-api/.env
    """
    candidates: list[str] = []

    explicit = os.environ.get("LAW_API_DOTENV")
    if explicit:
        candidates.append(explicit)

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
    탐색 경로 중 첫 번째로 존재하는 .env 파일을 로드해 os.environ에 채운다.
    이미 설정된 환경변수는 덮어쓰지 않는다(환경변수 우선 유지).
    반환: 로드한 파일의 절대 경로(없으면 None).
    """
    for path in _candidate_dotenv_paths():
        if not os.path.isfile(path):
            continue
        kv = _parse_dotenv(path)
        if not kv:
            continue
        applied: list[str] = []
        for k, v in kv.items():
            if k not in os.environ:
                os.environ[k] = v
                applied.append(k)
        if verbose:
            if applied:
                sys.stderr.write(
                    f"INFO: .env 로드: {path} (적용된 키: {', '.join(applied)})\n"
                )
            else:
                sys.stderr.write(
                    f"INFO: .env 발견했으나 모든 키가 이미 환경변수에 있음: {path}\n"
                )
        return path
    return None


def resolve_oc(cli_oc: str | None) -> str:
    """
    OC 우선순위: --oc 인자 > LAW_GO_KR_OC 환경변수 > .env 자동 로드.

    .env 자동 탐색 경로(첫 발견 후 멈춤):
      1) $LAW_API_DOTENV
      2) ./.env (cwd)
      3) <스크립트 폴더>/.env
      4) <스크립트 폴더의 부모 = skill 폴더>/.env
      5) ~/.law_api.env
      6) ~/.config/korean-law-api/.env
    """
    if cli_oc:
        return cli_oc

    oc = os.environ.get("LAW_GO_KR_OC")
    if oc:
        return oc

    # 환경변수도 인자도 없으면 .env 자동 로드 시도
    verbose = os.environ.get("LAW_API_VERBOSE", "").lower() in ("1", "true", "yes")
    loaded_path = _load_dotenv_into_environ(verbose=verbose)
    oc = os.environ.get("LAW_GO_KR_OC")
    if oc:
        return oc

    sys.stderr.write(
        "ERROR: OC(인증키)가 없습니다. 다음 중 하나로 제공하세요.\n"
        "  - CLI 인자:    --oc <your_id>\n"
        "  - 환경변수:    export LAW_GO_KR_OC=<your_id>\n"
        "  - .env 파일:   다음 위치 중 한 곳에 'LAW_GO_KR_OC=<your_id>' 한 줄을 작성\n"
        f"      • {os.path.join(os.getcwd(), '.env')}\n"
        "      • <skill 폴더>/.env  (예: ~/claude/korean-law-api/.env)\n"
        "      • ~/.law_api.env\n"
        "      • ~/.config/korean-law-api/.env\n"
        "      • $LAW_API_DOTENV 로 직접 경로 지정도 가능\n"
        "  (파일 형식: 'LAW_GO_KR_OC=값' 또는 'export LAW_GO_KR_OC=\"값\"', '#' 주석 허용)\n"
    )
    if loaded_path:
        sys.stderr.write(f"NOTE: .env는 발견했으나 LAW_GO_KR_OC 키가 없습니다: {loaded_path}\n")
    sys.exit(2)


def build_url(base: str, params: dict[str, str]) -> str:
    """None/빈 값 제거 후 querystring 조립."""
    cleaned = {k: v for k, v in params.items() if v is not None and v != ""}
    qs = urllib.parse.urlencode(cleaned, doseq=True, encoding="utf-8")
    return f"{base}?{qs}"


def http_get(url: str, timeout: int = 20, no_cache: bool = False,
             strict_errors: bool = False) -> str:
    """HTTP GET. UTF-8 디코딩 실패 시 EUC-KR 폴백.

    캐시(TTL 24h)를 http_get 계층에서 처리한다 — lawSearch.do·lawService.do 공통.
    적중 시 stderr에 'CACHE:' 접두 한 줄을 남긴다. --no-cache는 no_cache=True로 전달된다.

    strict_errors=True면 HTML 오류 페이지(OC 미등록 등)나 API 오류 필드(resultCode≠00)를
    감지해 명확한 메시지와 함께 비정상 종료한다. 응답 본문을 스스로 파싱·검사하는
    내부 호출부(_search_pages 등)는 False로 두어 관대하게 처리한다.
    """
    # 1) 캐시 조회
    if not no_cache:
        cached = _cache_read(url)
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
        _enforce_response_ok(body, url)

    # 4) 성공 응답만 캐시 — HTTP 200 & HTML 오류 페이지 아님 & API 오류 필드 없음.
    #    (비-strict 내부 순회에서 resultCode≠00 XML을 24h 캐시하는 것을 방지)
    if not no_cache and not looks_like_html_error(body) and find_api_error(body) is None:
        _cache_write(url, _mask_oc_in_body(body, url))

    return body


def _enforce_response_ok(body: str, url: str) -> None:
    """HTML 오류 페이지 또는 API 오류 필드를 감지하면 안내와 함께 비정상 종료."""
    if looks_like_html_error(body):
        sys.stderr.write(
            "ERROR: API가 XML/JSON이 아닌 HTML 페이지를 반환했습니다 — "
            "OC(인증키) 미등록·오타, 일일 호출 한도 초과, 또는 파라미터 조합 오류일 수 있습니다.\n"
            f"  URL(OC 제외): {_strip_oc_from_url(url)}\n"
            "  → open.law.go.kr에서 OC 등록 상태를 확인하고, 파라미터를 점검하세요.\n"
        )
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


def maybe_pretty(body: str, fmt: str) -> str:
    """XML/JSON이면 pretty-print, 그 외(HTML 등)는 원본 반환."""
    fmt = fmt.upper()
    try:
        if fmt == "XML":
            dom = xml.dom.minidom.parseString(body)
            return dom.toprettyxml(indent="  ", encoding=None)
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
        print(url)
        return

    body = http_get(url, no_cache=args.no_cache, strict_errors=True)
    output = maybe_pretty(body, args.type) if args.pretty else body
    print(output)
    if args.save_to:
        with open(args.save_to, "w", encoding="utf-8") as f:
            f.write(output)


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

def cmd_get(args: argparse.Namespace) -> None:
    oc = resolve_oc(args.oc)

    # 별표·서식 target은 본문 조회를 지원하지 않음 (search + download 사용)
    if args.target in SEARCH_ONLY_TARGETS:
        sys.stderr.write(
            f"ERROR (target={args.target}): 별표·서식은 본문 조회 API가 별도로 제공되지 않습니다.\n"
            f"  → 'search --target {args.target} --query ...'로 목록을 조회하고,\n"
            f"     'download --from-search-xml <저장한 XML>' 또는 'download --url <파일URL>'로 다운로드하세요.\n"
        )
        sys.exit(2)

    # target별 필요한 식별자 검증
    # - law:    MST(=법령일련번호) 또는 ID(=법령ID) 또는 LM
    # - admrul: ID(=행정규칙일련번호, 긴 숫자) 또는 LID 또는 LM
    # - ordin:  MST(=자치법규일련번호) 또는 ID(=자치법규ID) 또는 LM
    # - expc:   ID(=법령해석례일련번호) 또는 LM(=안건명)
    if args.target == "eflaw":
        if args.id:
            sys.stderr.write(
                "ERROR (target=eflaw): 본문 조회에 ID(법령ID)를 쓰면 현행본이 반환되고 efYd가 무시됩니다(공식 가이드).\n"
                "  과거 시행본은 --mst <버전 MST> --efyd <그 버전의 시행일자> 로 조회하세요.\n"
            )
            sys.exit(2)
        if not (args.mst and args.efyd):
            sys.stderr.write(
                "ERROR (target=eflaw): --mst(버전 MST)와 --efyd(그 버전의 시행일자)가 모두 필요합니다.\n"
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
        if not (args.mst or args.id or args.lm):
            sys.stderr.write(
                "ERROR (target=ordin): --mst(=자치법규일련번호) / --id(=자치법규ID) / --lm 중 하나는 필수.\n"
            )
            sys.exit(2)
    elif args.target == "expc":
        if not (args.id or args.lm):
            sys.stderr.write(
                "ERROR (target=expc): --id(=법령해석례일련번호) / --lm(=안건명) 중 하나는 필수.\n"
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
    if args.jo:
        params["JO"] = encode_jo(args.jo)
    if args.efyd:
        params["efYd"] = args.efyd
    if args.ancyd:
        params["ancYd"] = args.ancyd
    if args.lang:
        params["LANG"] = args.lang             # KO/EN

    url = build_url(BASE_SERVICE, params)
    if args.dry_run:
        print(url)
        return

    body = _get_body_with_jo_fallback(args, params, url)
    output = maybe_pretty(body, args.type) if args.pretty else body
    print(output)
    if args.save_to:
        with open(args.save_to, "w", encoding="utf-8") as f:
            f.write(output)


def _get_body_with_jo_fallback(args: argparse.Namespace, params: dict[str, str],
                               url: str) -> str:
    """본문 조회 + JO 자동 폴백.

    법령(law) 본문에서 JO를 지정했는데 결과가 비면(조문 컨테이너 부재), 대체 인코딩으로
    1회 자동 재시도한다(예: 039000 → 0390). type=XML일 때만 판정 가능하므로 그 경우에만
    폴백한다. 재시도 시 stderr로 알린다.
    """
    body = http_get(url, no_cache=args.no_cache, strict_errors=True)
    can_fallback = (
        args.jo and args.target == "law" and args.type.upper() == "XML"
    )
    if can_fallback and _jo_result_empty(body):
        alt = alt_encode_jo(args.jo)
        if alt and alt != params.get("JO"):
            sys.stderr.write(
                f"NOTE: JO={params.get('JO')} 결과가 비어 대체 인코딩 JO={alt}로 자동 재시도합니다.\n"
            )
            retry_params = dict(params)
            retry_params["JO"] = alt
            retry_url = build_url(BASE_SERVICE, retry_params)
            retry_body = http_get(retry_url, no_cache=args.no_cache, strict_errors=True)
            if not _jo_result_empty(retry_body):
                return retry_body
            sys.stderr.write(
                "NOTE: 대체 인코딩으로도 해당 조문을 찾지 못했습니다 — "
                "조문번호를 확인하거나 JO 없이 전체 본문을 받아 발췌하세요.\n"
            )
    return body


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


def _http_download(url: str, out_path: str, timeout: int = 30) -> tuple[str, str]:
    """파일 바이너리 다운로드. (저장경로, content_type) 반환."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "claude-korean-law-api/1.0 (+skill:korean-law-api)",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ct = resp.headers.get("Content-Type", "")
        data = resp.read()
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


def cmd_download(args: argparse.Namespace) -> None:
    out_dir = args.out_dir or "./byl_downloads"
    os.makedirs(out_dir, exist_ok=True)

    # 1) URL 직접 지정 모드
    if args.url:
        url = _abs_url(args.url)
        ext = _guess_ext(url)
        fname_base = _safe_filename(args.filename or os.path.basename(urllib.parse.urlparse(url).path) or "byl_file")
        out_path = os.path.join(out_dir, fname_base if fname_base.endswith(ext) else fname_base + ext)
        saved, ct = _http_download(url, out_path)
        # ext 보정 (Content-Type 기반)
        new_ext = _guess_ext(url, ct)
        if not saved.lower().endswith(new_ext):
            new_path = os.path.splitext(saved)[0] + new_ext
            os.rename(saved, new_path)
            saved = new_path
        print(f"SAVED: {saved} (Content-Type: {ct})")
        if args.extract_text and saved.lower().endswith(".pdf"):
            text = _extract_pdf_text(saved)
            if text is not None:
                txt_path = os.path.splitext(saved)[0] + ".txt"
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(text)
                print(f"TEXT: {txt_path}")
            else:
                sys.stderr.write("NOTE: pdftotext 미설치 또는 추출 실패 — PDF만 저장됨.\n")
        elif args.extract_text and (saved.lower().endswith(".hwp") or saved.lower().endswith(".hwpx")):
            sys.stderr.write("NOTE: HWP/HWPX는 자동 텍스트 추출 미지원 — 파일만 저장됨.\n")
        return

    # 2) 검색 응답 XML 파싱 모드
    if args.from_search_xml:
        with open(args.from_search_xml, "r", encoding="utf-8") as f:
            xml_text = f.read()
        items = _collect_links_from_xml(xml_text)
        if not items:
            sys.stderr.write(
                "WARN: 응답 XML에서 다운로드 URL을 찾지 못했습니다. "
                "라이브 호출로 응답 구조를 확인 후 BYL_LINK_TAG_HINTS를 보강해야 합니다.\n"
            )
            sys.exit(1)
        limit = args.limit if args.limit > 0 else len(items)
        target_items = items[:limit]
        print(f"INFO: 총 {len(items)}건 중 {len(target_items)}건 다운로드 시작 "
              f"(PDF 변환본 우선 선택; PDF 없으면 원본 사용)")

        for idx, it in enumerate(target_items):
            url = _abs_url(it["link"])
            # is_pdf=True인 경우 확장자를 .pdf로 강제 (URL에서 확장자 추론이 어려울 때 안전)
            ext = ".pdf" if it.get("is_pdf") else _guess_ext(url)
            base = _safe_filename(f"{idx+1:03d}_{it['name']}")
            out_path = os.path.join(out_dir, base + ext)
            try:
                saved, ct = _http_download(url, out_path)
                # Content-Type 기반 확장자 재보정 (PDF 우선 의도가 깨졌을 수도 있음)
                new_ext = _guess_ext(url, ct)
                if it.get("is_pdf"):
                    new_ext = ".pdf"  # PDF 링크임이 명확하면 강제
                if not saved.lower().endswith(new_ext):
                    new_path = os.path.splitext(saved)[0] + new_ext
                    os.rename(saved, new_path)
                    saved = new_path
                kind = "PDF" if it.get("is_pdf") else "RAW"
                print(f"[{idx+1}/{len(target_items)}] SAVED ({kind}): {saved}")
                if args.extract_text and saved.lower().endswith(".pdf"):
                    text = _extract_pdf_text(saved)
                    if text is not None:
                        txt_path = os.path.splitext(saved)[0] + ".txt"
                        with open(txt_path, "w", encoding="utf-8") as f:
                            f.write(text)
                        print(f"        TEXT: {txt_path}")
                    else:
                        sys.stderr.write(
                            f"        NOTE: pdftotext 미설치 또는 추출 실패 — {saved}\n"
                        )
                elif args.extract_text and (saved.lower().endswith(".hwp") or saved.lower().endswith(".hwpx")):
                    sys.stderr.write(
                        f"        NOTE: HWP/HWPX는 자동 텍스트 추출 미지원 — {saved} (파일만 저장됨)\n"
                    )
            except Exception as e:
                sys.stderr.write(f"[{idx+1}] FAILED: {url}  ({e})\n")
        return

    sys.stderr.write("ERROR: --url 또는 --from-search-xml 중 하나를 지정해야 합니다.\n")
    sys.exit(2)


def encode_jo(raw: str) -> str:
    """
    조문번호 인코딩.
    - 사용자가 '제390조'처럼 입력하거나 숫자만 입력해도 6자리 zero-padding 처리.
    - 가지조는 '제390조의2'·'390의2' 형태를 인식해 조 4자리 + 가지조 2자리(039002)로 인코딩.
    - 이미 6자리 숫자 형태면 그대로 통과.
    - 항·호 단위 인코딩(예: 03900100 = 제390조 제1항)은 스크립트에서 추측하지 않고 사용자가 명시한 값을 그대로 보낸다.
    """
    raw = raw.strip()
    # 가지조 — 숫자 이어붙이기('제390조의2' → 390200 = 제3902조 슬롯)로 엉뚱한 조문이 되는 것을 방지.
    m = re.fullmatch(r"제?\s*(\d{1,4})\s*조?\s*의\s*(\d{1,2})\s*조?", raw)
    if m:
        return m.group(1).zfill(4) + m.group(2).zfill(2)
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return raw
    # 6자리 미만이면 0-padding (앞 4자리는 조, 뒤 2자리는 가지조 — 일반적 가지조 0)
    if len(digits) <= 4:
        return digits.zfill(4) + "00"
    if len(digits) == 6:
        return digits
    # 6자리 초과는 그대로 (호·항 인코딩 등 사용자가 직접 만든 값으로 간주)
    return digits


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


def _dedupe_sort(rows: list[dict]) -> list[dict]:
    """(시행일자, MST) 중복 제거 + 시행일자 내림차순."""
    seen: set = set()
    out: list[dict] = []
    for r in rows:
        k = (r.get("시행일자"), r.get("MST"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    out.sort(key=lambda r: (r.get("시행일자") or "", r.get("MST") or ""), reverse=True)
    return out


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
            "법령ID": (el.findtext("법령ID") or "").strip(),
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
                "구분": label,
                "공포일자": (el.findtext("공포일자") or "").strip(),
                "공포번호": (el.findtext("공포번호") or "").strip(),
                "지자체": (el.findtext("지자체기관명") or "").strip(),
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
                "구분": (el.findtext("현행연혁구분") or "").strip() or "연혁",
                "공포일자": (el.findtext("발령일자") or "").strip(),
                "공포번호": (el.findtext("발령번호") or "").strip(),
                "법령구분": (el.findtext("행정규칙종류") or "").strip(),
            })
    return _dedupe_sort(rows)


def _admrul_bi_row(bi) -> dict:
    return {
        "명칭": (bi.findtext("행정규칙명") or "").strip(),
        "시행일자": (bi.findtext("시행일자") or "").strip(),
        "MST": (bi.findtext("행정규칙일련번호") or "").strip(),
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
    return _dedupe_sort(rows)


def _collect_versions(args: argparse.Namespace, oc: str, date: str | None = None) -> list[dict]:
    if args.target == "ordin":
        return _ordin_versions(oc, args.query, args.org, args.sborg)
    if args.target == "admrul":
        if args.query:
            return _admrul_versions_nw(oc, args.query, org=args.org)   # 기본: nw=2 연혁 직접 검색
        if args.id:
            return _admrul_versions(oc, args.id, date=date, max_steps=args.max_steps)  # 보조: 체인 역추적
        sys.stderr.write(
            "ERROR: target=admrul 버전 조회에는 --query(연혁 직접 검색, 권장) 또는 --id(체인 역추적, 보조)가 필요합니다.\n")
        sys.exit(2)
    return _law_versions(oc, args.lid, args.query, args.nw or "1,3")


def cmd_versions(args: argparse.Namespace) -> None:
    oc = resolve_oc(args.oc)
    if args.dry_run:
        base = {"OC": oc, "target": "eflaw" if args.target == "law" else "ordin",
                "type": "XML", "display": "100", "page": "1"}
        if args.target == "law":
            base["nw"] = args.nw or "1,3"
            if args.lid:
                base["LID"] = args.lid
            elif args.query:
                base["query"] = args.query
        else:
            base["query"] = args.query or ""
        print(build_url(BASE_SEARCH, base))
        return
    rows = _collect_versions(args, oc)
    if args.target == "admrul" and rows and not args.query:
        sys.stderr.write("NOTE: 신구법비교 체인 역추적 결과입니다(한 단계 = API 1회). 통상은 --query(연혁 직접 검색)를 권장.\n")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(f"# {args.target} 버전 {len(rows)}건 — 시행일자 내림차순, (시행일자, MST) 중복 제거")
    print("시행일자 | MST | 구분 | 공포일자 | 공포번호 | 명칭")
    for r in rows:
        extra = f" ({r['지자체']})" if r.get("지자체") else ""
        print(f"{r['시행일자']} | {r['MST']} | {r['구분']} | {r['공포일자']} | {r['공포번호']} | {r['명칭']}{extra}")
    if not rows:
        sys.stderr.write("NOTE: 0건 — target=law면 --lid 사용을, 명칭 변경(조례)·법령명 표기를 확인하세요.\n")


def cmd_get_asof(args: argparse.Namespace) -> None:
    oc = resolve_oc(args.oc)
    date = "".join(ch for ch in (args.date or "") if ch.isdigit())
    if len(date) != 8:
        sys.stderr.write("ERROR: --date는 YYYYMMDD 8자리(예: 20190601)여야 합니다.\n")
        sys.exit(2)

    rows = _collect_versions(args, oc, date=date)
    if not rows:
        sys.stderr.write("ERROR: 버전을 찾지 못했습니다 — target=law면 --lid 사용, 명칭·표기를 확인하세요.\n")
        sys.exit(2)
    names = {r["명칭"] for r in rows if r.get("명칭")}
    if len(names) > 1:
        sys.stderr.write(
            f"[get-asof] ⚠ 후보에 서로 다른 명칭 {len(names)}종이 섞여 있습니다(개정에 따른 명칭 변경 또는 동명 이종). "
            "선택본 명칭을 반드시 확인하세요.\n")

    eligible = [r for r in rows if r["시행일자"] and r["시행일자"] <= date]
    if not eligible:
        earliest = min(r["시행일자"] for r in rows if r["시행일자"])
        sys.stderr.write(
            f"ERROR: 기준일 {date} 이전에 시행 중이던 버전이 없습니다(최초 시행일: {earliest}).\n"
            "  제정 전 시점입니다 — 기준일 또는 법령 특정을 재확인하세요.\n"
        )
        sys.exit(2)
    pick = max(eligible, key=lambda r: (r["시행일자"], r["MST"]))
    newer = [r for r in rows if r["시행일자"] > pick["시행일자"]]
    nxt = min(newer, key=lambda r: (r["시행일자"], r["MST"])) if newer else None

    # 선택 결과 헤더 — stderr (stdout은 응답 본문만 유지)
    sys.stderr.write(
        f"[get-asof] 기준일 {date} → 선택본: {pick['명칭']} | 시행 {pick['시행일자']} | "
        f"MST {pick['MST']} | {pick['구분']} | 공포 {pick['공포일자']} 제{pick['공포번호']}호\n"
    )
    if pick["구분"] != "현행":
        end = _prev_day(nxt["시행일자"]) if nxt else ""
        period = f"{pick['시행일자']} ~ {end}" if end else f"{pick['시행일자']} ~"
        sys.stderr.write(f"[get-asof] ⚠ 연혁본 — 현행 본문이 아닙니다. 시행기간: {period}. 인용 시 구법 표기 필수.\n")
    else:
        sys.stderr.write("[get-asof] 기준일 현재 시행본이 현행과 동일합니다.\n")
    if nxt:
        kind = nxt.get("법령구분") or {"ordin": "조례·규칙", "admrul": "고시·예규"}.get(args.target, "법률")
        sys.stderr.write(
            f"[get-asof] 직후 개정: 공포 {_dot_date(nxt['공포일자'])} {kind} 제{nxt['공포번호']}호 (시행 {nxt['시행일자']})"
            f" → 판례식: 구 {pick['명칭']}({_dot_date(nxt['공포일자'])} {kind} 제{nxt['공포번호']}호로 개정되기 전의 것)\n"
        )

    # 본문 조회
    if args.target == "ordin":
        params: dict[str, str] = {"OC": oc, "target": "ordin", "type": args.type, "MST": pick["MST"]}
    elif args.target == "admrul":
        params = {"OC": oc, "target": "admrul", "type": args.type, "ID": pick["MST"]}
    else:
        params = {"OC": oc, "target": "eflaw", "type": args.type,
                  "MST": pick["MST"], "efYd": pick["시행일자"]}
    if args.jo:
        if args.target == "law":
            params["JO"] = encode_jo(args.jo)
        else:
            sys.stderr.write("NOTE: --jo는 법령(law)에서만 지원됩니다 — 전체 본문에서 해당 조를 발췌하세요.\n")
    url = build_url(BASE_SERVICE, params)
    if args.dry_run:
        print(url)
        return
    body = http_get(url, no_cache=args.no_cache, strict_errors=True)
    # JO 자동 폴백 — get-asof의 법령(eflaw) 본문에서 JO 지정 결과가 비면 대체 인코딩 1회 재시도.
    if args.jo and args.target == "law" and args.type.upper() == "XML" and _jo_result_empty(body):
        alt = alt_encode_jo(args.jo)
        if alt and alt != params.get("JO"):
            sys.stderr.write(
                f"NOTE: JO={params.get('JO')} 결과가 비어 대체 인코딩 JO={alt}로 자동 재시도합니다.\n"
            )
            retry_params = dict(params)
            retry_params["JO"] = alt
            retry_body = http_get(build_url(BASE_SERVICE, retry_params),
                                  no_cache=args.no_cache, strict_errors=True)
            if not _jo_result_empty(retry_body):
                body = retry_body
            else:
                sys.stderr.write(
                    "NOTE: 대체 인코딩으로도 해당 조문을 찾지 못했습니다 — "
                    "조문번호를 확인하거나 JO 없이 전체 본문을 받아 발췌하세요.\n"
                )
    output = maybe_pretty(body, args.type) if args.pretty else body
    print(output)
    if args.save_to:
        with open(args.save_to, "w", encoding="utf-8") as f:
            f.write(output)


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
                   help="요청 URL만 출력하고 호출하지 않음")
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
    s.add_argument("--search", help="검색범위 코드 (target=law: 1=법령명, 2=본문 등)")
    s.add_argument("--org", help="소관부처 (행정규칙) / 지자체 시·도 코드 (자치법규)")
    s.add_argument("--nw", help="현행/연혁 필터 — eflaw: 1연혁,2시행예정,3현행 조합(예: 1,3) / ordin: 1현행, 2연혁")
    s.add_argument("--sborg", help="자치법규 시·군·구 코드 (org와 함께)")
    s.add_argument("--knd", help="종류 코드 (target에 따라 의미 다름)")
    s.add_argument("--lid-search", "--lid", dest="lid_search",
                   help="LID(법령ID)로 한정 검색 — eflaw에서 특정 법령의 버전만 조회할 때 권장")
    s.add_argument("--efyd", help="시행일자 범위 YYYYMMDD~YYYYMMDD")
    s.add_argument("--ancyd", help="공포일자 범위 YYYYMMDD~YYYYMMDD")
    s.add_argument("--sort", help="정렬: lasc/ldes(법령명), dasc/ddes(공포일), ndes 등")
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
    g.add_argument("--mst", help="법령 마스터번호 (target=law)")
    g.add_argument("--id", dest="id", help="법령ID/행정규칙ID/자치법규ID")
    g.add_argument("--lid", help="LID (행정규칙·자치법규에서 사용)")
    g.add_argument("--lm", help="법령명/규칙명 직접 지정")
    g.add_argument("--jo", help="조문번호 (예: 390 또는 039000 또는 '제390조')")
    g.add_argument("--efyd", help="시행일자 YYYYMMDD")
    g.add_argument("--ancyd", help="공포일자 YYYYMMDD")
    g.add_argument("--lang", choices=["KO", "EN"], help="언어 (KO 기본, EN: 영문번역본 — 일부 법령만)")
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
                   help="저장 디렉터리 (기본: ./byl_downloads/)")
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
                   help="law(기본) / ordin(자치법규) / admrul(행정규칙 — 체인 역추적)")
    v.add_argument("--id", help="행정규칙일련번호 — target=admrul 체인 역추적(보조 경로) 시작점")
    v.add_argument("--max-steps", dest="max_steps", type=int, default=15,
                   help="admrul 체인 역추적 상한 (기본 15단계, 단계당 API 1회)")
    v.add_argument("--lid", help="법령ID — target=law에서 해당 법령의 버전만 조회(권장, 부분일치 오염 차단)")
    v.add_argument("--query", help="법령명(정확명 일치 필터) / 자치법규명·행정규칙명(부분일치 — 명칭·종류 확인용)")
    v.add_argument("--nw", help="eflaw nw 필터 (기본 1,3 = 연혁+현행, 시행예정 배제)")
    v.add_argument("--org", help="자치법규 시·도 코드")
    v.add_argument("--sborg", help="자치법규 시·군·구 코드 (org와 함께)")
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
                   help="law(기본) / ordin(자치법규) / admrul(행정규칙 — 체인 역추적)")
    a.add_argument("--id", help="행정규칙일련번호 — target=admrul 체인 역추적(보조 경로) 시작점")
    a.add_argument("--max-steps", dest="max_steps", type=int, default=15,
                   help="admrul 체인 역추적 상한 (기본 15단계)")
    a.add_argument("--lid", help="법령ID (target=law 권장)")
    a.add_argument("--query", help="법령명(정확명) / 자치법규명")
    a.add_argument("--nw", help="eflaw nw 필터 (기본 1,3)")
    a.add_argument("--org", help="자치법규 시·도 코드")
    a.add_argument("--sborg", help="자치법규 시·군·구 코드")
    a.add_argument("--jo", help="조문번호 (예: 46 또는 '제46조')")
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
