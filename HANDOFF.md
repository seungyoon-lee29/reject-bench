# 핸드오프 — 증거 조회 MCP 서버 구현 완료, 통합 방식 대기 (2026-08-29)

## 지금 하던 것

증거 조회 MCP 증분(T7)을 `dryforge/mcp-evidence-server` 브랜치에서 구현 완료했다. 커밋 12건, 신규 파일 3개(`rejectbench/mcp_server.py`, `tests/test_mcp_server.py`, `.mcp.json`):

- `ab50a92` 스캐폴드 — `uv add mcp`(mcp 2.1.1). 런타임 의존성 0 상태는 여기서 끝났다(spec §7, 사용자 결정).
- `df2042d`(머지 `b60d4e0`) T7-1 — 서버 모듈·정화 경계·테스트. `57327fb` T7-2 — 저장소 루트 `.mcp.json` 등록.
- `e28b120`(머지 `2a865c3`), `97b9c27`(머지 `a5890e2`) 및 후속 수정 — 독립 검토 3회에서 나온 코드 차단 3건·조언 다수 반영.
- `ecba33d`, `cc811fa`, `77299e1` 및 후속 문서 커밋 — 모듈 규칙·진입 문서·README·핸드오프. 검토가 지적한 문서 차단 2건(적재 스냅샷 규칙, 기준선 규율의 위치)은 이 커밋들에서 닫혔다.

현재 규모: `rejectbench/mcp_server.py` 741줄, `tests/test_mcp_server.py` 1,240줄(신규 74건). 기존 모듈은 한 줄도 바뀌지 않았다.

검증: `uv run pytest` exit 0, **431건**(기존 357 + 신규 74). 등록 항목 그대로 저장소 밖 작업 디렉터리에서 stdio 하위 프로세스를 띄워 `tools/list`(3종) → `list_guards` 왕복 1건 성공, 호출 전후 운영 store 불변, 잔여 프로세스 없음.

독립 검토 3회에서 닫은 차단 사항: ① SDK 인자 검증 오류가 정화 경계를 우회해 호출자 입력을 그대로 되비추던 경로 ② 보고서 응답의 별칭표가 보고서 본문보다 **이전** 스냅샷에서 만들어지던 순서 ③ 오류 메아리를 정화 **전에** 잘라 잘린 경로·식별자 조각이 살아남던 문제 ④ 자릿수 한도(4300)를 넘는 십진수 `version`이 `int()`에서 터져 예외가 도구 밖으로 새던 경로 ⑤ 문서가 실제와 어긋나던 적재 스냅샷 규칙 ⑥ 기준선 측정 규율이 임시 문서에만 있고 진입 문서에는 없던 문제(지금은 `CLAUDE.md` 비협상 ⑦).

## 다음 할 일 (구체적 첫 행동 1개)

**사용자에게 통합 방식을 물어 그대로 실행한다** — main 병합(`--no-ff`) / PR·push / 브랜치 유지 중 택1. 병합을 택하면 그 뒤 `.dryforge/{handoff,spec,plan}.md`를 `.dryforge/002/`로 이동(아카이브)한다.

## 미결 결정

1. **통합 방식** — main 병합 / PR / 브랜치 유지. main은 origin보다 1커밋 앞서 있다(3-doc 커밋 `56f2012`, 미push)
2. **오류 텍스트가 호출자가 준 `guard_id`를 되비추는 것** — 정화는 거치지만 호출자가 경로를 넣으면 `~/…`로 돌아온다. 서버 쪽 경로는 절대 노출되지 않는다. 진단 편의를 택할지 되비춤을 뺄지 결정
3. **적재 시점 4000자 절단의 잘린 경로 조각** — `recorder.py`가 기록 시점에 `reason`을 4000자로 자른다. 홈 경로나 세션 식별자가 그 경계에 걸치면 부분 문자열로 저장돼 조회 시점 정화 경계가 잡지 못한다. 이번 증분 범위 밖(기존 모듈)이라 손대지 않았다 — v1 본체 수정 주기에서 처리할지 결정
4. **검토 큐 2건 처리** — `ev-e7673c61…`·`ev-94f0ca1c…`(둘 다 `block-dangerous-git` 자기차단). `review demote --reason`으로 test 강등 또는 유용성 판정
5. **reply-gate 배포 잔여 작업** — 1차 관측 무대. `protect-live-reports` 발동 0건이라 O1이 이 작업 없이는 진전 없음
6. **v0 `hooks/collect.py` 퇴역** — 병행 배선 확인 후 별도 커밋(`docs/배선-목록.md` cutover 절)
7. **판정 첫 과금 호출 승인** — `judge --approve-billing` 시점(기본 gpt-5-mini)
8. **reply-gate `.claude/settings.json` 변경분 커밋** — 그 저장소에서 비커밋
9. **MCP 서버 전역 등록 확장** — 기준선 측정 1회를 마친 뒤에만 판단(지금은 저장소 로컬 등록만)
10. **외부 검증(X1)** — O2 뒤에만

## 함정

- `dryforge:ready`/`go`는 사용자 직접 실행 전용 — 에이전트가 호출 불가
- 이 저장소는 `.dryforge/` 계약 문서 3종을 git 추적(`worktrees/`·`status.json`만 제외) — go 기본 gitignore 규칙 적용 금지
- 배선이 살아 있다 — 시험·강제 발동은 반드시 `REJECTBENCH_TEST_SESSION` 아래에서. 픽스처에 위험 패턴 텍스트를 쓸 땐 heredoc 금지, 파일 도구로(전역 가드 자기차단)
- `uv run --project`는 작업 디렉터리를 바꾸지 않는다 — `package = false`라 저장소 밖에서 실행하면 `python -m rejectbench.*`가 모듈을 못 찾는다. `.mcp.json`은 `env.PYTHONPATH`로 이 의존을 없앴다
- 설치된 SDK는 mcp 2.x — v1의 `FastMCP`는 `MCPServer`로 이름이 바뀌었고, `ToolError`에는 `Error executing tool <이름>: ` 접두가 붙는다. 예상 못 한 예외가 새면 SDK가 사유를 지워 호출자가 단서를 못 받는다
- 구현층 규칙·함정 정본은 `rejectbench/AGENTS.md`(산입·post-remove 파생 정본, 교정 사이드카 `calibration.jsonl`, 조회 표면의 단일 출력 경계, modify 시 `--enforcement-script` 필수 등)
- `get_report` 동일성 테스트는 §4.3(보고서 원문 그대로)과 §5(정화)가 부딪히는 순간 일부러 red가 된다 — 보고서에 세션 식별자가 실리면 그때 둘 중 하나를 골라야 한다
- 첫 자연 사건은 도구 보고서 열람 전 transcript+git 기준선 측정(`docs/관찰-프로토콜.md`). **전역 등록을 피한 것으로 이 위험이 다 사라지지는 않는다** — 전역 가드의 관측 범위는 모든 저장소 세션이고 지금까지의 운영 사건 2건은 둘 다 이 저장소에서 났다. 이 저장소 세션이 기준선 복원 전에 가드별 증거·보고서를 부르면 같은 규율과 부딪힌다(규칙 정본은 `CLAUDE.md` 비협상 ⑦)
- `.claude/settings.json.bak-*`, `.codex/config.toml.bak-*`는 사용자 소유 비추적 백업 — 건드리지 않는다
- `docs/문제정의/01~05`·심사 스킬은 보존 결정 — 폴더 정리로 지우지 않는다
