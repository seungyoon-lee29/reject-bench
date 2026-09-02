# 핸드오프 — 003 주기 병합·아카이브 완료, 관측 대기 (2026-09-02)

## 지금 하던 것

003 주기(운영 경험 개선 증분)의 **E0~E7 여덟 태스크가 전부 끝났고 코드리뷰 반영까지 마쳤다.** **PR #8이 main에 병합됐고(`108f62c`) `.dryforge/` 루트 3종은 `003/`로 아카이브했다.** 무엇을 왜 바꿨는지는 `git log`의 커밋 본문이 정본이고, 체크박스는 `.dryforge/003/plan.md`(전부 `[x]`), 계약은 `.dryforge/003/{spec,plan,handoff}.md`다. 진행 중 주기는 없다.

**코드리뷰(2026-09-02)**: 앞선 리뷰 세션은 rate limit로 E1·E2(`73070dd`)까지만 단독 분석했다. 이 세션이 E3·E4·E7을 에이전트 둘로 재리뷰해 합쳤다. 반영: 절단점 바로 뒤가 공백이면 온전한 단위를 버리던 결함(spec §3.1 문면 개정), `HOME` 끝 슬래시 정규화·주입 env만 보는 `_home_path`(조회 경계도 같은 규칙), `_blind_truncate` 병합·도달 불가 분기 제거, 비율 리터럴 고정 테스트 삭제(사용자: 하드코딩 불필요), report의 O(n²)·중복 계산·버전 문자열 정렬, spec §9.1 파티션 키 한계·§9.6 파티션 안 근거 규칙 명문화 + 회귀 테스트 2건, 프로토콜 변경 기록 ⑨(rejudge 순서). 미반영(사용자 결정 — 다음 주기): 미결 8·9·10.

완료 게이트 상태: `uv run pytest` 535건 exit 0 · E3 임시 store 실기동 왕복 1건 · E0·E4 읽기 조회 캡처(`docs/배선-목록.md`) · 정본 3종 갱신 + `diff CLAUDE.md AGENTS.md` exit 0 · 운영 `data/` 읽기만 · 병합·아카이브 완료.

**git 밖 상태(E0·E4가 만든 것)**: 사용자 전역 `~/.claude/settings.json`은 래퍼 배선에 `PYTHONPATH` 접두가 있고 `collect.py` 훅 4건이 빠진 상태(훅 18건). 스냅샷 `~/.claude/settings.json.rejectbench-pre-e0-20260901`·`…pre-e4-20260902`가 유일한 되돌림 수단이라 남긴다. 사용자 소유 `.claude/settings.json.bak-*`·`.codex/config.toml.bak-*`는 비추적이며 건드리지 않는다.

**현재 표본** (2026-09-02 14:30 KST 실측): `guard_event` 8건 = operation 7(전부 `block-dangerous-git`·`project: reject-bench`, 서로 다른 세션 3개) + test 1. **그-외 파티션 0건.** 보고서 대표값(그-외)은 `미검증`, 전체는 0/1. 스키마 헤더 `기록기 현행 7.1 · 스냅샷 실존 7.0, 7.1`.

**새 사건 미처리 1건**: `ev-527f00a4…`, 2026-09-02 02:35 UTC, 다른 Claude 세션 `claude:55b5d4a…`의 heredoc 자기차단(선례 6건과 같은 오탐 구조). 7.1·`session_id_format: conforming`으로 기록된 첫 레코드 — E2가 전역 훅에서 실제로 돈 증거. 판정(과금)·검토(운영자 판단)라 손대지 않았다.

## 다음 할 일 (구체적 첫 행동 1개)

**새 사건 `ev-527f00a4…`의 판정·검토** — 사용자가 "나중에"로 결정 — 실행할 때는 순서 규칙(프로토콜 ⑨: 기본 모델이 마지막, 이번은 기본 모델 1회만)대로 `set -a; . ./.env; set +a` → `judge --approve-billing` → `review record --utility unnecessary …`(note에 위험 패턴 인용 금지). 그 뒤는 관측 대기: 2026-09-26경 4주 종료 판정까지 그-외 자연 발동이 없으면 "운영 빈도 미검증"으로 종료(D12). 강제 발동 금지. O2에서 그-외 사건이 있으면 `project` 값을 수동 확인(spec §9.1 한계).

## 미결 결정

