# Reject Bench

AI가 짠 코드를 기각하는 검증 도구 + 실제 개발 과정의 승인/기각 로그.

현재 상태: **수집기 v0만 동작 중.** 본 설계는 `기획-입력.md`를 입력으로 기획 하네스에서 만든다.

## 세션 연속성

Claude Code의 컨텍스트가 차서 auto-compact되거나 사용자가 `/compact`를 실행하면 다음 순서가 자동으로 이어진다.

1. `PreCompact`가 `HANDOFF.md`, 현재 branch·HEAD, `git status --short`를 OS 임시 디렉터리의 세션별 스냅샷에 원자적으로 저장한다.
2. Claude Code가 자체 compact를 수행한다.
3. compact가 만든 `SessionStart(source=compact)`가 직전 스냅샷을 컨텍스트에 다시 주입하고 기존 작업을 계속한다.
4. 세션 종료 시 해당 임시 스냅샷을 지운다. 비정상 종료로 남은 파일은 다음 실행에서 7일이 지나면 정리한다.

Codex에서는 compact보다 HANDOFF 갱신이 먼저다. 현재 유효 컨텍스트 창 기준 약 40%가 남으면 `Stop` 훅이 `HANDOFF.md` 갱신을 강제한다. 변경 해시가 확인된 뒤 약 30% 잔여에서 `PreCompact`가 스냅샷을 저장하고 compact를 허용한다. 갱신되지 않았거나 스냅샷 저장에 실패하면 manual/auto compact를 모두 차단한다.

공통 스냅샷 구현은 `hooks/continuity.py`, Codex 선행 게이트는 `hooks/codex_handoff_gate.py`, 설정은 `.claude/settings.json`·`.codex/config.toml`, 회귀 검증은 각 파일의 `.test.py`다. 자동 스냅샷은 transcript·프롬프트·도구 출력을 저장하지 않는다. Codex 게이트는 transcript의 최신 숫자형 토큰 사용량만 읽는다. compact 실행은 각 실행기 자체 기능에 맡기며 훅에서 별도 모델을 호출하지 않는다.

## 수집기 (설치 완료 2026-08-04)

전역 `~/.claude/settings.json`에 훅 4개가 등록돼 있고, 모든 Claude Code 세션에서 이벤트가 자동으로 쌓인다.

| 이벤트 | matcher | 설치 당시 의도한 의미 | 실측 결과 (아래 참조) |
|---|---|---|---|
| `PostToolUse` | `Edit\|Write\|MultiEdit\|NotebookEdit` | AI 제안이 **적용됨** (승인) | 948건. 유일하게 의도대로 동작 |
| `PermissionDenied` | `*` | 사용자가 **기각함** ← 핵심 신호 | **2건(0.2%). 사실상 비어 있음** |
| `PostToolUseFailure` | `*` | 도구 실패 (테스트 미통과 등 간접 기각 신호) | 33건 중 22건이 조회성 명령의 exit≠0 |
| `PermissionRequest` | `*` | 승인 요청 발생 (분모 계산용) | 39건 중 33건이 `AskUserQuestion` |

- 수집 스크립트: `hooks/collect.py`
- 적재 위치: `data/events.jsonl` (gitignore 대상)
- 모든 훅은 `async: true`, `timeout: 5` — 세션을 블로킹하지 않는다.

### 실측 검증 (2026-08-10) — 위 설계 의도 3개가 틀렸다

수집 6일 · 1,022건을 분석한 결과, **수집기는 정상 작동했지만 목표한 신호를 하나도 잡지 못했다.** 전체 분석은 [`docs/문제정의/02-리서치-실데이터.md`](docs/문제정의/02-리서치-실데이터.md).

1. **`PermissionDenied`는 기각의 대리 지표가 아니다.** 6일간 2건뿐이고 둘 다 Bash `cd`였다. **Edit/Write 제안이 permission 다이얼로그에서 기각된 사례는 0건.** 실제 기각은 다이얼로그가 아니라 ①적용 후 대화로 수정 지시 ②생성 중 Esc 중단 ③acceptEdits라 다이얼로그 자체가 안 뜸 ④계약서 단계에서 상류로 걸러짐 — 훅이 보지 않는 곳에서 일어난다.
2. **`PermissionRequest`는 분모가 될 수 없다.** 39건 중 **33건(85%)이 `AskUserQuestion`** — AI가 사용자에게 질문한 것이지 코드 변경 승인을 요청한 것이 아니다. `Edit`/`Write`에 대한 `PermissionRequest`는 0건.
3. **`PostToolUseFailure`는 신호 대 잡음비가 1:2다.** 33건 중 22건(67%)이 `ls`·`grep`·`wc`·`cat` 등 조회성 명령의 exit≠0으로, 실제 출력은 정상이다. 실행성 명령의 실패는 11건뿐.

부수적으로 확인된 결함 둘:

4. **`project` 필드(= cwd basename)는 집계에 쓸 수 없다.** git worktree가 별도 프로젝트로 둔갑하고(`T5`, `.dryforge`), cwd와 편집 대상이 다른 경우를 전부 오분류한다. 경로 정규화가 필요하다.
5. **`Edit`에는 변경 규모가 기록되지 않는다.** `size`는 `Write`에만 채워져 `Edit` 799건 중 보유 0건. 같은 파일 재편집 간격 중앙값이 12초(71%가 30초 이내)라 "AI가 한 변경을 여러 호출로 쪼갠 것"과 "사람이 물리고 다시 시킨 것"을 **구분할 방법이 없다.**

**이 결함들을 알고도 수집기를 끄지 않는다.** 스키마를 고칠 때는 `SCHEMA` 상수를 올리고 기존 줄은 그대로 둔다 — v0 로그를 재작성하지 않는다. 실패한 계측이라도 원본이어야 증거가 된다.

**뜻밖의 수확**: `error` 필드 안에, 이 저장소가 아니라 **reply-gate 프로젝트에 심어둔 가드가 AI의 실행을 거부한 기록 2건**이 우연히 잡혔다. 기각한 주체가 사람도 다이얼로그도 아니라 프로젝트에 심은 가드이고, 사유가 자연어 + 도메인 근거로 남아 있다. `ERR_MAX=200`자 절단에 걸려 뒷부분은 잘렸다. v1이 잡아야 할 형태가 여기 있다.

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

**문제정의를 다시 하는 중이다** (2026-08-10~). 위 실측 검증이 `기획-입력.md`의 확정 사항 1("로그가 본체")과 성공 지표("AI 제안 N건 중 기각률 X%")를 직접 흔들었기 때문이다. 기획 하네스(`/ready`)에 넣기 전에 문제를 다시 세운다.

절차와 진행 상태: [`docs/문제정의/00-계획.md`](docs/문제정의/00-계획.md) · 현재 위치: [`HANDOFF.md`](HANDOFF.md).

로그는 그동안 계속 쌓인다.
