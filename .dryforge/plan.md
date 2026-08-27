# Reject Bench v7 — 실행 계획

작성 2026-08-27. 행동 계약은 [`spec.md`](spec.md), 문제정의는 [`docs/문제정의/06-v7-문제정의.md`](../docs/문제정의/06-v7-문제정의.md)가 정본이다.

## 상태 표시

- `[ ]` 미착수
- `[~]` 진행 중
- `[x]` 완료 및 검증됨
- `[?]` 외부 상태나 자연 사건을 기다림

현재 frontier는 **T1 v7 도메인 계약**이다. D0 승인과 D1 기존 구현 정리는 완료됐다.

## D0 — v7 문제·계약 승인

- [x] 문제정의 재인터뷰와 공유 이해 확인
- [x] `06-v7-문제정의.md` 작성
- [x] `기획-입력.md`, v7 spec·plan·handoff 초안 작성
- [x] 기존 구현 보존/폐기/전환 경계 문서화
- [x] 사용자 검토와 승인 (2026-08-27)

완료 증거: 문서 링크·참조 검사, `git diff --check`, 사용자의 명시 승인.

## D1 — 기존 구현 정리

의존: D0 승인.

전용 정리 커밋 하나에서:

- [x] `rejectbench/`, `tests/`, `conftest.py`, `docs/형식-표준.md` 삭제
- [x] v0 baseline 생성·후보 규칙·기존 형식 파서 등 구현 전용 코드가 남지 않았는지 검색
- [x] 실측 원천·baseline·연구 문서·연속성 훅이 보존됐는지 확인
- [x] `hooks/collect.py`는 새 기록기 cutover 전까지 유지
- [x] 새 구현을 위한 최소 Python 프로젝트 골격만 남김
- [x] 정리 커밋 생성

검증: 삭제 manifest와 `git diff --name-status`, 보존 파일 존재 확인, 연속성 훅 회귀 검사.

## T1 — v7 도메인 계약

의존: D1.

- [ ] GuardSpec, GuardEvent, PolicyVerdict, UtilityReview, GuardDecision, LossRecord, Amendment 스키마를 새로 정의
- [ ] enum·필수 필드·참조 무결성·정규화 해시 규칙 구현
- [ ] 단순 append 저장소와 테스트용 임시 저장소 경계 구현
- [ ] 스키마/참조/불변식 테스트

완료 조건: spec 3절의 모든 엔티티와 참조가 테스트로 고정되고 실제 운영 경로에는 쓰지 않는다.

## T2 — 가드 등록부와 맥락 버전

의존: T1.

- [ ] GuardSpec 생성·검증 CLI
- [ ] 의미 변경 시 새 버전을 강제하고 덮어쓰기 차단
- [ ] 정규화 content hash 생성
- [ ] 허용/차단 예시와 예외의 최소 품질 검증
- [ ] 존재하지 않거나 사건보다 늦은 spec 참조 차단

완료 조건: 두 버전의 시험 가드에서 과거 사건이 항상 과거 해시를 참조함을 검증.

## T3 — 기록기·어댑터·출처·비밀 제거

의존: T2.

- [ ] 비블로킹 원자 append 기록기
- [ ] `operation | test | unknown` 출처와 근거 기록
- [ ] 명시적 test mode가 전 구간에서 보존되도록 구현
- [ ] 최소 행동 뼈대 추출과 적재 시점 비밀 제거
- [ ] 기록 실패·부분 기록 LossRecord
- [ ] 동시 append, 부분 쓰기, 정상 양성 대조, 운영 저장소 비접촉 테스트
- [ ] 새 배선을 실제 지속형 가드 하나에 설치
- [ ] cutover 후 관측 공백이 없음을 확인하고 v0 `hooks/collect.py` 퇴역을 별도 결정

완료 조건: 기록기 실패가 가드의 원래 결과를 바꾸지 않고, 시험 사건이 운영 사건으로 저장되지 않는다.

## T4 — 세션 뒤 LLM 정책 판정

의존: T3.

