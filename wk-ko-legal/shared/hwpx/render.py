#!/usr/bin/env python3
"""서면 markdown → hwpx 변환(표지 양식 + 프로필). 표준 라이브러리만 쓴다.

명령
  build  초안.md [-o 출력.hwpx] [--force] [--profile 경로] [--side 원고측|피고측] [--template 양식.hwpx]
  check  결과.hwpx [--md 초안.md]          구조 검사(+ 초안 대비 글자 누락 검사)
  mark   양식.hwpx -o 표지본.hwpx [--no-background]   한글 양식에 표지({{…}})를 넣는다
  where  [초안.md] [--profile 경로]          쓰일 프로필·양식을 보여 준다

규칙의 정본은 shared/hwpx-출력-정책.md 이다.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import os
import re
import struct
import sys
import zipfile
from collections import Counter
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROFILE = os.path.join(HERE, '기본프로필.json')
PROFILE_REL = os.path.join('.wk-legal', 'hwpx', 'profile.json')
PX = 75                              # 96dpi 1px = 75 HWPUNIT
MARKERS = ('표제', '당사자', '모두문', '다음', '본문', '날짜', '대리인', '법인', '변호사', '법원')
OUTLINE_RE = [                        # 개요 1~6: 1. / 가. / 1) / 가) / (1) / (가)
    re.compile(r'^(\d{1,2})\.\s+(?!\d{1,2}\.)'),
    re.compile(r'^([가나다라마바사아자차카타파하])\.\s+'),
    re.compile(r'^(\d{1,2})\)\s+'),
    re.compile(r'^([가나다라마바사아자차카타파하])\)\s+'),
    re.compile(r'^\((\d{1,2})\)\s+'),
    re.compile(r'^\(([가나다라마바사아자차카타파하])\)\s+'),
]
HANGUL_SEQ = '가나다라마바사아자차카타파하'
PARTY_LABELS = sorted("""사건 사건명 원고 피고 원고들 피고들 원고겸반소피고 피고겸반소원고 반소원고 반소피고 채권자 채무자 제3채무자
신청인 피신청인 항고인 재항고인 상대방 청구인 피청구인 항소인 피항소인 상고인 피상고인 피고인 피의자 고소인 피고소인
고발인 피고발인 진정인 피진정인 참가인 보조참가인 독립당사자참가인 소송수계인 원고승계참가인 신청외""".split(), key=len, reverse=True)
DATE_RE = re.compile(r'^(\d{4})\.\s*(\d{1,2})?\.?\s*(\d{1,2})?\.?$')
_ids = itertools.count(1900000001)


class RenderError(Exception):
    pass


# ───────────────────────── 프로필 ─────────────────────────

def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def find_profile(explicit=None, start_dirs=()):
    """프로필 탐색: --profile → WK_LEGAL_HWPX_PROFILE → 초안·현재 폴더에서 위로 .wk-legal/hwpx/profile.json
    → ~/.config/wk-legal/hwpx/profile.json → 없음(None)."""
    def as_file(p):
        p = os.path.expanduser(p)
        return os.path.join(p, 'profile.json') if os.path.isdir(p) else p
    if explicit:
        f = as_file(explicit)
        if not os.path.isfile(f):
            raise RenderError(f'프로필 없음: {f}')
        return f
    env = os.environ.get('WK_LEGAL_HWPX_PROFILE')
    if env:
        f = as_file(env)
        if not os.path.isfile(f):
            raise RenderError(f'WK_LEGAL_HWPX_PROFILE이 가리키는 프로필 없음: {f}')
        return f
    seen = set()
    for d in start_dirs:
        d = os.path.abspath(d)
        while d not in seen:
            seen.add(d)
            f = os.path.join(d, PROFILE_REL)
            if os.path.isfile(f):
                return f
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    f = os.path.expanduser(os.path.join('~', '.config', 'wk-legal', 'hwpx', 'profile.json'))
    return f if os.path.isfile(f) else None


def load_profile(path):
    with open(DEFAULT_PROFILE, encoding='utf-8') as fp:
        base = json.load(fp)
    base['_dir'] = HERE
    if not path:
        base['_path'] = None
        return base
    with open(path, encoding='utf-8') as fp:
        local = json.load(fp)
    prof = deep_merge(base, local)
    prof['_dir'] = os.path.dirname(os.path.abspath(path))
    prof['_path'] = path
    if 'templates' in local:          # 양식 목록은 로컬 것으로 통째 교체
        prof['templates'] = local['templates']
    return prof


def pick_template(prof, side):
    t = prof['templates']
    name = t.get(side) if side else None
    name = name or t.get('기본') or next(iter(t.values()))
    path = name if os.path.isabs(name) else os.path.join(prof['_dir'], name)
    if not os.path.isfile(path):
        raise RenderError(f'양식 파일 없음: {path}')
    return path


# ───────────────────────── markdown 해석 ─────────────────────────

def clean_inline(s):
    s = re.sub(r'<!--.*?-->', '', s)
    s = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', s)
    s = re.sub(r'\[([^\]]+)\]\((?:https?://|#)[^)]*\)', r'\1', s)
    s = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|>])', r'\1', s)
    return s


def unwrap(s):
    """제목 줄 장식(#, **, __) 제거."""
    s = re.sub(r'^#{1,6}\s+', '', s.strip())
    m = re.fullmatch(r'(\*\*|__)(.+?)\1', s)
    return (m.group(2) if m else s).strip()


def squeeze(s):
    return re.sub(r'\s+', '', s)


def spaced_title(s):
    """'증 명 방 법'·'다        음'처럼 글자 사이를 띄운 짧은 제목인가."""
    t = s.strip()
    return bool(re.fullmatch(r'[가-힣](?:\s+[가-힣]){1,7}', t))


def outline_level(s):
    for i, rx in enumerate(OUTLINE_RE):
        m = rx.match(s)
        if m:
            return i + 1, m.group(1), s[m.end():]
    return 0, None, s


def party_line(s):
    """'원    고   홍길동' → ('원고', '홍길동'); 라벨에 괄호 지위('(항소인)')를 허용."""
    t = s.strip()
    for lab in PARTY_LABELS:
        rx = r'\s*'.join(map(re.escape, lab)) + r'(\s*\([가-힣·,\s]+\))?\s+(\S.*)'
        m = re.fullmatch(rx, t)
        if m and (re.search(r'\s{2,}', t) or len(lab) >= 2):
            paren = squeeze(m.group(1) or '')
            return lab + paren, m.group(2).strip()
    return None


def parse_md(text, prof):
    """초안 markdown → {title, subtitles, parties, opening, has_da_eum, body[], date, signature[], court, annex[]}"""
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    lines = text.splitlines()
    list_secs = set(prof['lists']['sections'])
    doc = {'title': '', 'subtitles': [], 'parties': [], 'opening': None, 'has_da_eum': False,
           'body': [], 'date': None, 'signature': [], 'court': None, 'annex': [], 'warnings': []}
    i, n = 0, len(lines)
    while i < n and not lines[i].strip():
        i += 1
    if i < n:
        first = unwrap(lines[i])
        if outline_level(first)[0]:
            raise RenderError('첫 줄이 번호 항목 — 서면 전문(표제부터 법원까지)만 변환한다')
        doc['title'] = first; i += 1
    # 머리(당사자 표시). 표제 바로 뒤 작성 메모(> 인용·**[…]** 주석)는 제출본이 아니라는 신호다
    while i < n:
        raw = lines[i]
        if not raw.strip():
            i += 1; continue
        if raw.lstrip().startswith('>'):
            raise RenderError('표제 뒤에 작성 메모(> …) — 메모·검토 논평을 뺀 제출본 md를 만들어 변환한다(hwpx-출력-정책 2.)')
        u = re.sub(r'\*\*|__', '', unwrap(raw))
        p = party_line(u)
        if p:
            doc['parties'].append([p[0], [p[1]]]); i += 1; continue
        if not doc['parties'] and re.match(r'^\s*[—–-]\s', raw):
            doc['subtitles'].append(raw.strip()); i += 1; continue
        if doc['parties'] and not outline_level(u)[0] and not spaced_title(u) and not re.match(r'^(#|\||!\[|-{3,})', u) \
                and not re.search(r'사건에\s*관하여|다음과\s*같이|대리인은|니다\.\s*$|다\.\s*$', u):
            doc['parties'][-1][1].append(u.strip()); i += 1; continue   # 주소·대표자 등 이음 줄
        break
    # 꼬리(날짜·서명·법원·별지) 찾기: 마지막 '…귀중' 줄과 그 앞 날짜 줄
    court_i = None
    for j in range(n - 1, i - 1, -1):
        if re.search(r'귀\s*중\s*(\*\*)?\s*$', lines[j]):
            court_i = j; break
    date_i = None
    if court_i is not None:
        for j in range(court_i - 1, i - 1, -1):
            if DATE_RE.match(squeeze(unwrap(lines[j])).replace('.', '. ').replace('  ', ' ').strip()) or \
               re.fullmatch(r'\d{4}\.\s*\d{0,2}\.?\s*\d{0,2}\.?', unwrap(lines[j])):
                date_i = j; break
    end_body = date_i if date_i is not None else (court_i if court_i is not None else n)
    if date_i is not None:
        doc['date'] = unwrap(lines[date_i])
        doc['signature'] = [unwrap(l) for l in lines[date_i + 1:court_i] if l.strip()]
    if court_i is not None:
        doc['court'] = unwrap(lines[court_i])
        rest = lines[court_i + 1:]
        k = 0
        while k < len(rest) and (not rest[k].strip() or re.fullmatch(r'\s*(-{3,}|\*{3,}|_{3,})\s*', rest[k])):
            k += 1
        doc['annex'] = rest[k:]
        if doc['annex'] and not re.search(r'별\s*지|목\s*록', unwrap(doc['annex'][0])):
            raise RenderError('법원 줄 뒤에 별지가 아닌 내용 — 검토 메모 등은 뺀 제출본 md를 변환한다(별지는 "---" 다음 "[별지] …" 제목으로)')
    body_lines = lines[i:end_body]
    # 모두문·다음
    j = 0
    while j < len(body_lines) and not body_lines[j].strip():
        j += 1
    if j < len(body_lines):
        first = unwrap(body_lines[j])
        if not outline_level(first)[0] and not spaced_title(first) and re.search(r'사건에\s*관하여|다음과\s*같이|대리인은', first):
            doc['opening'] = clean_inline(first.strip()); j += 1
            while j < len(body_lines) and not body_lines[j].strip():
                j += 1
            if j < len(body_lines) and squeeze(unwrap(body_lines[j])) == '다음':
                doc['has_da_eum'] = True; j += 1
    if court_i is None or date_i is None:
        doc['warnings'].append('날짜·법원 줄을 찾지 못함 — 서면 전문인지 확인(양식의 서명·법원 자리는 비운다)')
    doc['body'] = blocks(body_lines[j:], list_secs, doc['warnings'])
    doc['annex_blocks'] = blocks(doc['annex'][1:], list_secs, doc['warnings']) if doc['annex'] else []
    doc['annex_title'] = unwrap(doc['annex'][0]) if doc['annex'] else ''
    return doc


def blocks(lines, list_secs, warnings):
    """본문 줄 → 블록 목록. 블록: (종류, 값)
    종류: outline(level, text) / body(text) / quote(text) / mid(text) / item(text, [이음줄]) / table(rows, aligns) / image(cap, path)"""
    out, mode = [], 'outline'
    k, n = 0, len(lines)
    while k < n:
        raw = lines[k]
        s = raw.strip()
        if not s or re.fullmatch(r'(-{3,}|\*{3,}|_{3,}|```.*)', s):
            k += 1; continue
        if s.startswith('|') and k + 1 < n and re.match(r'^\s*\|?\s*:?-{3,}', lines[k + 1]):
            rows = [split_row(s)]
            aligns = [('C' if c.startswith(':') and c.endswith(':') else 'R' if c.endswith(':')
                       else 'L' if c.startswith(':') else 'J') for c in split_row(lines[k + 1])]
            k += 2
            while k < n and lines[k].strip().startswith('|'):
                rows.append(split_row(lines[k])); k += 1
            width = max(len(r) for r in rows)
            rows = [r + [''] * (width - len(r)) for r in rows]
            aligns = (aligns + ['J'] * width)[:width]
            out.append(('table', (rows, aligns))); continue
        m = re.fullmatch(r'!\[(.*?)\]\((.+?)\)', s)
        if m:
            out.append(('image', (clean_inline(m.group(1)), m.group(2)))); k += 1; continue
        if s.startswith('>'):
            out.append(('quote', clean_inline(re.sub(r'^>\s?', '', s)))); k += 1; continue
        u = unwrap(s)
        if spaced_title(u) or squeeze(u) in list_secs:
            out.append(('mid', u))
            mode = 'list' if squeeze(u) in list_secs else 'outline'
            k += 1; continue
        if mode == 'list' and re.match(r'#{1,6}\s', s):
            mode = 'outline'                     # '#' 제목은 목록 구역을 끝낸다
        if mode == 'list':
            if re.match(r'^\s{3,}\S', raw) and out and out[-1][0] == 'item' and not outline_level(s)[0]:
                out[-1][1][1].append(clean_inline(s))
            else:
                out.append(('item', (clean_inline(re.sub(r'^[-*]\s+', '', s)), [])))
            k += 1; continue
        lv, num, rest = outline_level(re.sub(r'^#{1,6}\s+', '', s))
        if lv:
            out.append(('outline', (lv, num, clean_inline(rest).strip().strip('*').strip()))); k += 1; continue
        if re.match(r'^#{1,6}\s', s):
            warnings.append(f'번호 없는 제목을 굵은 본문으로 넣음: {u[:30]}')
            out.append(('strong', clean_inline(u))); k += 1; continue
        out.append(('body', clean_inline(re.sub(r'^[-*]\s+', '- ', s)))); k += 1
    check_numbering(out, warnings)
    return out


def check_numbering(blks, warnings):
    """번호 계획: 초안 번호가 같은 단계에서 처음(1·가)으로 돌아가면 새 번호열(seg)을 시작한다 — 한글 자동 번호가
    이어지지 않게. 그 밖에 자동 번호와 다른 번호는 경고한다. outline 블록 값에 seg를 덧붙인다."""
    cnt = [0] * 7
    seg = 0
    for idx, (kind, val) in enumerate(blks):
        if kind != 'outline':
            continue
        lv, num, text = val[:3]
        if num in ('1', '가') and cnt[lv] > 0:
            seg += 1
            cnt = [0] * 7
        cnt[lv] += 1
        for d in range(lv + 1, 7):
            cnt[d] = 0
        expect = str(cnt[lv]) if lv in (1, 3, 5) else (HANGUL_SEQ[cnt[lv] - 1] if cnt[lv] <= len(HANGUL_SEQ) else '?')
        if num != expect:
            warnings.append(f'번호 불일치: 초안 "{num}" → 자동 번호 "{expect}" ({text[:20]}) — 본문 안의 항목 참조를 확인')
        blks[idx] = ('outline', (lv, num, text, seg))


def split_row(line):
    return [c.strip() for c in re.split(r'(?<!\\)\|', line.strip().strip('|'))]


# ───────────────────────── header.xml 편집 ─────────────────────────

class Header:
    def __init__(self, xml):
        self.x = xml
        self.cache = {}
        self.style = {}
        for m in re.finditer(r'<hh:style id="(\d+)" type="PARA" name="([^"]+)"[^>]*?paraPrIDRef="(\d+)" charPrIDRef="(\d+)"', xml):
            self.style[m.group(2)] = (m.group(1), m.group(3), m.group(4))

    def _add(self, group, tag, body_fn):
        i = max(int(v) for v in re.findall(rf'<hh:{tag} id="(\d+)"', self.x)) + 1
        self.x = re.sub(rf'<hh:{group} itemCnt="(\d+)"', lambda m: f'<hh:{group} itemCnt="{int(m.group(1)) + 1}"', self.x, 1)
        self.x = self.x.replace(f'</hh:{group}>', body_fn(i) + f'</hh:{group}>', 1)
        return str(i)

    def _clone(self, group, tag, src, fn, key):
        if key in self.cache:
            return self.cache[key]
        s = re.search(rf'<hh:{tag} id="{src}"[ >].*?</hh:{tag}>', self.x, re.S).group(0)
        self.cache[key] = self._add(group, tag, lambda i: fn(re.sub(r'id="\d+"', f'id="{i}"', s, 1)))
        return self.cache[key]

    def char(self, src, bold=False, underline=False, height=None):
        if not (bold or underline or height):
            return src
        def fn(s):
            if height:
                s = re.sub(r'height="\d+"', f'height="{height}"', s, 1)
            if bold and '<hh:bold/>' not in s:
                s = s.replace('<hh:underline', '<hh:bold/><hh:underline', 1)
            if underline:
                s = re.sub(r'<hh:underline type="\w+"', '<hh:underline type="BOTTOM"', s, 1)
            return s
        return self._clone('charProperties', 'charPr', src, fn, ('c', src, bold, underline, height))

    def para(self, src, align=None, line=None, prev=None, next_=None, keep=None, left=None, heading=None):
        if all(v is None for v in (align, line, prev, next_, keep, left, heading)):
            return src
        def fn(s):
            if align:
                s = re.sub(r'horizontal="\w+"', f'horizontal="{align}"', s, 1)
            if line:
                s = re.sub(r'(<hh:lineSpacing type=")\w+(" value=")\d+', rf'\g<1>PERCENT\g<2>{line}', s)
            if prev is not None:
                s = re.sub(r'<hc:prev value="-?\d+"', f'<hc:prev value="{prev}"', s)
            if next_ is not None:
                s = re.sub(r'<hc:next value="-?\d+"', f'<hc:next value="{next_}"', s)
            if left is not None:
                s = re.sub(r'<hc:left value="-?\d+"', f'<hc:left value="{left}"', s)
            if keep is not None:
                s = re.sub(r'keepWithNext="\d"', f'keepWithNext="{int(keep)}"', s, 1)
            if heading:
                s = re.sub(r'<hh:heading [^>]*/>', f'<hh:heading type="{heading[0]}" idRef="{heading[1]}" level="{heading[2]}"/>', s, 1)
            return s
        return self._clone('paraProperties', 'paraPr', src, fn, ('p', src, align, line, prev, next_, keep, left, heading))

    def border(self, l, r, t, b):
        key = ('b', l, r, t, b)
        if key in self.cache:
            return self.cache[key]
        def fn(i):
            sides = ''.join(f'<hh:{nm}Border type="SOLID" width="{w}" color="#000000"/>'
                            for nm, w in (('left', l), ('right', r), ('top', t), ('bottom', b)))
            return (f'<hh:borderFill id="{i}" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">'
                    '<hh:slash type="NONE" Crooked="0" isCounter="0"/><hh:backSlash type="NONE" Crooked="0" isCounter="0"/>'
                    f'{sides}<hh:diagonal type="SOLID" width="0.1 mm" color="#000000"/></hh:borderFill>')
        self.cache[key] = self._add('borderFills', 'borderFill', fn)
        return self.cache[key]

    def numbering_copy(self, key, src='1'):
        """번호를 1부터 다시 세는 번호열용 문단 번호 정의 복제(key마다 하나)."""
        return self._clone('numberings', 'numbering', src, lambda s: s, ('nclone', key))


# ───────────────────────── 렌더러 ─────────────────────────

def run(cid, text):
    return f'<hp:run charPrIDRef="{cid}"><hp:t>{escape(text)}</hp:t></hp:run>'


def para(pid, sid, runs, page_break=False):
    return (f'<hp:p id="0" paraPrIDRef="{pid}" styleIDRef="{sid}" pageBreak="{int(page_break)}" '
            f'columnBreak="0" merged="0">{runs}</hp:p>')


def disp_w(t, height):
    return sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in t) * height


