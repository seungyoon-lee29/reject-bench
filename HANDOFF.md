# 핸드오프 — 증거 조회 MCP 서버 구현 완료, 통합 방식 대기 (2026-08-29)

## 지금 하던 것

증거 조회 MCP 증분(T7)을 `dryforge/mcp-evidence-server` 브랜치에서 구현 완료했다. 커밋 3건:

- `ab50a92` 스캐폴드 — `uv add mcp`(mcp 2.1.1). 런타임 의존성 0 상태는 여기서 끝났다(spec §7, 사용자 결정).
- `df2042d` (머지 `b60d4e0`) T7-1 — `rejectbench/mcp_server.py`(447줄) + `tests/test_mcp_server.py`(626줄, 신규 22건). 기존 파일 무변경.
- `57327fb` T7-2 — 저장소 루트 `.mcp.json` 등록 1건.

검증: `uv run pytest` exit 0, 379건(기존 357 + 신규 22). 등록 항목 그대로 저장소 밖 작업 디렉터리에서 stdio 하위 프로세스를 띄워 `tools/list`(3종) → `list_guards` 왕복 1건 성공, 호출 전후 운영 store 불변, 잔여 프로세스 없음.

문서 갱신: `rejectbench/AGENTS.md`(모듈 맵·불변식·함정), `CLAUDE.md`/`AGENTS.md`(구현층 비협상 ⑥), `README.md`(조회 표면 절), `.dryforge/plan.md` 체크박스 8건.

## 다음 할 일 (구체적 첫 행동 1개)

**사용자에게 통합 방식을 물어 그대로 실행한다** — main 병합(`--no-ff`) / PR·push / 브랜치 유지 중 택1. 병합을 택하면 그 뒤 `.dryforge/{handoff,spec,plan}.md`를 `.dryforge/002/`로 이동(아카이브)한다.

## 미결 결정

1. **통합 방식** — main 병합 / PR / 브랜치 유지. main은 origin보다 1커밋 앞서 있다(3-doc 커밋 `56f2012`, 미push)
2. **오류 텍스트가 호출자가 준 `guard_id`를 되비추는 것** — 정화는 거치지만 호출자가 경로를 넣으면 `~/…`로 돌아온다. 서버 쪽 경로는 절대 노출되지 않는다. 진단 편의를 택할지 되비춤을 뺄지 결정
3. **검토 큐 2건 처리** — `ev-e7673c61…`·`ev-94f0ca1c…`(둘 다 `block-dangerous-git` 자기차단). `review demote --reason`으로 test 강등 또는 유용성 판정
4. **reply-gate 배포 잔여 작업** — 1차 관측 무대. `protect-live-reports` 발동 0건이라 O1이 이 작업 없이는 진전 없음
5. **v0 `hooks/collect.py` 퇴역** — 병행 배선 확인 후 별도 커밋(`docs/배선-목록.md` cutover 절)
6. **판정 첫 과금 호출 승인** — `judge --approve-billing` 시점(기본 gpt-5-mini)
7. **reply-gate `.claude/settings.json` 변경분 커밋** — 그 저장소에서 비커밋
8. **MCP 서버 전역 등록 확장** — 기준선 측정 1회를 마친 뒤에만 판단(지금은 저장소 로컬 등록만)
9. **외부 검증(X1)** — O2 뒤에만

## 함정

- `dryforge:ready`/`go`는 사용자 직접 실행 전용 — 에이전트가 호출 불가
- 이 저장소는 `.dryforge/` 계약 문서 3종을 git 추적(`worktrees/`·`status.json`만 제외) — go 기본 gitignore 규칙 적용 금지
- 배선이 살아 있다 — 시험·강제 발동은 반드시 `REJECTBENCH_TEST_SESSION` 아래에서. 픽스처에 위험 패턴 텍스트를 쓸 땐 heredoc 금지, 파일 도구로(전역 가드 자기차단)
- `uv run --project`는 작업 디렉터리를 바꾸지 않는다 — `package = false`라 저장소 밖에서 실행하면 `python -m rejectbench.*`가 모듈을 못 찾는다. `.mcp.json`은 `env.PYTHONPATH`로 이 의존을 없앴다
- 설치된 SDK는 mcp 2.x — v1의 `FastMCP`는 `MCPServer`로 이름이 바뀌었고, 도구 오류 텍스트에 SDK가 `Error executing tool <이름>: ` 접두를 붙인다
- 구현층 규칙·함정 정본은 `rejectbench/AGENTS.md`(산입·post-remove 파생 정본, 교정 사이드카 `calibration.jsonl`, 조회 표면의 단일 출력 경계, modify 시 `--enforcement-script` 필수 등)
- `get_report` 동일성 테스트는 §4.3(보고서 원문 그대로)과 §5(정화)가 부딪히는 순간 일부러 red가 된다 — 보고서에 세션 식별자가 실리면 그때 둘 중 하나를 골라야 한다
- 첫 자연 사건은 도구 보고서 열람 전 transcript+git 기준선 측정(`docs/관찰-프로토콜.md`)
- `.claude/settings.json.bak-*`, `.codex/config.toml.bak-*`는 사용자 소유 비추적 백업 — 건드리지 않는다
- `docs/문제정의/01~05`·심사 스킬은 보존 결정 — 폴더 정리로 지우지 않는다
