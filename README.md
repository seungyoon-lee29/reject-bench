# Reject Bench

AI가 짠 코드를 기각하는 검증 도구 + 실제 개발 과정의 승인/기각 로그.

현재 상태: **수집기 v0만 동작 중.** 본 설계는 `기획-입력.md`를 입력으로 기획 하네스에서 만든다.

## 수집기 (설치 완료 2026-08-04)

전역 `~/.claude/settings.json`에 훅 4개가 등록돼 있고, 모든 Claude Code 세션에서 이벤트가 자동으로 쌓인다.

| 이벤트 | matcher | 의미 |
|---|---|---|
| `PostToolUse` | `Edit\|Write\|MultiEdit\|NotebookEdit` | AI 제안이 **적용됨** (승인) |
| `PermissionDenied` | `*` | 사용자가 **기각함** ← 핵심 신호 |
| `PostToolUseFailure` | `*` | 도구 실패 (테스트 미통과 등 간접 기각 신호) |
| `PermissionRequest` | `*` | 승인 요청 발생 (분모 계산용) |

- 수집 스크립트: `hooks/collect.py`
- 적재 위치: `data/events.jsonl` (gitignore 대상)
- 모든 훅은 `async: true`, `timeout: 5` — 세션을 블로킹하지 않는다.

### 기록하는 것 / 하지 않는 것

`collect.py`의 설계 원칙은 **원문을 저장하지 않는 것**이다. 전역 규칙 "비밀은 어디에도 평문 금지"를 훅 단계에서 지킨다.

**기록함**: 타임스탬프 · 세션 ID · 프로젝트 폴더명 · 도구명 · 파일 경로(홈은 `~`로 축약) · Bash 실행 파일명만 · 변경 크기(문자 수)/edit 개수 · 마스킹·절단된 에러 메시지

**기록 안 함**: 파일 내용 · Bash 명령어 인자 · 프롬프트 · 도구 응답 본문

에러 메시지는 24자 이상의 토큰류 문자열을 `<redacted>`로 치환하고 200자로 자른다. 이 마스킹은 의도적으로 과하게 잡혀 있어서 파일 경로도 함께 가려질 수 있다 — 분류 신호는 `tool`·`cmd`·`path` 필드가 담당하므로 감수한다.

### 레코드 예시

```json
{"schema":"v0","ts":"2026-08-04T08:15:29+00:00","event":"PostToolUse","session_id":"...",
 "project":"reply-gate","tool":"Write","path":"~/workspace/reply-gate/src/gate.py","size":1420}
```

`tag` 필드(기각 사유: 허위 API / 테스트 미통과 / 과잉 구현 / 보안)는 수집 시점에 비어 있다. 사후에 채우거나, v1에서 자동 분류를 붙인다.

### 스키마 변경

`SCHEMA` 상수를 올리고 기존 줄은 건드리지 않는다. 읽는 쪽에서 `schema` 필드로 분기한다. v0 로그를 재작성하지 말 것 — 로그의 가치는 원본성이다.

### 제거·복원

훅을 끄려면 `~/.claude/settings.json`의 `hooks`에서 `reject-bench/hooks/collect.py`를 부르는 항목 4개를 지운다.
설치 직전 백업: `~/.claude/settings.json.pre-reject-bench`

## 다음 단계

`기획-입력.md`를 입력으로 기획 하네스를 돌려 검증 에이전트·MCP 서버·대시보드를 설계한다. 로그는 그동안 계속 쌓인다.
