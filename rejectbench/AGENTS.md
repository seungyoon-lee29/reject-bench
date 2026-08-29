# rejectbench 패키지 — 모듈 규칙

v7 도메인 구현 전체가 이 패키지다. 행동의 정본은 `.dryforge/spec.md`이며, 여기는 코드만 봐서는 안 보이는 경계·불변식·함정을 적는다.

## 모듈 맵

- `records` — 7개 레코드 스키마와 enum. 전역 규칙(스키마 버전·고유 id·UTC aware)과 필드 조합 강제(origin↔origin_evidence 닫힌 표, unregistered↔참조 셋 배타)는 전부 여기서 생성 시점에 막는다.
- `hashing` — 정규화 직렬화 해시. `content_hash` 도메인은 의미 5필드(purpose·policy·exceptions·allow_examples·block_examples)만이다.
- `origin` — 출처 결정표와 강등 전이 규칙(순수 함수).
- `store` — append 전용 JSONL, flock + O_APPEND 단일 write. `production_root()`는 패키지 파일 위치 기준 `<repo>/data/v7`라서 어느 저장소에서 호출돼도 중앙으로 간다.
- `dataset` — append 순서 보존 적재와 참조 무결성 검사. 선행성 판정은 append 순서가 근거고 벽시계는 이상 신호 표시용이다.
- `metrics` — 확정값/보류값/미처리 분류와 판정 가능 가드(분모) 단일 정의.
- `registry`, `cli` — GuardSpec 등록부(버전 강제=content_hash 비교)와 전체 서브커맨드.
- `recorder`, `scrub`, `wrapper` — 발동 기록 경로. 래퍼는 가드의 stdout/stderr/exit를 그대로 투명 전달하고, 기록은 최선 노력이다.
- `judge`, `rubric` — 세션 뒤 정책 판정(OpenAI, stdlib urllib). 교정 레코드는 메인 store가 아니라 사이드카 `calibration.jsonl`에 쌓인다.
- `review`, `decision` — 전수 검토 큐와 수명주기 결정.
- `report` — Markdown 보고서. 기준선은 store 루트 `baseline.json` 관례를 읽는다(없으면 "미측정" 출력).

## 불변식 (어기면 데이터가 오염된다)

- **기록 실패가 가드 결과를 바꾸면 안 된다.** recorder/wrapper의 어떤 변경도 예외를 호출자 쪽 exit 2로 새게 하면 안 된다. 유실 사다리는 주 저장 → LossRecord → OS 임시 경로/stderr 순이고, "절대 보장"이라 주장하지 않는다.
- 원본 레코드는 불변 — 정정은 amendment append뿐. 유효 출처는 `dataset.effective_origin`으로 파생하며, `operation` 승격 전이는 스키마가 거부한다.
- 가치 산입(countable)·`post-remove` 표시·관측 도중 신규 가드 표기는 **저장값이 아니라 파생 계산이 정본**이다. recorder는 `post_remove=False`로 적고, 표시 시점에 `decision.post_remove_event_ids`를 쓴다. 산입을 입력으로 받는 함수를 추가하지 말 것.
- 판정 입력은 사건 + 그 사건이 참조한 정확한 GuardSpec + 루브릭뿐이다. 검토·결정·집계·세션 id를 bundle에 넣으면 테스트(`bundle 순수성`)가 막는다 — 그 테스트를 완화하지 말 것.
- 전문 저장 금지: `ActionSummary`는 구조화 필드만 받고 초과 키를 거부한다. reason은 `redact_command_echo`(명령 되풀이 제거) → `scrub_text`(비밀 치환) → 4000자 절단 순서다.

## 경계

- `hooks/`는 세션 연속성 인프라로 이 패키지와 무관하다 — 수정 금지(퇴역은 별도 결정).
- 운영 store `data/`에 테스트가 닿으면 `tests/conftest.py`의 autouse 게이트가 하드 실패시킨다. 이 게이트를 끄지 말 것.
- 실제 가드 스크립트(전역 `block-dangerous-git.sh`, reply-gate `protect-live-reports.sh`)는 해시 계산용 바이트 읽기만 한다. 실행·수정 금지(래퍼가 배선 경로에서 실행하는 것만 예외).

## 함정

- 배선 명령은 반드시 `uv run --project /Users/ian/workspace/reject-bench python -m rejectbench.wrapper …` 형태여야 한다. 맨 `python3 -m`은 패키지를 못 찾는다(package=false, pythonpath는 pytest 전용).
- 전역 가드는 Bash 명령 문자열 **전문**을 패턴 매칭한다. 위험 패턴 텍스트가 포함된 픽스처·문서를 heredoc/echo로 쓰면 자기 차단된다 — 파일 도구(Write/Edit)로 쓸 것. (이 발동 자체는 도구가 기록한다.)
- 판정 기본 설정 `{"temperature": 0}`은 모델이 거부할 수 있다 — 그 경우 미처리로 안전 실패하며, settings 주입으로 조정하고 `model_settings_hash`가 바뀐 재판정 규율을 따른다.
- 같은 (spec·rubric·model·settings) 조합의 교정 실패 기록은 재사용된다 — 동일 설정 강제 재교정 경로는 없고, 설정을 바꾸면 자연히 재교정된다.

## 테스트 규칙

- 임시 디렉터리만. 네트워크 금지(판정은 fake 전송 주입). 실제 가드는 mock 스크립트로 인터페이스(exit 2+stderr / deny JSON+exit 0)만 재현.
- 새 행동은 red→green으로: 실패 테스트 확인 후 구현. 전체 게이트는 `uv run pytest`(hooks 연속성 회귀 포함) 하나다.
