# wk-legal — 한국 변호사 법률 사무용 Claude 플러그인

한국 변호사 법률 사무용 Claude 플러그인의 원본 저장소입니다. 배포는 패키지 파일(`.plugin`)로 합니다.

## 수록 플러그인

| 플러그인 | 설명 |
|---|---|
| [`wk-ko-legal`](./wk-ko-legal) | 한국 변호사 법률 사무 스킬 번들 (민사·행정·형사 서면·자문의견서 작성, 제출 전 적대적 검증, 법령 API 조회, lbox·bigcase 판례 검색, lbox 주석서 검색 — 9 skills) |
| [`wk-ko-evidence`](./wk-ko-evidence) | (실험) 한국 소송 증거·기록 분석 — `ko-evidence-analysis` 1개. 유용성 검증 뒤 `wk-ko-legal` 편입 검토 |

## 설치 (패키지 파일)

제작자 또는 배포처(세미나 수료생 자료실 등)에서 제공받은 패키지 파일 `wk-ko-legal.plugin`을 Claude 데스크톱 앱의 플러그인 화면에서 올려 설치합니다. 설치 후 스킬은 `wk-ko-legal:<스킬명>`(실험 플러그인은 `wk-ko-evidence:<스킬명>`) 네임스페이스로 등록됩니다.

새 판이 나오면 새 패키지 파일을 다시 제공받아 같은 방법으로 설치하고, 새 세션에서 설치 버전을 확인합니다. 자동으로 갱신되지 않습니다.

## 개발

패키지는 [`wk-ko-legal/tools/build.py`](./wk-ko-legal/tools/build.py)로 만듭니다. 검증을 통과하면 저장소 루트에 `wk-ko-legal.plugin`이 생깁니다. 다른 플러그인은 `--plugin <폴더>`로 검증·패키징합니다(예: `--plugin wk-ko-evidence`). 패키지에는 이 저장소의 `LICENSE.md`가 들어가고, 개발용 파일(evals/·tools/·CHANGELOG.md, README의 개발 절)은 빠집니다. 자세한 내용은 [플러그인 README](./wk-ko-legal/README.md).

제작자 환경에서는 이 저장소를 Claude Code 마켓플레이스(`.claude-plugin/marketplace.json`)로 등록해 쓸 수도 있습니다. 이 방법은 배포용이 아닙니다.

## 라이선스

오픈소스가 아니며, [제한적 사용권 라이선스](./LICENSE.md)가 적용됩니다. 요지: 제작자 또는 배포처에서 패키지를 직접 제공받은 본인만 사용할 수 있습니다. 사용·수정·자기 업무에의 재사용은 자유이나 **원본 그대로 또는 원본의 형태가 상당히 남아 있는 상태의 재배포와 다른 사람에 대한 전달(같은 사무소 구성원 포함)은 금지**됩니다. 플러그인으로 생성한 업무 산출물에는 제한이 없습니다.