- [ ] 판정 루브릭과 context bundle 직렬화 고정
- [ ] GuardEvent + 정확한 GuardSpec만 입력하는 판정 실행기
- [ ] 모델·설정·rubric·bundle 해시 기록
- [ ] 새 운영 사건 전수 선택, API 실패 pending, 임의 재샘플링 방지
- [ ] 프롬프트 주입 방어와 `insufficient_context` 테스트
- [ ] API 키는 환경변수로만 읽고 비용 발생 전 사용자 승인 상태 확인

완료 조건: 고정 fixture에서 세 판정값과 pending 경로가 재현되고, 사용자 검토나 집계 정보가 입력 bundle에 포함되지 않는다.

## T5 — 사용자 검토와 가드 결정

의존: T4.

- [ ] 새 운영 사건 전수 검토 큐
- [ ] `useful | unnecessary | uncertain` append 기록
- [ ] 가드별 세션·사건·두 판단 축을 함께 보는 최소 CLI/정적 뷰
- [ ] `keep | modify | remove` 결정과 evidence id 연결
- [ ] modify 시 새 GuardSpec 버전 강제
- [ ] 결정 변경 이력 보존

완료 조건: 시험 데이터로 네 가지 정책/유용성 조합을 모두 표현하고, 둘 이상의 운영 세션 조건을 우회할 수 없다.

## T6 — 보고서와 기술 E2E

의존: T5.

- [ ] 대표 지표와 세 진단 지표를 원수 `N/D`로 계산
- [ ] 분모 0, pending, 소표본, test/unknown 제외 처리
- [ ] 손실·부분 기록·수정 이력 병기
- [ ] 명시적 시험 가드로 전체 E2E 실행
- [ ] 산출물에 `기술 검증용 test evidence` 표시
- [ ] 운영 가치 미검증 상태를 그대로 보고

완료 조건: 자동 테스트·CLI 스모크·실제 파일 산출 검증이 통과하고, 시험 사건이 운영 지표에 0건 포함된다.

## O1 — 자연 운영 관찰

의존: T6. 캘린더 시간 필요.

- [ ] 관찰 시작 전에 종료 조건과 평가 질문 기록
- [?] 같은 가드가 서로 다른 여러 세션에서 자연 발동하기를 기다림
- [ ] 매 세션 뒤 새 운영 사건 전수 정책 판정·사용자 검토
- [ ] 강제 발동으로 표본을 채우지 않았는지 출처 감사

종료 분기:

- 자연 사건 조건 충족 → O2
- 종료 시점까지 조건 미충족 → `운영 빈도 미검증` 기록 후 종료

## O2 — 내부 수명주기 결정과 반증 평가

의존: O1 조건 충족.

- [ ] 한 가드의 `keep | modify | remove` 결정과 근거 기록
- [ ] 결정 가능성·근거 복원 노력·결정 확신의 변화 평가
- [ ] 검토 부담과 얻은 가치 비교
- [ ] 대표/진단 지표를 원수와 함께 동결
- [ ] 성공 또는 가설 실패를 주장 범위 안에서 서술

완료 조건: 최소 사용자 가치 흐름 1건 또는 명시적 가치/채택 가설 실패 결론.

## X1 — 외부 검증

의존: O2. v1 완료에 필수 아님.

- [ ] 이미 지속형 가드를 운영하는 독립 사용자 모집
- [ ] 실제 저장소에서 전체 흐름 사용
- [ ] 검토 부담·결정 변화·이탈 이유 수집
- [ ] 실제 사용 전 일반화 금지

## 커밋 경계

권장 최소 커밋 순서:

1. `docs: redesign reject bench around guard lifecycle decisions`
2. `chore: retire pre-v7 implementation`
3. `feat: define v7 domain records`
4. `feat: version guard context`
5. `feat: capture guard events with provenance`
6. `feat: judge policy consistency after sessions`
7. `feat: review utility and record guard decisions`
8. `feat: report v7 evidence and technical e2e`

관찰 결과는 구현 커밋과 분리한다. 커밋과 push는 별개이며, 외부 push는 사용자가 요청할 때만 한다.
