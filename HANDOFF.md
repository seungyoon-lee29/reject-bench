# 핸드오프 — Dryforge 002 리뷰 수정 완료, PR 생성 전 (2026-09-01)

## 지금 하던 것

PR #1은 `main`의 `b4c88032847bd870dcc975a3b8d358d3a806455e`로 병합됐다. 병합 뒤 리뷰에서 확인된 MCP 조회 표면 결함을 `fix/dryforge-002-review` 브랜치의 구현 커밋 `933045c7932be39063e3b63a9b07f8e18f6b21be`로 수정했다. 이 브랜치는 아직 원격에 올리지 않았다.

수정 범위:

- 홈 경로를 품은 세션 ID는 홈 치환 전에 별칭화하고, `e`·`-` 같은 짧은 ID가 JSON 키·날짜·경로·도메인 값을 훼손하지 않도록 값 전체·완전한 토큰 경계를 적용했다. 고정 JSON 키는 공개 스키마로 보존한다.
- 세 도구의 예상 밖 예외를 정적 한 줄 `ToolError`로 바꿔 SDK 로그·stderr에 traceback, 저장 경로, 홈 경로가 새지 않게 했다. 미등록 가드와 없는 버전도 호출자 입력을 되비추지 않는다.
- `.mcp.json`에 `PYTHONDONTWRITEBYTECODE=1`을 넣어 등록 서버 기동이 저장소에 `__pycache__`·`.pyc`를 만들지 않게 했다.
- `.dryforge/002/` 계약의 구현 diff 예외를 명확히 하고, 아카이브 뒤 깨진 `.dryforge/{spec,plan,handoff}.md` 정본 참조를 `001/`·`002/`로 고쳤다.
- Standards/Spec 두 SubAgent가 독립 리뷰한 뒤 서로의 반례를 소크라테스식으로 검토했다. 최종 결과는 Standards hard finding 0·smell 0, Spec 행동 결함 0·scope creep 0이다. 마지막 P3 문서 표현도 `.dryforge/002/plan.md`에서 정본과 맞췄다.

검증: `uv run pytest` **439 passed in 6.72s**, `uv lock --check` exit 0, `git diff --check` exit 0, `AGENTS.md == CLAUDE.md`, `.mcp.json` JSON 파싱 성공, 001/002 정본 대상 존재 확인. 테스트와 공개 MCP 재현은 임시 store만 썼고, 운영 `guard_evidence`·`get_report`는 호출하지 않았다.

## 다음 할 일 (구체적 첫 행동 1개)

`git push -u origin fix/dryforge-002-review`로 구현 커밋과 이 HANDOFF 후속 커밋을 올린 뒤 `main` 대상 PR을 연다. 병합 뒤 첫 자연 사건 기준선은 `docs/관찰-프로토콜.md`의 transcript+git 절차로 먼저 측정하고, 그 전에는 가드별 증거·전체 보고서를 조회하지 않는다.

## 미결 결정

1. **적재 시점 4000자 절단의 잘린 경로 조각** — `recorder.py`가 `reason`을 4000자로 자를 때 홈 경로나 세션 ID 경계가 잘리면 조회 경계가 원문 전체를 인식하지 못할 수 있다. 기존 모듈 범위라 이번 수정에서는 건드리지 않았다.
2. **세션 ID 문법 또는 typed provenance** — 현 스키마는 임의 non-empty 문자열을 허용한다. 출력 시점에는 `prefix<session-id>suffix`가 실제 세션 언급인지 우연한 일반 텍스트인지 구별할 수 없어, 현재 계약은 값 전체·완전한 토큰만 별칭화한다. 임의 부분문자열까지 원문 0회를 절대 보장하려면 적재 계약을 먼저 강화해야 한다.
3. **검토 큐 2건** — 이전 HANDOFF가 기록한 `block-dangerous-git` 자기차단 2건은 기준선 오염 방지를 위해 이번 세션에서 재조회하지 않았다. 기준선 뒤 `review demote --reason` 또는 유용성 판정을 결정한다.
4. **reply-gate 배포 잔여 작업** — 1차 관측 무대이며 `protect-live-reports` 자연 발동 관측이 필요하다.
5. **v0 `hooks/collect.py` 퇴역** — 병행 배선 확인 뒤 별도 커밋으로 처리한다.
6. **판정 첫 과금 호출 승인** — `judge --approve-billing`은 별도 사용자 승인 전 실행하지 않는다.
7. **reply-gate `.claude/settings.json` 변경분 커밋** — 해당 저장소에서 아직 처리할 일이다.
8. **MCP 서버 전역 등록 확장** — 첫 자연 사건 기준선 측정 뒤에만 판단한다. 현재는 저장소 로컬 등록뿐이다.
9. **외부 검증(X1)** — O2 뒤에만 진행한다.

## 함정

- 첫 자연 사건 기준선 전에는 `guard_evidence`·`get_report`를 호출하지 않는다. 등록 확인용 기동과 `list_guards`만 허용된다.
- 테스트는 반드시 임시 store만 쓰고, 시험·강제 발동은 `REJECTBENCH_TEST_SESSION` 아래에서만 한다. 판정 과금 호출은 `--approve-billing` 승인 전 금지다.
- 세션 별칭 경계는 값 전체·완전한 `\w` 토큰이다. 단어에 붙은 동일 부분문자열은 증거 값 보존을 위해 바꾸지 않으며, 일반 값 전체·토큰이 세션 ID와 우연히 같으면 비노출을 우선해 별칭화한다. 이 모호성을 길이·구두점 휴리스틱으로 다시 덮지 않는다.
- 예상 밖 도구 예외에는 정적 사유만 노출한다. 서버 예외 객체나 호출자 `guard_id`·`version`을 진단 편의로 다시 echo하지 않는다.
- 저장소 로컬 MCP 등록의 `PYTHONDONTWRITEBYTECODE=1`을 제거하면 읽기 전용 기동이 저장소에 바이트코드를 만들 수 있다.
- `.dryforge/`의 완료 주기는 번호 폴더(`001/`, `002/`)에 있고 진행 중 주기만 루트에 둔다. 현재 루트에 진행 중 Dryforge 계약은 없다.
- `.claude/settings.json.bak-1786598695`, `.codex/config.toml.bak-1786598644`는 사용자 소유 비추적 백업이다. 스테이징·수정·삭제하지 않는다.
- 현재 브랜치에는 구현 커밋과 이 HANDOFF 후속 커밋 두 개만 있으며 아직 push하지 않았다.