def img_size(data):
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return struct.unpack('>II', data[16:24])
    if data[:2] == b'\xff\xd8':
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1; continue
            if data[i + 1] in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack('>HH', data[i + 5:i + 9]); return w, h
            i += 2 + struct.unpack('>H', data[i + 2:i + 4])[0]
    raise RenderError('그림은 PNG·JPEG만 지원')


def top_paras(sec):
    out, depth, start = [], 0, 0
    for m in re.finditer(r'<hp:p |</hp:p>', sec):
        if m.group(0) == '<hp:p ':
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                out.append((start, m.end()))
    return out


def marker_of(p):
    q = re.sub(r'<hp:subList.*?</hp:subList>', '', p, flags=re.S)
    t = ''.join(re.findall(r'<hp:t>([^<]*)', q)).strip()
    m = re.fullmatch(r'\{\{(\S+?)\}\}', t)
    return m.group(1) if m else None


def outer_elems(s, tag):
    """s 안의 최상위 <tag …>…</tag> 요소들(같은 태그의 중첩을 센다)."""
    out, depth, start = [], 0, 0
    for m in re.finditer(rf'<{tag}(?=[ >/])[^>]*?/>|<{tag}(?=[ >])[^>]*>|</{tag}>', s):
        tok = m.group(0)
        if tok.endswith('/>') and not tok.startswith('</'):
            if depth == 0:
                out.append(tok)
            continue
        if tok.startswith('</'):
            depth -= 1
            if depth == 0:
                out.append(s[start:m.end()])
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return out


