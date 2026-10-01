# wk-ko-evidence (실험)

한국 소송의 증거·기록 분석 스킬 `ko-evidence-analysis` 하나를 담은 실험 플러그인입니다. 쪽 단위 markdown 변환본(`pages/page_NNN.md` + `_index.md`)을 입력으로, 문건 다이제스트·원천 현황·타임라인·인물 목록(1층)과 쟁점별 분석표 — 상대방 서증 인부 검토표, 진술 변천·모순표, 증거능력 스크리닝 등(2층) — 를 만들고, 산출물의 직접 인용을 원문과 기계로 대조합니다(`scripts/evidence.py verify`).

## 상태

`wk-ko-legal`에 넣지 않고 따로 둡니다. 실물 기록 비교 시험(2026-09-26, 민사 2건)에서 같은 모델에게 스킬 없이 직접 맡긴 결과보다 품질이 낫다는 결과가 나오지 않았기 때문입니다. 인용의 글자 그대로 정확성은 기계로 보장되지만, 최종 산출물의 평가 점수로는 이어지지 않았습니다. 다음을 확인한 뒤 `wk-ko-legal` 편입을 다시 검토합니다.

- 한 에이전트가 원문을 직접 읽기 어려운 대용량 기록(예: 형사 증거기록 1,000쪽 이상)
- 같은 1층 분석을 여러 산출물·여러 세션에서 재사용하는 경우
- 지시문 보완 뒤 같은 조건의 재시험에서 무스킬과 같거나 나은 결과

## 사용

- **이름을 불러 씁니다** — "ko-evidence-analysis로 이 기록을 분석해 줘". `wk-ko-legal`과 함께 설치하면 형사 서면 스킬이 "증거기록 첨부" 요청을 먼저 가져갈 수 있습니다.
- `wk-ko-legal`이 함께 설치돼 있으면 조문 확인(`ko-law-api`)과 판례·지식베이스 정책(`shared/판례-인용-정책.md`·`shared/LLM-wiki-연동-정책.md`)을 씁니다. 없어도 동작하며, 확인하지 못한 조문·판례는 미확인 표식으로 남습니다.
- 분석 산출물을 서면에 쓸 때는 서면 스킬에 산출물 파일 경로를 참고 자료로 알려 주면 됩니다.

## 설치

```
/plugin install wk-ko-evidence@wk-legal
```

Cowork 등 파일 업로드로 설치할 때는 저장소 루트에서 `python3 wk-ko-legal/tools/build.py --plugin wk-ko-evidence`를 실행해 만든 `wk-ko-evidence.plugin`을 올립니다.

<!-- package:skip-start -->
## 개발

- 검증·패키징: `python3 wk-ko-legal/tools/build.py --plugin wk-ko-evidence [--no-zip]`
- 스크립트 회귀 검사(합성 기록): `python3 wk-ko-evidence/tools/evidence_regress.py`
<!-- package:skip-end -->
