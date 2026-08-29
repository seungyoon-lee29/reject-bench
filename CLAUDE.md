# Reject Bench — 프로젝트 규칙

## 핸드오프 (세션 연속성)

`HANDOFF.md`가 세션 간 유일한 인수인계 지점이다. Claude Code는 `.claude/settings.json`, Codex는 `.codex/config.toml`의 훅으로 auto/manual compact 직전에 이 파일과 Git 상태를 OS 임시 디렉터리에 스냅샷으로 남긴다. 이어지는 `SessionStart(source=compact)` 훅은 그 스냅샷을 새 컨텍스트에 자동 주입하고 작업을 계속하게 한다. 일반 시작·resume에서는 현재 `HANDOFF.md`를 주입한다.

Codex는 반드시 `HANDOFF.md` 갱신 → 변경 해시 확인 → 스냅샷 저장 → compact 순서로 진행한다. 현재 모델의 유효 컨텍스트 창 258,400 토큰을 기준으로 약 40%가 남으면 `Stop` 훅이 HANDOFF 갱신을 먼저 강제하고, 180,880 토큰 사용 시점(약 30% 잔여)에 auto compact한다. 새 HANDOFF 해시가 없거나 스냅샷 저장이 실패하면 `PreCompact`가 compact를 차단한다. 모델의 유효 컨텍스트 창이 바뀌면 `.codex/config.toml`의 절대 토큰 임계값도 다시 계산한다.

**갱신 시점** — 아래 순간에는 진행 중 작업보다 갱신이 우선한다:

1. 단계·마일스톤이 끝났을 때
2. 방향을 바꾸는 결정이 난 직후
3. **컨텍스트 부족·auto-compact 임박 경고가 보일 때 — 다른 무엇보다 먼저 갱신한다**

**형식**: 지금 하던 것 / 다음 할 일(구체적 첫 행동 1개) / 미결 결정 / 함정. 파일 경로·커밋 해시로 구체적으로 쓴다. 지난 이력은 git log가 담당하므로 현재 상태만 유지하고, 낡은 섹션은 지운다.

자동 스냅샷은 대화 transcript·프롬프트·도구 출력을 복사하지 않는다. `HANDOFF.md`, branch, HEAD, `git status --short`만 저장한다. Codex 선행 게이트가 transcript에서 읽는 값도 최신 `token_count`의 숫자 필드뿐이다. compact 자체는 각 실행기가 수행하며 훅이 중첩 Claude/Codex 프로세스나 `/compact` 명령을 실행하지 않는다.

## 문제정의 단계 산출물

`docs/문제정의/`에 번호 순서로 쌓는다. 절차·완료 기준의 정본은 `docs/문제정의/00-계획.md`.

## 구현층 (v7)

도메인 구현은 `rejectbench/` 패키지 하나이며, 모듈 경계·불변식·함정은 `rejectbench/AGENTS.md`가 정본이다. 행동 계약은 `.dryforge/spec.md`, 검증 게이트는 `uv run pytest` 하나다(연속성 훅 회귀 포함).

기록·판정·검토·결정은 CLI로만 한다. 조회는 저장소 로컬 `.mcp.json`에 등록된 읽기 전용 MCP 서버로도 할 수 있다.

작업 전 최소 비협상 사항: ① 기록 실패가 가드 발동 결과를 바꾸면 안 된다 ② 테스트는 임시 디렉터리만 — 운영 `data/`에 닿으면 하드 실패 ③ 시험·강제 발동은 반드시 `REJECTBENCH_TEST_SESSION` 아래에서 ④ 판정 API 과금 호출은 승인 게이트(`--approve-billing`) 없이는 불가 ⑤ 비밀·명령 전문·파일 전문은 어떤 레코드에도 저장하지 않는다 ⑥ 조회 표면은 쓰기 도구가 없고, 응답으로 나가는 모든 문자열이 홈 경로와 세션 식별자를 지우는 출력 경계를 지난다 ⑦ **첫 자연 사건의 기준선 측정을 마치기 전에는 가드별 증거·전체 보고서를 조회하지 않는다** — 도구 보고서를 먼저 읽으면 `docs/관찰-프로토콜.md`가 요구하는 transcript+git 복원이 오염된다(가드 목록 조회와 등록 확인용 기동 점검은 무방).