1. **종료 조건은 그-외 파티션으로 판정한다**(결정기록 D12, 프로토콜 변경 기록 ⑤). 전체 지표로는 `block-dangerous-git`이 이미 2세션을 넘었지만 전부 도구개발 자기차단이다. 그-외 자연 발동이 없으면 미검증으로 종료.
2. **판정기 신뢰도** — 두 모델(gpt-4.1-mini / gpt-5-mini) 대조 6건 중 5건 불일치(83%). 순서 규칙은 결정됨(프로토콜 ⑨: 기본 모델 마지막, 소급 없음, 추가 임계 선등록 없음). 남은 것은 O2에서 "판정기가 증거로 쓸 만한가"의 정성 판단뿐. 관찰 기록은 `docs/기준선-측정.md` 부록.
3. **가드 수명주기 결정** — 오탐 구조(명령 문자열 전문 매칭, heredoc 무구분)는 O2의 `keep`/`modify` 재료. 관측 중 가드를 고치지 않는다.
4. **MCP 서버 전역 등록** — 다음 주기. 그-외 사건이 0건이라 판단 재료가 아직 없다.
5. **외부 검증(X1)** — O2 뒤에만.
6. **판정 사유 절단**(`judge.py`의 `PolicyVerdict.reason`)은 맹목 그대로 — 계약 문면만 한정했고(D11) 코드는 다음 주기. 처방: recorder와 같은 되물림을 공용 함수로, 판정 번들은 건드리지 않는다(`context_bundle_hash` 불연속 회피).
7. **판정 번들 순수성이 자유 텍스트(`reason`·`target_path`)로 우회된다** — 홈 경로·세션 ID가 외부 판정 API로 나간다. 이번 주기 변경 없음, 다음 주기 입력.
8. **짧은 비준수 세션 ID가 needle이 되면 사유 본문이 0자까지 준다**(`recorder._sensitive_values`, spec §3.3이 허용). Claude Code는 UUID만 주므로 합성·타 실행기 한정. 다음 주기: `session_id_format`이 conforming일 때만 원본을 needle로 삼는 §3.2 개정 검토(E3 규칙과 일관).
9. **자리표시 복합값 `claude:unknown`이 여전히 needle**(계약 문면대로, docstring 근거와만 어긋남) · **별칭화가 대문자 UUID를 못 가림**(Claude Code는 소문자만 줌). 둘 다 다음 주기.
10. **파티션 키가 cwd basename** — spec §9.1 한계로 명문화했고 기록기 변경(저장소 식별)은 다음 주기.

## 함정

- **전역 가드 자기차단이 곧 운영 사건이다.** 위험 패턴 텍스트를 heredoc/echo/CLI 인자(`--note`·`--reason`·`--rationale`·`--rejudge-reason`)로 쓰면 명령 문자열 전문 매칭으로 차단되고 operation 사건으로 기록된다. 파일 도구로 쓸 것. 새 사건이 정확히 이 경로였다.
- **작업 트리 코드가 모든 저장소의 Bash 훅에서 해석된다**(`package = false` + `PYTHONPATH` 접두). `records`·`recorder`·`wrapper` 편집 중 import 불가 상태면 전역 가드가 죽는다 — 편집마다 저장소 밖 cwd에서 `import rejectbench.wrapper` 스모크.
- **시험·강제 발동은 `REJECTBENCH_TEST_SESSION` 아래에서만**, store는 `REJECTBENCH_STORE`로 임시 경로에. 조회 서버는 그 env를 안 읽는다 — 임시 store는 `--store <경로>`로 별도 기동(`.dryforge/003/handoff.md` 하드 게이트 3의 명령 한 줄).
- **판정은 `.env`를 자동으로 읽지 않는다** — 셸 주입. 키 값을 어디에도 남기지 말 것. **기본 모델을 gpt-5 계열로 되돌리지 말 것**(temperature 거부로 전량 실패, `TestDefaultConfigCoherence`가 막는다).
- **테스트는 임시 store만**(conftest 게이트), 네트워크 금지. `pytest -q`는 addopts의 `-q`와 겹쳐 요약 줄이 사라진다 — 건수는 `uv run pytest | tail -1`.
- **E3 자격은 UUID 문법이지 E2 술어(8~128자)가 아니다.** 바꾸면 날짜꼴·hex 조각이 준수가 되어 타임스탬프·해시·event_id가 뭉개진다. 분해는 `records.split_session_id` 하나. 자리표시 `unknown`은 needle이 아니다.
- **미등록 가드 오류는 호출자 입력을 되비추지 않는다** — 오류 경로에서 별칭 치환을 보려면 타입 오류(목록형 `guard_id`)를 쓴다.
- **되물림·형식 진단의 폴백은 `assemble_event` 안에서 닫힌다.** 밖으로 새면 사건이 LossRecord로 강등된다. `session_id_format`은 진단값이지 게이트가 아니다.
- **v0 수집기는 되살리지 않는다**(사용자 결정). `PermissionRequest`·`PermissionDenied` 전역 관측 종료는 프로토콜 변경 기록 ②에 있다.
- **관찰-프로토콜 본문은 append만**, 변경 기록이 본문을 이긴다(⑥).
