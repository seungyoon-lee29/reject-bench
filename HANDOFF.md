# 핸드오프 — 증거 조회 MCP 서버 3-doc 완성, 사용자 승인 대기 (2026-08-29)

## 지금 하던 것

`/dryforge:ready`로 다음 증분(증거 조회 MCP 서버)의 계약 문서 3종을 새로 작성했다: `.dryforge/{spec,plan,handoff}.md`. 직전 v1 주기 문서는 `.dryforge/001/`로 이동(아카이브, git mv 스테이지 상태). 설계 결정 4건은 사용자가 직접 내림 — ① 도구 3종(가드 목록·가드별 증거·전체 보고서) ② 세션은 응답 내 별칭 S1·S2 ③ 등록은 이 저장소 `.mcp.json`에만 ④ 공식 MCP SDK 의존성 추가(stdlib 권고 기각). 독립 감사 2회 통과: 의도 완결성 감사(사용자 추가 질문 0건, spec 반영 의무 3건 반영), 3-doc 게이트(차단 0건, 조언 3건 반영 — 교정 상태 값 집합 `passed|failed|none` 열거, 보고서 동일성 비교의 시각 정규화 명시, 기동 확인 도구 `list_guards` 고정).

## 다음 할 일 (구체적 첫 행동 1개)

**사용자 승인 후 같은 세션에서 `/dryforge:go` 실행.** 그 전에 go의 선행 조건인 깨끗한 작업 트리를 위해 스테이지된 아카이브 이동분(`.dryforge/001/`)과 이 HANDOFF 변경분을 커밋해야 한다(커밋은 사용자 승인 필요).

## 미결 결정

1. **3-doc 승인** — 승인 시 go, 수정 요청 시 해당 문서 수정
2. **아카이브 이동·HANDOFF 커밋** — go 선행 조건(작업 트리 정리)
3. **검토 큐 2건 처리** — `ev-e7673c61…`·`ev-94f0ca1c…`(둘 다 `block-dangerous-git` 자기차단). `review demote --reason`으로 test 강등 또는 유용성 판정
4. **reply-gate 배포 잔여 작업** — 1차 관측 무대. `protect-live-reports` 발동 0건이라 O1이 이 작업 없이는 진전 없음
5. **v0 `hooks/collect.py` 퇴역** — 병행 배선 확인 후 별도 커밋(`docs/배선-목록.md` cutover 절)
6. **판정 첫 과금 호출 승인** — `judge --approve-billing` 시점(기본 gpt-5-mini)
7. **reply-gate `.claude/settings.json` 변경분 커밋** — 그 저장소에서 비커밋
8. **외부 검증(X1)** — O2 뒤에만

## 함정

- `dryforge:ready`/`go`는 사용자 직접 실행 전용 — 에이전트가 호출 불가
- 이 저장소는 `.dryforge/` 계약 문서 3종을 git 추적(`worktrees/`·`status.json`만 제외) — go 기본 gitignore 규칙 적용 금지
- 배선이 살아 있다 — 시험·강제 발동은 반드시 `REJECTBENCH_TEST_SESSION` 아래에서. MCP 픽스처에 위험 패턴 텍스트를 쓸 땐 heredoc 금지, 파일 도구로(전역 가드 자기차단)
- 배선·등록 명령은 `/Users/ian/.local/bin/uv run --project …` 절대 경로 필수
- 구현층 규칙·함정 정본은 `rejectbench/AGENTS.md`(산입·post-remove 파생 정본, 교정 사이드카 `calibration.jsonl`, modify 시 `--enforcement-script` 필수 등)
- `enforcement_ref.script_path`가 홈 절대 경로로 저장돼 있음 — MCP 정화 경계가 이 값도 치환해야(새 spec §5)
- 첫 자연 사건은 도구 보고서 열람 전 transcript+git 기준선 측정(`docs/관찰-프로토콜.md`)
- `.claude/settings.json.bak-*`, `.codex/config.toml.bak-*`는 사용자 소유 비추적 백업 — 건드리지 않는다
- `docs/문제정의/01~05`·심사 스킬은 보존 결정 — 폴더 정리로 지우지 않는다