def top_runs(p):
    """문단의 최상위 run 목록(꼬리말 등 하위 목록 안의 run은 그 run 안에 그대로 둔다)."""
    body = p[re.match(r'<hp:p [^>]*>', p).end():p.rfind('</hp:p>')]
    out, depth, start = [], 0, 0
    for m in re.finditer(r'<hp:run [^>]*?/>|<hp:run [^>]*>|</hp:run>', body):
        tok = m.group(0)
        if tok.endswith('/>'):
            if depth == 0:
                out.append(tok)
            continue
        if tok.startswith('<hp:run'):
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                out.append(body[start:m.end()])
    return out


def set_text(p, runs_xml):
    """표지 문단의 글 run만 바꾸고 secPr·ctrl(머리말·꼬리말 등) run은 남긴다(그 run 안 최상위 글만 지운다)."""
    head = re.match(r'<hp:p [^>]*>', p).group(0)
    keep = []
    for r in top_runs(p):
        if '<hp:secPr' not in r and '<hp:ctrl>' not in r:
            continue
        # 최상위 글(<hp:t>)만 지운다 — 꼬리말 안 문단의 글(쪽 번호 ' / ')은 하위 목록에 있으므로 보존
        inner = re.split(r'(<hp:subList.*?</hp:subList>)', r, flags=re.S)
        r = ''.join(seg if seg.startswith('<hp:subList') else re.sub(r'<hp:t>.*?</hp:t>|<hp:t/>', '', seg, flags=re.S)
                    for seg in inner)
        keep.append(r)
    return head + ''.join(keep) + runs_xml + '</hp:p>'


