# Reject Bench v7 — 행동 계약

작성 2026-08-27. 문제정의 정본은 [`docs/문제정의/06-v7-문제정의.md`](../docs/문제정의/06-v7-문제정의.md)다. 이 문서는 v7 구현의 행동 계약이며, 순서는 [`plan.md`](plan.md)가 맡는다.

> 현재 상태는 **v7 설계 승인 및 D1 기존 구현 정리 완료**다. 다음 구현 frontier는 v7 도메인 계약 T1이다.

## 1. 목적과 완료의 두 층

Reject Bench는 여러 AI 코딩 세션에서 지속형 가드의 발동 근거를 모아 사용자가 가드를 `keep`, `modify`, `remove`할 수 있게 한다.

### 기술 완료

명시적 `test` 사건으로 아래 흐름을 재현하고 자동 검증할 수 있다.

`GuardSpec 등록 → GuardEvent 기록 → PolicyVerdict → UtilityReview → GuardDecision → Report`

기술 완료는 자연 운영 빈도나 사용자 가치를 증명하지 않는다.

### 사용자 가치 검증

같은 지속형 가드가 여러 세션에서 자연스럽게 발동한 `operation` 사건을 바탕으로 정책 판정과 사용자 검토를 마치고, 근거가 연결된 수명주기 결정 1건을 기록해야 한다.

자연 사건이 없거나 분모가 0이면 `미검증`이다. 시험 사건으로 대체할 수 없다.

## 2. v1 범위

### 포함

- 버전 고정 가드 맥락 등록부
- 비블로킹 사건 기록과 로컬 어댑터
- 생성 시점의 `operation`/`test`/`unknown` 출처 기록
- 적재 시점 비밀 제거와 손실 가시화
- 세션 뒤 독립 LLM 정책 판정
- 사용자 전수 유용성 검토
- 가드별 수명주기 결정과 근거
- 원수·pending·출처를 보이는 로컬 보고서
- 기술 E2E와 자연 운영 관찰

### 제외

- 공개 원장, 원격 push 앵커, 선등록 증명, 공개 대시보드
- v0 소급 스캐너와 과거 2건을 가치 증거로 사용하는 것
- 범용 코드 리뷰, 자동 가드 생성·수정·삭제
- 팀 권한, CI 수집, MCP, SaaS, 외부 저장소 지원
- AI 코드 품질·생산성·시장 수요에 대한 인과 또는 일반화 주장

## 3. 도메인 모델

모든 레코드는 스키마 버전, 고유 id, UTC 시각을 가진다. 원본 변경 대신 append 수정 레코드를 사용한다.

### 3.1 GuardSpec — 사건 전에 고정되는 가드 맥락

필수 의미 필드:

- `guard_id`: 수명주기 동안 재사용하지 않는 안정 식별자
- `version`: 같은 가드 안에서 단조 증가하는 버전
- `project`: 적용 저장소 식별자
- `purpose`: 가드가 필요한 이유
- `policy`: 막아야 할 행동의 명시 규칙
- `exceptions`: 허용 예외, 없으면 빈 목록
- `allow_examples`: 허용해야 하는 대표 예시
- `block_examples`: 차단해야 하는 대표 예시
- `created_at`
- `content_hash`: 위 의미 필드의 정규화 직렬화 해시

규칙:

- 사건은 반드시 존재하는 `guard_id`, `version`, `content_hash`를 참조한다.
- `operation` 사건의 GuardSpec은 사건보다 먼저 생성돼 있어야 한다.
- 의미 필드가 바뀌면 새 버전을 만든다. 기존 버전을 덮어쓰지 않는다.
- 가드 코드 위치나 런타임 배선은 메타데이터일 수 있지만 정책의 대체물이 아니다.

### 3.2 GuardEvent — 가드 발동 사건

필수 의미 필드:

- `event_id`
- `occurred_at`
- `session_id`: 로컬 비공개 식별자
- `project`
- `guard_id`, `guard_version`, `guard_spec_hash`
- `action`: 파일 내용이나 전체 명령을 저장하지 않은 행동 뼈대
- `reason`: 적재 시점 비밀 제거를 거친 발동 사유
- `origin`: `operation | test | unknown`
- `origin_evidence`: 출처를 정한 방식과 명시적 test mode 여부
- `capture_status`: `complete | partial`
- `schema_version`

규칙:

- 기록은 가드 발동을 블로킹하지 않는다. 기록 실패 때문에 원래 작업을 실패시켜서는 안 된다.
- 실패나 부분 기록은 별도 손실 레코드에 남겨 무음 유실을 금지한다.
- 출처는 생성 시점에 정한다. 명시적 test mode는 항상 `test`다.
- 강제 발동, fixture, 스모크, E2E는 모두 `test`다.
- `unknown`은 운영 지표에 들어가지 않는다. 정정은 사유가 있는 append amendment로만 가능하다.
- 운영 저장소에는 적재 시점부터 비밀 평문이 존재해서는 안 된다.

