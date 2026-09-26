# Changelog

## 0.1.0 — 2026-09-26

`wk-ko-legal` 기능 브랜치에서 만들던 `ko-evidence-analysis`(미등록·미배포)를 별도 실험 플러그인으로 분리. 실물 기록 비교 시험에서 같은 모델의 무스킬 작성보다 품질이 낫다는 결과가 나오지 않아, 유용성이 검증될 때까지 `wk-ko-legal` 밖에 둔다.

- **스킬 내용은 분리 전과 같다** — 2층 구조(1층 다이제스트 카드·원천 현황·타임라인·인물·주의메모 / 2층 쟁점 분석 모드), 약식 1층(원문 15만 자 미만), 인용 정확성 게이트, `scripts/evidence.py` 서브커맨드 14개(`discover`·`import`·`locate`·`gate`·`index`·`evlist`·`assign`·`coverage`·`cardfill`·`cardsha`·`derive`·`profile`·`sum`·`verify`). 실물 민사 기록 2건 실증(인용 대조 불일치 0), 2026-09-26 검수 결함 E1~E7 수정과 합성 회귀 검사(`tools/evidence_regress.py`)를 포함한다
- **플러그인 밖 참조를 조건부로**: 판례·지식베이스 정책(`shared/판례-인용-정책.md`·`shared/LLM-wiki-연동-정책.md`)과 `ko-law-api`·drafting 접합점은 `wk-ko-legal`이 함께 설치돼 있을 때만 쓴다. 정책 파일은 설치 위치를 `find`로 찾고, 없으면 판례·조문은 미확인 표식으로 남긴다
