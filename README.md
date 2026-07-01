# wk-legal — Claude Code 플러그인 마켓플레이스

한국 변호사 법률 사무용 Claude Code 플러그인을 배포하는 마켓플레이스입니다.

## 수록 플러그인

| 플러그인 | 설명 |
|---|---|
| [`wk-ko-legal`](./wk-ko-legal) | 한국 변호사 법률 사무 스킬 번들 (민사·행정·형사 서면·자문의견서 작성, 작성 계획, 법령 API 조회, lbox 판례·주석서 검색, 사건 진행 동기화 — 9 skills) |

## 설치

Claude Code에서:

```
/plugin marketplace add https://github.com/wkjin-jeong/wk-legal
/plugin install wk-ko-legal@wk-legal
```

설치 후 스킬은 `wk-ko-legal:<스킬명>` 네임스페이스로 등록됩니다.

## 마켓플레이스 갱신

플러그인 업데이트를 반영하려면:

```
/plugin marketplace update wk-legal
```

## 개발

플러그인 검증·패키징 스크립트는 [`wk-ko-legal/tools/build.py`](./wk-ko-legal/tools/build.py)를 참고하세요. 자세한 내용은 [플러그인 README](./wk-ko-legal/README.md).