def first_char(p):
    q = re.sub(r'<hp:subList.*?</hp:subList>', '', p, flags=re.S)
    cs = [c for r, c in re.findall(r'(<hp:run charPrIDRef="(\d+)"[^>]*>(?:(?!</hp:run>).)*?<hp:t>)', q, re.S)]
    if cs:
        return cs[0]
    return re.search(r'charPrIDRef="(\d+)"', q).group(1)


class Renderer:
    def __init__(self, tpl_path, prof, md_dir):
        self.z = zipfile.ZipFile(tpl_path)
        self.files = {nm: self.z.read(nm) for nm in self.z.namelist()}
        self.h = Header(self.files['Contents/header.xml'].decode('utf-8'))
        self.sec = self.files['Contents/section0.xml'].decode('utf-8')
        self.prof = prof
        self.md_dir = md_dir
        self.bins = []
        self.nbin = len([f for f in self.files if f.startswith('BinData/')])
        st = prof['styles']
        S = self.h.style
        def need(name):
            if name not in S:
                raise RenderError(f'양식에 스타일 "{name}"이 없음 — 프로필 styles로 연결하거나 양식에 추가')
            return S[name]
        self.body = need(st['본문'])
        self.base = need(st['바탕글'])
        self.heads = []
        for lv, name in enumerate(st['개요'], 1):
            self.heads.append(S.get(name))
        if not self.heads[0]:
            raise RenderError(f'양식에 스타일 "{st["개요"][0]}"이 없음')
        self.table_style = S.get(st['표']) or self.base
        self.mid = S.get(st['중간제목'])
        self.title_style = S.get(st['문서제목'])
        bp = prof['body']
        self.body_c = self.body[2]
        self.emph_c = self.h.char(self.body_c, bold='bold' in bp['emphasis'], underline='underline' in bp['emphasis'])
        self.strong_c = self.h.char(self.body_c, bold=True)
        keep = prof['heading']['keep_with_next']
        self.head_fmt = []
        for lv in range(6):
            sty = self.heads[lv] or self.heads[0]
            pid = sty[1]
            if not self.heads[lv]:     # 개요 n 스타일이 없으면 개요 1을 그 수준으로 복제
                pid = self.h.para(pid, heading=('OUTLINE', '0', str(lv)))
            self.head_fmt.append((sty[0], self.h.para(pid, keep=True) if keep else pid, sty[2]))
        self.holder_p = self.h.para(self.body[1], 'CENTER')
        self.quote_p = self.h.para(self.body[1], left=bp['quote_indent'])
        lp = prof['lists']
        self.list_p = self.h.para(self.base[1], 'JUSTIFY', line=lp['line_spacing'] or None, prev=0, next_=0)
        mid = self.mid or (self.body[0], self.h.para(self.body[1], 'CENTER'), self.strong_c)
        self.mid_fmt = (mid[0], self.h.para(mid[1], keep=True) if keep else mid[1], mid[2])

    # ── 블록 ──
    def inline(self, text, base_c):
        parts = re.split(r'\*\*(.+?)\*\*', text)
        return ''.join(run(self.emph_c if i % 2 else base_c, re.sub(r'(?<!\*)\*(?!\*)', '', p))
                       for i, p in enumerate(parts) if p)

    def lead(self, text, n):
        return ' ' * n + text.lstrip()

    def seg_fmt(self, key):
        """번호열 key의 개요 모양 — 본문 첫 번호열은 양식의 개요(OUTLINE), 나머지는 번호 정의를 복제한 NUMBER."""
        if key == ('body', 0):
            return self.head_fmt
        if key not in self.h.cache:
            nid = self.h.numbering_copy(key)
            self.h.cache[key] = [(sid, self.h.para(pid, heading=('NUMBER', nid, str(lv))), cid)
                                 for lv, (sid, pid, cid) in enumerate(self.head_fmt)]
        return self.h.cache[key]

    def render_blocks(self, blks, area='body'):
        out = []
        bp = self.prof['body']
        for kind, val in blks:
            if kind == 'outline':
                lv, _, text, seg = val
                sid, pid, cid = self.seg_fmt((area, seg))[lv - 1]
                out.append(para(pid, sid, self.inline(text.replace('**', ''), cid)))
            elif kind == 'body':
                out.append(para(self.body[1], self.body[0], self.inline(self.lead(val, bp['lead_spaces']), self.body_c)))
            elif kind == 'strong':
                pid = self.h.para(self.body[1], keep=True) if self.prof['heading']['keep_with_next'] else self.body[1]
                out.append(para(pid, self.body[0], run(self.strong_c, val.replace('**', ''))))
            elif kind == 'quote':
                out.append(para(self.quote_p, self.body[0], self.inline(val, self.body_c)))
            elif kind == 'mid':
                sid, pid, cid = self.mid_fmt
                out.append(para(pid, sid, run(cid, val)))
            elif kind == 'item':
                text, conts = val
                lead = ' ' * self.prof['lists']['lead_spaces']
                t = escape(lead + text) + ''.join('<hp:lineBreak/>' + escape(lead + c) for c in conts)
                out.append(para(self.list_p, self.base[0], f'<hp:run charPrIDRef="{self.body_c}"><hp:t>{t}</hp:t></hp:run>'))
            elif kind == 'table':
                out.append(para(self.holder_p, self.body[0],
                                f'<hp:run charPrIDRef="{self.body_c}">{self.table_xml(*val)}<hp:t/></hp:run>'))
            elif kind == 'image':
                cap, path = val
                pic, cap_para = self.pic_xml(cap, path)
                cp = self.prof['picture']
                block = para(self.holder_p, self.body[0], f'<hp:run charPrIDRef="{self.body_c}">{pic}<hp:t/></hp:run>')
                if cap_para and cp['caption_side'] == 'BOTTOM':
                    out += [block, cap_para]
                elif cap_para:
                    out += [cap_para, block]
                else:
                    out.append(block)
        return out

    def table_xml(self, rows, aligns):
        tp = self.prof['table']
        ncol, nrow = len(rows[0]), len(rows)
        size = next(pt for mx, pt in tp['font_pt_by_cols'] if ncol <= mx) * 100
        tsid, tpara, tchar = self.table_style
        c_norm = self.h.char(tchar, height=size if size != 1200 else None)
        c_bold = self.h.char(tchar, bold=True, height=size if size != 1200 else None)
        palign = {'C': self.h.para(tpara, 'CENTER'), 'R': self.h.para(tpara, 'RIGHT'),
                  'L': self.h.para(tpara, 'LEFT'), 'J': self.h.para(tpara, 'JUSTIFY')}
        cm, out_m, in_m = tp['cell_margin'], tp['out_margin'], tp['in_margin']
        cap = self.text_w - 2 * out_m
        nat = [max(disp_w(r[c], size) for r in rows) * 1.15 + 2 * cm + 200 for c in range(ncol)]
        if sum(nat) <= cap:
            w = [int(x) for x in nat]
        else:
            fixed = {}
            while True:
                free = [c for c in range(ncol) if c not in fixed]
                share = (cap - sum(fixed.values())) / len(free)
                small = [c for c in free if nat[c] <= share]
                if not small or len(small) == len(free):
                    break
                for c in small:
                    fixed[c] = nat[c]
            w = [int(fixed.get(c, share)) for c in range(ncol)]
            w[-1] += cap - sum(w)
        total = nrow > 2 and re.fullmatch(r'(합\s*계|총\s*계|소\s*계|계)', rows[-1][0].strip())
        O, I, HR, TR = tp['outer'], tp['inner'], tp['header_rule'], tp['total_rule']
        trs = []
        for ri, r in enumerate(rows):
            tcs = []
            for ci, t in enumerate(r):
                top = O if ri == 0 else HR if ri == 1 else TR if (total and ri == nrow - 1) else I
                bot = O if ri == nrow - 1 else HR if ri == 0 else TR if (total and ri == nrow - 2) else I
                bf = self.h.border(O if ci == 0 else I, O if ci == ncol - 1 else I, top, bot)
                head = ri == 0
                al = 'C' if head else aligns[ci]
                bold = (head and tp['header_bold']) or (total and ri == nrow - 1 and ci > 0 and t and tp['total_bold_values'])
                ch = c_bold if bold else c_norm
                cell = clean_inline(t).replace('**', '')
                tcs.append(
                    f'<hp:tc name="" header="0" hasMargin="1" protect="0" editable="0" dirty="0" borderFillIDRef="{bf}">'
                    '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" '
                    'linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                    f'{para(palign[al], tsid, run(ch, cell))}</hp:subList>'
                    f'<hp:cellAddr colAddr="{ci}" rowAddr="{ri}"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
                    f'<hp:cellSz width="{w[ci]}" height="5"/>'
                    f'<hp:cellMargin left="{cm}" right="{cm}" top="{cm}" bottom="{cm}"/></hp:tc>')
            trs.append('<hp:tr>' + ''.join(tcs) + '</hp:tr>')
        return (f'<hp:tbl id="{next(_ids)}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
                f'lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" rowCnt="{nrow}" colCnt="{ncol}" '
                f'cellSpacing="0" borderFillIDRef="{self.h.border(I, I, I, I)}" noAdjust="0">'
                f'<hp:sz width="{sum(w)}" widthRelTo="ABSOLUTE" height="{5 * nrow}" heightRelTo="ABSOLUTE" protect="0"/>'
                '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
                'vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
                f'<hp:outMargin left="{out_m}" right="{out_m}" top="{out_m}" bottom="{out_m}"/>'
                f'<hp:inMargin left="{in_m}" right="{in_m}" top="{in_m}" bottom="{in_m}"/>' + ''.join(trs) + '</hp:tbl>')

    def pic_xml(self, caption, path):
        cp = self.prof['picture']
        full = path if os.path.isabs(path) else os.path.join(self.md_dir, path)
        if not os.path.isfile(full):
            raise RenderError(f'그림 파일 없음: {full}')
        with open(full, 'rb') as fp:
            data = fp.read()
        pw, ph = img_size(data)
        ext = 'png' if data[:4] == b'\x89PNG' else 'jpg'
        self.nbin += 1
        bid = f'image{self.nbin}'
        while f'BinData/{bid}.{ext}' in self.files or any(b[0] == bid for b in self.bins):
            self.nbin += 1; bid = f'image{self.nbin}'
        self.bins.append((bid, f'BinData/{bid}.{ext}', data, 'image/png' if ext == 'png' else 'image/jpg'))
        ow, oh = pw * PX, ph * PX
        cw = min(ow, int(self.text_w * cp['max_width_ratio'])); ch = int(oh * cw / ow)
        if ch > self.text_h * cp['max_height_ratio']:
            ch = int(self.text_h * cp['max_height_ratio']); cw = int(ow * ch / oh)
        sx, sy = cw / ow, ch / oh
        border = ''
        if cp['border_hwpunit']:
            border = (f'<hp:lineShape color="#000000" width="{cp["border_hwpunit"]}" style="SOLID" endCap="ROUND" '
                      'headStyle="NORMAL" tailStyle="NORMAL" headfill="0" tailfill="0" headSz="SMALL_SMALL" '
                      'tailSz="SMALL_SMALL" outlineStyle="OUTER" alpha="0"/>')
        cap_inner, cap_para = '', None
        if caption:
            text = cp['caption_format'].replace('{}', caption.strip('【】[] '))
            tsid, tpara, _ = self.table_style
            cap_par = para(self.h.para(tpara, cp['caption_align']), tsid, run(self.body_c, text))
            if cp['caption_native']:
                cap_inner = (f'<hp:caption side="{cp["caption_side"]}" fullSz="0" width="8504" gap="850" lastWidth="{cw}">'
                             '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="TOP" linkListIDRef="0" '
                             'linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                             f'{cap_par}</hp:subList></hp:caption>')
            else:
                cap_para = para(self.h.para(self.body[1], cp['caption_align'].replace('JUSTIFY', 'CENTER')),
                                self.body[0], run(self.body_c, text))
        pic = (f'<hp:pic id="{next(_ids)}" zOrder="1" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
               f'lock="0" dropcapstyle="None" href="" groupLevel="0" instid="{next(_ids)}" reverse="0">'
               '<hp:offset x="0" y="0"/>'
               f'<hp:orgSz width="{ow}" height="{oh}"/><hp:curSz width="{cw}" height="{ch}"/>'
               '<hp:flip horizontal="0" vertical="0"/>'
               f'<hp:rotationInfo angle="0" centerX="{cw // 2}" centerY="{ch // 2}" rotateimage="1"/>'
               '<hp:renderingInfo><hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>'
               f'<hc:scaMatrix e1="{sx:.6f}" e2="0" e3="0" e4="0" e5="{sy:.6f}" e6="0"/>'
               '<hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/></hp:renderingInfo>'
               f'<hc:img binaryItemIDRef="{bid}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>{border}'
               f'<hp:imgRect><hc:pt0 x="0" y="0"/><hc:pt1 x="{ow}" y="0"/><hc:pt2 x="{ow}" y="{oh}"/><hc:pt3 x="0" y="{oh}"/></hp:imgRect>'
               f'<hp:imgClip left="0" right="{ow}" top="0" bottom="{oh}"/>'
               '<hp:inMargin left="0" right="0" top="0" bottom="0"/>'
               f'<hp:imgDim dimwidth="{ow}" dimheight="{oh}"/><hp:effects/>'
               f'<hp:sz width="{cw}" widthRelTo="ABSOLUTE" height="{ch}" heightRelTo="ABSOLUTE" protect="0"/>'
               '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" '
               'vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
               '<hp:outMargin left="0" right="0" top="0" bottom="0"/>'
               f'<hp:shapeComment>그림입니다.</hp:shapeComment>{cap_inner}</hp:pic>')
        return pic, cap_para

    # ── 머리·꼬리 ──
    def party_runs(self, proto, label, values):
        tabs = re.findall(r'<hp:tab [^>]*/>', proto)
        lab = label
        if len(tabs) >= 3 and len(lab) == 2:
            t = escape(lab[0]) + tabs[0] + tabs[1] + escape(lab[1]) + tabs[2]
        elif len(tabs) >= 3 and len(lab) == 3:
            t = escape(lab[0]) + tabs[0] + escape(lab[1]) + tabs[1] + escape(lab[2]) + tabs[2]
        elif tabs:
            t = escape(lab) + tabs[-1]
        else:
            t = escape(lab) + '  '
        t += '<hp:lineBreak/>'.join(escape(v) for v in values)
        return f'<hp:run charPrIDRef="{first_char(proto)}"><hp:t>{t}</hp:t></hp:run>'

    def signer_lines(self, sig, side_label):
        """초안 서명 블록 → [(역할, 글)]. 자리표시(○○)는 프로필 서명자로 채운다."""
        sp = self.prof['signers']
        out, lawyers_md = [], []
        agent = firm = None
        for line in sig:
            t = line.strip()
            if re.match(r'^(담당)?변호사\s', t):
                lawyers_md.append(re.sub(r'^(담당)?변호사\s+', '', t).strip())
            elif re.search(r'법무법인|법률사무소|합동법률|법무조합', t):
                firm = t
            elif agent is None:
                agent = t
            else:
                out.append(('대리인', t))
        if firm and '○' in firm and sp.get('firm'):
            firm = sp['firm']
        if not lawyers_md or all('○' in x for x in lawyers_md):
            lawyers = sp.get('lawyers') or lawyers_md
        else:
            lawyers = lawyers_md
        res = []
        if agent:
            res.append(('대리인', agent))
        res += out
        if firm:
            res.append(('법인', firm))
        for nm in lawyers:
            plain = nm.replace(' ', '')
            spaced = ' '.join(plain) if len(plain) <= 4 and '○' not in plain else nm
            res.append(('변호사', sp['lawyer_line'].replace('{name_spaced}', spaced).replace('{name}', nm)))
        return res

    def build(self, doc):
        ps = top_paras(self.sec)
        paras = [self.sec[a:b] for a, b in ps]
        marks = {}
        for idx, p in enumerate(paras):
            mk = marker_of(p)
            if mk:
                marks.setdefault(mk, idx)
        if '본문' not in marks:
            raise RenderError('양식에 {{본문}} 표지가 없음 — `render.py mark`로 표지본을 만드세요')
        secpr = re.search(r'<hp:pagePr [^>]*width="(\d+)" height="(\d+)"[^>]*>\s*<hp:margin [^>]*left="(\d+)" right="(\d+)" top="(\d+)" bottom="(\d+)"', self.sec)
        W, Hh, L, R, T, B = map(int, secpr.groups())
        self.text_w, self.text_h = W - L - R, Hh - T - B
        keep_sig = self.prof['signature']['keep_together']
        moved_ctrl = []
        out = []
        for idx, p in enumerate(paras):
            mk = marker_of(p)
            if mk is None:
                if idx > marks.get('날짜', 10 ** 9) and keep_sig and idx < marks.get('법원', -1):
                    pid = re.search(r'paraPrIDRef="(\d+)"', p).group(1)
                    p = p.replace(f'paraPrIDRef="{pid}"', f'paraPrIDRef="{self.h.para(pid, keep=True)}"', 1)
                out.append(p); continue
            fill = None
            head = re.match(r'<hp:p [^>]*>', p).group(0)
            pid = re.search(r'paraPrIDRef="(\d+)"', head).group(1)
            cid = first_char(p)
            if mk == '표제':
                fill = [set_text(p, run(cid, normalize_title(doc['title'])))]
            elif mk == '당사자':
                fill = [set_text(p, self.party_runs(p, lab, vals)) for lab, vals in doc['parties']]
                if doc['subtitles']:
                    fill = [para(self.h.para(self.base[1], 'CENTER'), self.base[0], run(self.body_c, s))
                            for s in doc['subtitles']] + fill
            elif mk == '모두문':
                if doc['opening']:
                    fill = [set_text(p, self.inline(self.lead(doc['opening'], self.prof['body']['lead_spaces']), cid))]
            elif mk == '다음':
                if doc['has_da_eum']:
                    fill = [re.sub(r'\{\{다음\}\}', '다\t음', p).replace('\t', '<hp:tab width="5700" leader="0" type="1"/>')]
            elif mk == '본문':
                fill = self.render_blocks(doc['body'])
            elif mk == '날짜':
                if doc['date']:
                    q = set_text(p, run(cid, doc['date']))
                    fill = [q.replace(f'paraPrIDRef="{pid}"', f'paraPrIDRef="{self.h.para(pid, keep=True)}"', 1) if keep_sig else q]
            elif mk in ('대리인', '법인', '변호사'):
                if mk == '대리인':
                    fill = []
                    protos = {m2: paras[marks[m2]] for m2 in ('대리인', '법인', '변호사') if m2 in marks}
                    for role, text in self.signer_lines(doc['signature'], None):
                        pr = protos.get(role) or protos.get('대리인') or p
                        ppid = re.search(r'paraPrIDRef="(\d+)"', pr).group(1)
                        q = set_text(pr, run(first_char(pr), text))
                        if keep_sig:
                            q = q.replace(f'paraPrIDRef="{ppid}"', f'paraPrIDRef="{self.h.para(ppid, keep=True)}"', 1)
                        fill.append(q)
                else:
                    fill = []            # 대리인 표지 자리에서 한꺼번에 채움
            elif mk == '법원':
                if doc['court']:
                    fill = [set_text(p, run(cid, doc['court']))]
            else:
                fill = None
            if fill is None or (fill == [] and mk not in ('법인', '변호사')):
                for r in top_runs(p):
                    moved_ctrl += [c for c in outer_elems(r, 'hp:ctrl') if re.match(r'<hp:ctrl>\s*<hp:(?:header|footer) ', c)]
                continue
            out += fill
        if doc.get('annex_blocks') or doc.get('annex_title'):
            out += self.render_annex(doc)
        if moved_ctrl:              # 지운 표지 문단의 머리말·꼬리말은 첫 문단으로 옮긴다
            first = out[0]
            cid = first_char(first)
            ins = f'<hp:run charPrIDRef="{cid}">' + ''.join(moved_ctrl) + '</hp:run>'
            pos = re.match(r'<hp:p [^>]*>', first).end()
            runs = top_runs(first)
            if runs and '<hp:secPr' in runs[0]:
                pos = first.index(runs[0]) + len(runs[0])
            out[0] = first[:pos] + ins + first[pos:]
        out = [re.sub(r'<hp:linesegarray>.*?</hp:linesegarray>', '', p, flags=re.S) for p in out]
        self.sec = self.sec[:ps[0][0]] + ''.join(out) + self.sec[ps[-1][1]:]
        return self

    def render_annex(self, doc):
        """별지: 쪽을 나누고 번호를 1부터 다시 센다."""
        out = []
        if doc['annex_title']:
            sid, pid, cid = self.title_style or (self.body[0], self.h.para(self.body[1], 'CENTER'), self.strong_c)
            out.append(para(pid, sid, run(cid, normalize_title(doc['annex_title'])), page_break=True))
        body = self.render_blocks(doc['annex_blocks'], area='annex')
        if not out and body:
            body[0] = body[0].replace('pageBreak="0"', 'pageBreak="1"', 1)
        return out + body

    def save(self, out_path):
        self.files['Contents/section0.xml'] = self.sec.encode('utf-8')
        self.files['Contents/header.xml'] = self.h.x.encode('utf-8')
        hpf = self.files['Contents/content.hpf'].decode('utf-8')
        items = ''.join(f'<opf:item id="{b}" href="{p}" media-type="{mt}" isEmbeded="1"/>' for b, p, _, mt in self.bins)
        hpf = re.sub(r'(<opf:manifest>)', r'\1' + items.replace('\\', '\\\\'), hpf, 1)
        self.files['Contents/content.hpf'] = hpf.encode('utf-8')
        prv = re.sub(r'<hp:subList.*?</hp:subList>', '', self.sec, flags=re.S).replace('</hp:p>', '\n')
        self.files['Preview/PrvText.txt'] = re.sub(r'<[^>]+>', '', prv)[:1000].encode('utf-8')
        order = [i.filename for i in self.z.infolist()]
        pos = order.index('Contents/header.xml') if 'Contents/header.xml' in order else len(order)
        for b, p, data, _ in self.bins:
            self.files[p] = data
            order.insert(pos, p)
        with zipfile.ZipFile(out_path, 'w') as o:
            for nm in order:
                o.writestr(zipfile.ZipInfo(nm, (1980, 1, 1, 0, 0, 0)), self.files[nm],
                           compress_type=zipfile.ZIP_STORED if nm == 'mimetype' else zipfile.ZIP_DEFLATED)


def normalize_title(t):
    t = re.sub(r'\*\*|__', '', t).strip()
    return re.sub(r'(?<=[가-힣])\s+(?=[가-힣])', '', t)


def detect_side(doc, prof):
    texts = [doc.get('opening') or ''] + doc.get('signature', [])
    for t in texts:
        m = re.search(r'([가-힣]+?)(?:들)?(?:\s*[가-힣]+)?의?\s*(?:소송)?대리인', t)
        if not m:
            continue
        who = squeeze(t[:m.end()])
        for side, labels in prof['side_map'].items():
            for lab in sorted(labels, key=len, reverse=True):
                if re.match(rf'^(위\S*에관하여)?{lab}(들)?(\S*)?의?(소송)?대리인', re.sub(r'^위사건에관하여', '', who)):
                    return side
    return None


# ───────────────────────── 검사 ─────────────────────────

def check(path, md_text=None, prof=None):
    """구조 검사 + (초안이 있으면) 글자 누락 검사. 문제 목록을 돌려준다."""
    probs = []
    z = zipfile.ZipFile(path)
    infos = z.infolist()
    if not infos or infos[0].filename != 'mimetype' or infos[0].compress_type != zipfile.ZIP_STORED:
        probs.append('mimetype이 첫 항목·무압축이 아님')
    names = set(z.namelist())
    for nm in names:
        if nm.endswith('.xml') or nm.endswith('.hpf'):
            raw = z.read(nm)
            if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:      # 한글 문서에는 DTD가 없다 — 엔티티 확장 공격 차단
                probs.append(f'DTD·엔티티 선언 포함(거부): {nm}')
                continue
            try:
                ET.fromstring(raw)
            except ET.ParseError as e:
                probs.append(f'XML 오류 {nm}: {e}')
    H = z.read('Contents/header.xml').decode('utf-8')
    for grp, tag in (('borderFills', 'borderFill'), ('charProperties', 'charPr'), ('paraProperties', 'paraPr'),
                     ('styles', 'style'), ('numberings', 'numbering')):
        m = re.search(rf'<hh:{grp} itemCnt="(\d+)"', H)
        if m:
            ids = re.findall(rf'<hh:{tag} id="(\d+)"', H)
            if int(m.group(1)) != len(ids):
                probs.append(f'{grp} itemCnt {m.group(1)} ≠ 실제 {len(ids)}')
            if len(set(ids)) != len(ids):
                probs.append(f'{grp} id 중복')
    secs = [nm for nm in names if re.match(r'Contents/section\d+\.xml', nm)]
    X = ''.join(z.read(nm).decode('utf-8') for nm in secs)
    have = {t: set(re.findall(rf'<hh:{t} id="(\d+)"', H)) for t in ('paraPr', 'charPr', 'style', 'borderFill')}
    for attr, t in (('paraPrIDRef', 'paraPr'), ('charPrIDRef', 'charPr'), ('styleIDRef', 'style'), ('borderFillIDRef', 'borderFill')):
        miss = set(re.findall(rf'{attr}="(\d+)"', X)) - have[t]
        if attr == 'charPrIDRef':
            miss -= {'4294967295'}
        if miss:
            probs.append(f'{attr} 미정의: {sorted(miss)[:5]}')
    hpf = z.read('Contents/content.hpf').decode('utf-8')
    listed = set(re.findall(r'href="(BinData/[^"]+)"', hpf))
    actual = {nm for nm in names if nm.startswith('BinData/')}
    if listed != actual:
        probs.append(f'BinData 등재 불일치: 목록만 {sorted(listed - actual)} / 파일만 {sorted(actual - listed)}')
    refs = set(re.findall(r'binaryItemIDRef="([^"]+)"', X + H))
    ids = set(re.findall(r'<opf:item id="([^"]+)" href="BinData/', hpf))
    if refs - ids:
        probs.append(f'그림 참조 미등재: {sorted(refs - ids)}')
    if re.search(r'\{\{\S+?\}\}', ''.join(re.findall(r'<hp:t>([^<]*)', X))):
        probs.append('표지({{…}})가 남아 있음')
    if md_text is not None:
        got = Counter(c for c in ''.join(re.findall(r'<hp:t>((?:[^<]|<hp:(?:tab|lineBreak)[^>]*/>)*)', X)) if c.isalnum())
        want = Counter(c for c in md_content_text(md_text, prof) if c.isalnum())
        lost = want - got
        if lost:
            probs.append('초안 글자 누락: ' + ''.join(sorted(lost.elements()))[:60])
    return probs


def md_content_text(md, prof):
    """누락 검사용: 초안에서 장식·자동 번호·표 구분선을 뺀 글."""
    md = re.sub(r'<!--.*?-->', '', md, flags=re.S)
    out = []
    list_mode = False
    secs = set((prof or {}).get('lists', {}).get('sections', []))
    for line in md.splitlines():
        s = line.strip()
        if re.fullmatch(r'(-{3,}|\*{3,}|_{3,})', s) or re.match(r'#{1,6}\s', s):
            list_mode = False                      # 구분선·# 제목에서 목록 구역이 끝난다(별지 등)
        if re.fullmatch(r'\|?\s*:?-{3,}.*', s) or re.fullmatch(r'(-{3,}|\*{3,}|_{3,})', s):
            continue
        u = unwrap(s)
        if spaced_title(u) or squeeze(u) in secs:
            list_mode = squeeze(u) in secs
            out.append(u); continue
        s = re.sub(r'^#{1,6}\s+|^>\s?', '', s)
        if not list_mode:
            s = outline_level(s)[2]
        s = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', s)
        out.append(clean_inline(s).replace('**', '').replace('|', ''))
    return ''.join(out)


# ───────────────────────── 표지(mark) ─────────────────────────

def mark_template(src, dst, no_background=False):
    """한글 양식에 표지를 넣는다 — 스타일 이름과 문단 모양으로 역할을 추정한다. 결과를 한글에서 열어 확인할 것."""
    z = zipfile.ZipFile(src)
    files = {nm: z.read(nm) for nm in z.namelist()}
    H = files['Contents/header.xml'].decode('utf-8')
    X = files['Contents/section0.xml'].decode('utf-8')
    style = {m.group(1): m.group(2) for m in re.finditer(r'<hh:style id="(\d+)" type="PARA" name="([^"]+)"', H)}
    def align(pid):
        m = re.search(rf'<hh:paraPr id="{pid}"[ >].*?horizontal="(\w+)"', H, re.S)
        return m.group(1) if m else ''
    def intent(pid):
        m = re.search(rf'<hh:paraPr id="{pid}"[ >].*?<hc:intent value="(-?\d+)"', H, re.S)
        return int(m.group(1)) if m else 0
    ps = top_paras(X)
    paras = [X[a:b] for a, b in ps]
    out, found = [], []
    body_done = False
    after_date = False
    lawyer_done = False
    party_done = False
    for p in paras:
        sid = re.search(r'styleIDRef="(\d+)"', p).group(1)
        pid = re.search(r'paraPrIDRef="(\d+)"', p).group(1)
        sname = style.get(sid, '')
        q = re.sub(r'<hp:subList.*?</hp:subList>', '', p, flags=re.S)
        text = ''.join(re.findall(r'<hp:t>((?:[^<]|<hp:tab[^>]*/>)*)', q))
        plain = re.sub(r'<hp:tab[^>]*/>', '', text).strip()
        cid = first_char(p)
        def put(mk):
            found.append(mk)
            return set_text(p, run(cid, '{{' + mk + '}}'))
        if sname == '문서제목' and '표제' not in found:
            out.append(put('표제')); continue
        if '<hp:tab' in text and intent(pid) < 0 and not body_done:
            if not party_done:          # 탭 배분(라벨 글자 사이·값 위치)을 엔진이 쓰도록 탭 요소를 표지 뒤에 남긴다
                tabs = ''.join(re.findall(r'<hp:tab [^>]*/>', text))
                found.append('당사자')
                out.append(set_text(p, f'<hp:run charPrIDRef="{cid}"><hp:t>{{{{당사자}}}}{tabs}</hp:t></hp:run>'))
                party_done = True
            continue
        if sname == '중간제목' and squeeze(plain) == '다음':
            out.append(put('다음')); continue
        if (sname == '본문' or '사건에 관하여' in plain) and '모두문' not in found and not body_done:
            out.append(put('모두문')); continue
        if sname.startswith('개요') and not after_date:
            if not body_done:
                out.append(put('본문')); body_done = True
            continue
        if re.fullmatch(r'\d{4}\.\s*\.?\s*\.?\s*\.?', plain) or re.fullmatch(r'\d{4}\.\s*\d{0,2}\.\s*\d{0,2}\.', plain):
            out.append(put('날짜')); after_date = True; continue
        if after_date and plain:
            if sname == '법원' or plain.endswith('귀중'):
                out.append(put('법원')); continue
            if re.match(r'^(담당)?변호사', plain):
                if not lawyer_done:
                    out.append(put('변호사')); lawyer_done = True
                continue
            if re.search(r'법무법인|법률사무소|합동법률|법무조합', plain):
                out.append(put('법인')); continue
            if '대리인' in plain:
                out.append(put('대리인')); continue
        out.append(p)
    X2 = X[:ps[0][0]] + ''.join(out) + X[ps[-1][1]:]
    if no_background:
        X2 = re.sub(r'<hp:pageBorderFill type="BOTH" borderFillIDRef="\d+"', '<hp:pageBorderFill type="BOTH" borderFillIDRef="1"', X2)
        H = re.sub(r'<hc:fillBrush><hc:imgBrush.*?</hc:fillBrush>', '', H, flags=re.S)
        hpf = files['Contents/content.hpf'].decode('utf-8')
        for b in re.findall(r'<opf:item id="[^"]+" href="(BinData/[^"]+)"[^>]*/>', hpf):
            if b not in X2 and re.search(r'binaryItemIDRef="%s"' % re.escape(re.search(r'id="([^"]+)" href="%s"' % re.escape(b), hpf).group(1)), X2 + H) is None:
                hpf = re.sub(r'<opf:item id="[^"]+" href="%s"[^>]*/>' % re.escape(b), '', hpf)
                files.pop(b, None)
        files['Contents/content.hpf'] = hpf.encode('utf-8')
    hpf = files['Contents/content.hpf'].decode('utf-8')
    hpf = re.sub(r'(<opf:meta name="(?:creator|lastsaveby)" content="text">)[^<]*', r'\1', hpf)
    files['Contents/content.hpf'] = hpf.encode('utf-8')
    files['Contents/header.xml'] = H.encode('utf-8')
    files['Contents/section0.xml'] = re.sub(r'<hp:linesegarray>.*?</hp:linesegarray>', '', X2, flags=re.S).encode('utf-8')
    prv = re.sub(r'<hp:subList.*?</hp:subList>', '', X2, flags=re.S).replace('</hp:p>', '\n')
    files['Preview/PrvText.txt'] = re.sub(r'<[^>]+>', '', prv)[:1000].encode('utf-8')
    with zipfile.ZipFile(dst, 'w') as o:
        for info in z.infolist():
            if info.filename not in files:
                continue
            o.writestr(zipfile.ZipInfo(info.filename, (1980, 1, 1, 0, 0, 0)), files[info.filename],
                       compress_type=zipfile.ZIP_STORED if info.filename == 'mimetype' else zipfile.ZIP_DEFLATED)
    return found


# ───────────────────────── 명령 ─────────────────────────

def cmd_build(a):
    md_path = os.path.abspath(a.md)
    with open(md_path, encoding='utf-8') as fp:
        md = fp.read()
    prof_path = find_profile(a.profile, [os.path.dirname(md_path), os.getcwd()])
    prof = load_profile(prof_path)
    doc = parse_md(md, prof)
    side = a.side or detect_side(doc, prof)
    tpl = a.template or pick_template(prof, side)
    out = a.output or os.path.splitext(md_path)[0] + '.hwpx'
    if os.path.exists(out) and not a.force:
        raise RenderError(f'이미 있음(한글에서 고친 파일일 수 있음): {out} — 새 이름은 -o, 덮어쓰려면 --force')
    r = Renderer(tpl, prof, os.path.dirname(md_path)).build(doc)
    r.save(out)
    probs = check(out, md, prof)
    print(f'출력: {out}')
    print(f'프로필: {prof_path or "기본(플러그인)"} · 양식: {os.path.basename(tpl)} · 측: {side or "판단 못 함(기본 양식)"}')
    for w in doc['warnings']:
        print(f'경고: {w}')
    for pmsg in probs:
        print(f'오류: {pmsg}')
    return 1 if probs else 0


def cmd_check(a):
    md = open(a.md, encoding='utf-8').read() if a.md else None
    prof = load_profile(find_profile(a.profile, [os.path.dirname(os.path.abspath(a.md))] if a.md else []))
    probs = check(a.hwpx, md, prof)
    print('\n'.join(probs) if probs else '문제 없음')
    return 1 if probs else 0


def cmd_mark(a):
    found = mark_template(a.template, a.output, a.no_background)
    print(f'표지: {", ".join(found)}')
    missing = [m for m in ('표제', '당사자', '본문', '날짜', '법원') if m not in found]
    if missing:
        print(f'경고: 찾지 못한 표지 {missing} — 한글에서 해당 문단에 {{{{표지}}}}를 직접 적으세요')
    return 0


def cmd_where(a):
    start = [os.path.dirname(os.path.abspath(a.md))] if a.md else []
    p = find_profile(a.profile, start + [os.getcwd()])
    prof = load_profile(p)
    print(f'프로필: {p or "기본(플러그인) " + DEFAULT_PROFILE}')
    for k, v in prof['templates'].items():
        print(f'  양식 {k}: {v if os.path.isabs(v) else os.path.join(prof["_dir"], v)}')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build'); b.add_argument('md'); b.add_argument('-o', '--output')
    b.add_argument('--profile'); b.add_argument('--side'); b.add_argument('--template')
    b.add_argument('--force', action='store_true', help='같은 이름의 hwpx를 덮어쓴다')
    c = sub.add_parser('check'); c.add_argument('hwpx'); c.add_argument('--md'); c.add_argument('--profile')
    m = sub.add_parser('mark'); m.add_argument('template'); m.add_argument('-o', '--output', required=True)
    m.add_argument('--no-background', action='store_true')
    w = sub.add_parser('where'); w.add_argument('md', nargs='?'); w.add_argument('--profile')
    a = ap.parse_args(argv)
    try:
        return {'build': cmd_build, 'check': cmd_check, 'mark': cmd_mark, 'where': cmd_where}[a.cmd](a)
    except RenderError as e:
        print(f'오류: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