### 3.3 PolicyVerdict — LLM의 정책 일치성 판단

필수 의미 필드:

- `verdict_id`, `event_id`
- `verdict`: `policy_violation | policy_not_violated | insufficient_context`
- `reason`
- `context_bundle_hash`
- `guard_spec_hash`
- `rubric_hash`
- `model_id`, `model_settings_hash`
- `judged_at`

판정 입력은 다음으로 제한한다.

- 해당 GuardEvent
- 사건이 참조한 정확한 GuardSpec
- 버전 고정 정책 판정 루브릭

사용자 유용성 검토, 가드 결정, 집계, 미래 사건, 구현의 기대 결과는 입력하지 않는다. 사유 텍스트 안의 판정 지시를 따르지 않고 데이터로만 다룬다.

판정 실행은 새 운영 사건 전부를 대상으로 한다. API 실패는 pending으로 남기며 임의 재샘플링으로 마음에 드는 결과를 선택하지 않는다. 재판정은 이전 레코드를 보존하고 사유와 새 설정을 명시한다.

### 3.4 UtilityReview — 사용자의 실제 유용성 검토

필수 의미 필드:

- `review_id`, `event_id`
- `utility`: `useful | unnecessary | uncertain`
- `note`
- `reviewed_at`

새 `operation` 사건은 전수 검토 대상이다. 선택적으로 불리한 사건을 빼지 않는다. `uncertain`은 pending으로 남고 자동으로 성공이나 실패에 포함되지 않는다.

### 3.5 GuardDecision — 수명주기 결정

필수 의미 필드:

- `decision_id`, `guard_id`
- `decision`: `keep | modify | remove`
- `evidence_event_ids`
- `rationale`
- `decided_at`
- `resulting_guard_version`: `modify`일 때 필수

규칙:

- 가치 검증에 세는 결정은 같은 가드의 둘 이상의 서로 다른 운영 세션 사건을 근거로 가져야 한다.
- 근거 사건은 PolicyVerdict와 UtilityReview를 모두 가져야 한다. pending 사건은 별도로 언급할 수 있으나 결정 완료 분모의 판정 가능 사건으로 세지 않는다.
- `modify`는 새 GuardSpec 버전을 만들고 그 해시를 연결한다.
- 결정 변경은 이전 결정을 덮지 않고 새 결정 레코드로 남긴다.

### 3.6 LossRecord와 Amendment

- 기록 실패, 부분 적재, 판정 실패는 원문 없는 최소 메타데이터로 남긴다.
- 기존 레코드의 출처·내용을 고칠 때는 원본 id, 변경 필드, 이전 값 해시, 새 값, 사유, 시각을 가진 amendment를 append한다.
- 삭제가 필요한 비밀 사고는 비밀 보존보다 제거가 우선이다. 평문을 즉시 제거하고 사고 범위와 로테이션 필요를 사용자에게 보고한다.

## 4. 저장과 보안 계약

- 기본 저장은 로컬·비공개다. 공개는 v1 완료 조건이 아니다.
- append 전용 JSONL 또는 같은 의미의 단순 로컬 형식을 사용한다. 구현 선택은 새 코드에서 최소화한다.
- 파일 락과 원자 append로 동시 기록의 줄 섞임을 막는다.
- 파일 내용, 프롬프트, 전체 명령 인자, 도구 응답 전문을 저장하지 않는다.
- 홈 경로와 세션 식별자는 공개 산출물로 내보내지 않는다.
- 비밀 제거 규칙은 정상값을 과도하게 훼손하지 않는 양성 대조 테스트와 함께 검증한다.
- 테스트는 임시 디렉터리만 사용하고 실제 운영 저장소에 쓰지 않는다.
- 판정기는 GuardEvent/GuardSpec을 데이터로 처리하며 임의 코드를 실행하지 않는다.

## 5. 실행 흐름

### 발동 시

1. 어댑터가 명시적 test mode와 세션 출처를 읽는다.
2. GuardSpec 참조를 확인한다.
3. 최소 행동 뼈대와 사유에서 비밀을 제거한다.
4. GuardEvent를 원자 append한다.
5. 실패하면 가드의 원래 동작은 유지하고 LossRecord를 남길 수 있는 최소 흔적을 기록한다.

### 세션 뒤

