# wk-ko-legal

한국 변호사 법률 사무용 스킬 9종을 단일 플러그인으로 관리한다. 설치 후 스킬은 `wk-ko-legal:<스킬명>` 네임스페이스로 등록된다.

| 스킬 | 역할 |
|---|---|
| ko-civil-litigation-drafting | 민사 서면(소장·답변서·준비서면, 항소·상고이유서·상고이유 답변서, 보전·집행·담보·절차 신청서) 작성·검토 |
| ko-administrative-drafting | 행정쟁송 서면(행정소송 — 절차 신청·상소이유서 포함, 행정심판·집행정지)과 행정청 대상 서면(처분 전 의견제출·처분 후 이의신청) 작성·검토 — 원고·청구인 측 |
| ko-criminal-drafting | 형사 서면(수사·공판·상소 변호, 고소·고발 대리) 작성·검토 |
| ko-legal-advisory-drafting | 자문의견서 작성·검토 |
| ko-adversarial-review | 제출 전 적대적 검증 — 서면·자문의견서의 인용 전수 대조(판시 유지·상급심·명제 대응), 요건·절차·증거 공격, 예상 반박, 기한 조치를 심각도 순 보고서로(고쳐 쓰지 않고 대체 문안만) |
| ko-law-api | 국가법령정보 OPEN API 조회(법령·행정규칙·자치법규·별표·해석례) |
| lbox-case-search | lbox.kr 판례 검색·정리 |
| bigcase-case-search | bigcase.ai(빅케이스) 판례 검색·정리 |
| lbox-commentary-search | lbox.kr 주석서·실무서 검색 |

## 운용 메모

- ko-law-api: OC 인증키 필요. 키 파일(`LAW_GO_KR_OC=…` 한 줄)을 `~/.config/korean-law-api/.env`(구명칭 경로 유지) 또는 작업 폴더의 `.law_api.env`에 둔다 — Cowork 등 샌드박스 VM은 후자. `--oc` 인자는 세션 기록에 키가 남으므로 쓰지 않는다. 회귀 검사: `python3 tools/law_api_regress.py [--live]`.
- ko-adversarial-review는 고쳐 쓰지 않는 검증 보고서를 낸다(고쳐 쓰기는 drafting 4종의 검토 모드). 검증 절차의 정본은 `shared/적대적-검증-절차.md`이고, drafting 4종도 검토 모드의 판례·조문·증거 대조에 같은 파일의 3.을 쓴다. Claude가 이 대화에서 쓴 초안은 새 문맥의 서브에이전트로 검증한다(작성 전제를 물려받지 않게).
- ko-administrative-drafting은 기준 시점 법령(처분시법, 제재처분은 위반행위시법 — SKILL.md 4.2), ko-criminal-drafting은 행위시법을 ko-law-api `get-asof`로 확인한다. 형사 양형기준은 로컬 구조화 파일(json/md, 사용자 제공 시) 우선, 없으면 양형위원회 공식 웹사이트 확인.
- drafting 4종의 문체·문형 기본값은 한국 법조 서면의 일반 관행이다. 로컬 실무지식베이스(기본 `~/LLM-wiki`, Cowork VM은 마운트된 `~/mnt/LLM-wiki`, `WK_LEGAL_WIKI_ROOT`로 재정의 — 지정하면 그 경로만 본다)가 있으면 `shared/LLM-wiki-연동-정책.md`에 따라서만 병용하고, 없으면 연동 단계는 조용히 생략된다.
- 판례는 판례DB를 먼저 쓴다(`shared/판례-인용-정책.md` 1.): 원격 판례 MCP(llm-wiki 커넥터)와 로컬 판례DB(`{지식베이스 루트}/판례DB/_색인.sqlite` — `shared/case_db.py`로 읽기 전용 조회) 중 수록 범위가 최신인 쪽. 판례DB 원문은 원본 대조 없이 인용하고, 인용 형식은 따옴표 없는 원문 전재 + 괄호 출처가 기본(큰따옴표 직접 인용도 가능). 회귀 검사: `python3 tools/case_db_regress.py`.
- 스킬 description 트리거 회귀: `python3 tools/trigger_eval.py --check`(세트 형식) → `--limit 3`(사전 점검) → 전량 실측(모델 호출 요금 발생). 결과는 임시 폴더, `--rescore DIR`로 재채점. 평가 세트는 각 스킬 `evals/trigger-eval.json`(음성 행에 기대 목적지 `expected`).
- lbox 2종·bigcase: Claude in Chrome + 해당 사이트(lbox.kr / bigcase.ai) 로그인 전제. 서면 작성에서는 판례DB로 부족한 쟁점·핵심 쟁점의 핵심 판례 검색·최신성·인용 수·사건번호 URL 직행 검증에만 쓴다.
- evals/·tools/·CHANGELOG.md는 개발·이력용이다 — `tools/build.py`가 만드는 .plugin(zip)에서는 빠지지만, 마켓플레이스(git) 설치본에는 들어 있다(런타임에 적재되지는 않는다).
- `tools/build.py`는 검증(구조·참조·드리프트 린트·절 포인터 실존·스킬 수 표기·마켓플레이스 등재) 뒤 패키징한다. 같은 저장소의 다른 플러그인(실험 플러그인 `wk-ko-evidence`)은 `--plugin <폴더>`로 같은 검증·패키징을 한다. 드리프트 린트는 개정 뒤 일부 파일에 남기 쉬운 낡은 표현(lbox 단독 확인, '. 자', 옛 Chrome 도구명, `python` 실행, JS 분할 반환, 행정규칙일련번호 고정 예시, 크로스 폴백 '0건이면 lbox', 보전·집행의 민사소송 실무제요 지정, 가정 표지 '가사', 별칭 괄호 안 마침표, 금액 한글 병기, 형사 증거 좌표 '○번'·'○면', 부분 날짜 '하나만', 서면 법령명 낫표, 조문 '§', 패키지 필드의 text_status 누락, 실제 사건 예문 재유입 등)을 막는다. 개별 스킬(SKILL.md·references·evals)에는 위키 직접 참조·구조 노출·내부 용어·지식베이스 부재 고지를 막는 `SKILL_TREE_PATTERNS`를, 연동 정책 밖의 공용 문서에는 위키 문체 근거 표지를 막는 `NON_POLICY_PATTERNS`를 적용하고, SKILL.md가 컴팩션 재첨부 한도(약 2만 자)를 넘으면 경고한다 — 새 전수 grep 대상이 생기면 `DRIFT_PATTERNS`에 한 줄 추가한다.

## 라이선스

저장소 루트의 [LICENSE.md](../LICENSE.md)(제한적 사용권 — 사용·수정·재사용 자유, 원형 잔존 상태의 재배포 금지)가 적용된다.

## 빌드·배포

```bash
python3 tools/build.py   # 검증 + wk-ko-legal.plugin 생성 (저장소 부모 폴더에)
```

수정 절차: 스킬 원본 수정 → CHANGELOG 기록 → plugin.json 버전 증가 → build.py → Settings > Capabilities에서 재설치.
