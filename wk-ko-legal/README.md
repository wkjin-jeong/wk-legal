# wk-ko-legal

한국 변호사 법률 사무용 스킬 9종을 단일 플러그인으로 관리한다. 설치 후 스킬은 `wk-ko-legal:<스킬명>` 네임스페이스로 등록된다.

| 스킬 | 역할 |
|---|---|
| ko-civil-litigation-drafting | 민사 서면(소장·답변서·준비서면) 작성·검토 |
| ko-administrative-drafting | 행정쟁송 서면(행정소송·행정심판·집행정지·처분 전 단계) 작성·검토 — 원고·청구인 측 |
| ko-criminal-drafting | 형사 서면(수사·공판·상소 변호, 고소·고발 대리) 작성·검토 |
| ko-legal-advisory-drafting | 자문의견서 작성·검토 |
| ko-legal-writing-plan | 서면·의견서 작성 전 계획·전략 설계 (민사·행정·형사·자문) |
| ko-law-api | 국가법령정보 OPEN API 조회(법령·행정규칙·자치법규·별표·해석례) |
| lbox-case-search | lbox.kr 판례 검색·정리 |
| lbox-commentary-search | lbox.kr 주석서·실무서 검색 |
| lbox-case-progress | lbox 등록 사건 진행경과 동기화 보고서 |

## 운용 메모

- ko-law-api: OC 인증키 필요(`--oc`/`LAW_GO_KR_OC`/`.env`). 기존 호환을 위해 스크립트 내부 식별자와 설정 경로(`~/.config/korean-law-api/.env`)는 구명칭을 유지한다.
- ko-administrative-drafting은 처분시법, ko-criminal-drafting은 행위시법을 ko-law-api `get-asof`로 확인한다. 형사 양형기준은 로컬 구조화 파일(json/md, 사용자 제공 시) 우선, 없으면 양형위원회 공식 웹사이트 확인.
- lbox 3종: Claude in Chrome + lbox.kr 로그인 전제. lbox-case-progress는 `$LBOX_DIR` 클라우드 동기화 폴더 사용.
- evals/는 개발용으로 패키지에 포함되지 않는다.

## 빌드·배포

```bash
python3 tools/build.py   # 검증 + wk-ko-legal.plugin 생성 (저장소 부모 폴더에)
```

수정 절차: 스킬 원본 수정 → CHANGELOG 기록 → plugin.json 버전 증가 → build.py → Settings > Capabilities에서 재설치.