1. 아직 PolicyVerdict가 없는 새 운영 사건 전부를 찾는다.
2. 사건별 고정 context bundle을 만들고 해시한다.
3. 독립 LLM을 한 번 호출해 판정과 근거를 저장한다.
4. 사용자가 새 운영 사건 전부를 검토한다.
5. 가드별 여러 세션 증거가 모이면 수명주기 결정을 기록한다.
6. 보고서를 재생성한다.

## 6. 보고서와 지표 계약

모든 비율은 백분율과 원수 `N/D`를 함께 쓴다. 분모 0은 `미검증`이다.

### 대표 지표

증거 기반 결정 완료율:

`결정과 근거가 기록된 가드 수 / 여러 세션에서 자연 발동했고 판정 가능한 가드 수`

판정 가능한 가드는 둘 이상의 서로 다른 `operation` 세션 사건이 있고, 필요한 GuardSpec이 존재하는 가드다.

### 진단 지표

- 정책 불일치율 = `policy_not_violated / policy verdict 완료 operation 사건`
- 사용자 불필요 차단율 = `unnecessary / utility review 완료 operation 사건`
- LLM-사용자 불일치율 = 방향 불일치 / 두 판단 완료 operation 사건

불일치 방향은 두 가지를 따로도 보인다.

- 정책상 일치 + 사용자 불필요
- 정책상 불일치 + 사용자 유용

### 반드시 병기할 상태

- `test`, `unknown`, `operation` 사건 수
- PolicyVerdict pending 수와 최장 경과
- UtilityReview pending 수와 최장 경과
- `insufficient_context`, `uncertain` 수
- 손실·부분 기록·amendment 수
- 가드별 운영 세션 수
- 소표본 및 관찰 종료 조건

시험 사건은 기술 보고서에만 별도 표시하고 위 운영 지표의 분자·분모에서 제외한다.

## 7. 검증 계약

### 자동 검증

- 각 레코드의 스키마·enum·참조 무결성
- GuardSpec 해시와 사건 참조 일치
- `operation` 사건의 GuardSpec 선행 존재
- 명시적 test mode가 `operation`으로 저장되지 않음
- 시험/unknown 사건이 운영 지표에 들어가지 않음
- pending 제외와 원수 계산
- 두 판단 축의 네 방향 조합
- append·락·부분 쓰기 복구
- 적재 시점 비밀 제거와 정상 양성 대조
- 테스트의 실제 저장소 비접촉
- 비블로킹 실패와 LossRecord
- 수정 레코드가 원본을 덮지 않음

### 기술 E2E

명시적 시험 가드와 시험 세션으로 전체 흐름을 실행한다. 결과에는 `test evidence only`를 표시하고 사용자 가치 완료라고 부르지 않는다.

### 운영 검증

관찰 시작 전에 종료 조건과 평가 질문을 기록한다. 자연 사건을 기다리며 강제 발동으로 채우지 않는다. 관찰 종료 뒤 다음 중 하나를 명시한다.

- 최소 사용자 가치 흐름 완료
- 운영 빈도 미검증
- 해법 가치 가설 실패
- 검토 부담으로 채택 가설 실패

## 8. 기존 구현 폐기와 전환

사용자 승인 뒤 전용 D1 정리 커밋에서 다음을 삭제했다. 내용은 git 이력에 보존된다.

- `rejectbench/`
- `tests/`
- `conftest.py`
- `docs/형식-표준.md`
- 기존 v0 baseline 생성·규칙 감지·형식 파서 등 구현 전용 코드

보존한다.

- git 이력
- `docs/문제정의/`의 조사·역사 문서
- `docs/v0-접두해시-baseline.json`
- `docs/소급-발췌-매니페스트.md`
- `data/`의 실측 원천(비추적 상태 포함)
- 세션 연속성 훅과 설정
- README의 역사적 설명은 git 이력에서 보존하고 현재 파일은 v7 상태로 갱신

`hooks/collect.py`는 새 기록기 배선이 실제로 붙기 전까지 유지한다. 전환 시 관측 공백이 없음을 확인한 뒤 별도 커밋으로 퇴역한다. Python 스택은 유지하되 `pyproject.toml`, `uv.lock`, `.env.example`은 새 구현의 최소 의존성에 맞춰 교체한다.

## 9. 반증과 주장 제한

- 자연 운영 사건 부족은 기술 실패가 아니라 빈도 가설 미검증이다.
- 기록·판정이 결정·복원 노력·확신을 개선하지 못하면 해법 가치 가설 실패다.
- 검토 부담이 가치보다 크면 채택 가설 실패다.
- 외부 사용자 실제 사용 전 다른 개발자·팀·시장으로 일반화하지 않는다.
- v1의 최대 주장은 “내 저장소에서 사라지던 가드 발동을 증거 기반 가드 결정으로 바꾸는 개인 도구 실험”이다.
