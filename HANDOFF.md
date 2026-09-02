# 핸드오프 — 003 마감·적대리뷰 반영 완료, 관측 대기 (2026-09-03)

## 지금 하던 것

003 주기(운영 경험 개선 증분)의 **E0~E7 여덟 태스크가 전부 끝났고 코드리뷰 반영까지 마쳤다.** **PR #8이 main에 병합됐고(`108f62c`) `.dryforge/` 루트 3종은 `003/`로 아카이브했다.** 무엇을 왜 바꿨는지는 `git log`의 커밋 본문이 정본이고, 체크박스는 `.dryforge/003/plan.md`(전부 `[x]`), 계약은 `.dryforge/003/{spec,plan,handoff}.md`다. 진행 중 주기는 없다.

**병합 뒤 2026-09-02에 한 것**: 코드리뷰 반영(`e0d2233`) → 개정분 적대 리뷰 `docs/003-개정분-적대리뷰.md`(블라인드 A·B + 메인, 교차 재현 9건) → grilling 인터뷰로 전량 결정(결정기록 D13~D22) → 반영(`0ff7643`). 살아 있는 계약은 아카이브된 `.dryforge/003/spec.md` §3.1·§9.1·§9.6과 프로토콜 변경 기록 ⑨~⑪이다. **⑪이 ⑨의 사실 오류를 정정했다**(6건 확정값은 gpt-4.1-mini 2 + gpt-5-mini 4). 코드 쪽 변화: `metrics.decision_completion`이 근거∩파티션으로 재고, 보고서에 파티션 불가(`unknown`) 건수·최신 판정 모델 분포·insufficient_context 임계 줄이 생겼다.

완료 게이트 상태: `uv run pytest` 540건 exit 0 · `diff CLAUDE.md AGENTS.md` exit 0 · 운영 `data/` 읽기만(판정 1회 과금 제외) · 작업 트리 clean, main = origin/main.

**git 밖 상태(E0·E4가 만든 것)**: 사용자 전역 `~/.claude/settings.json`은 래퍼 배선에 `PYTHONPATH` 접두가 있고 `collect.py` 훅 4건이 빠진 상태(훅 18건). 스냅샷 `~/.claude/settings.json.rejectbench-pre-e0-20260901`·`…pre-e4-20260902`가 유일한 되돌림 수단이라 남긴다. 사용자 소유 `.claude/settings.json.bak-*`·`.codex/config.toml.bak-*`는 비추적이며 건드리지 않는다.

**현재 표본** (2026-09-02 22:10 KST 보고서 실측): `guard_event` 8건 = operation 7(전부 `block-dangerous-git`·`project: reject-bench`, 서로 다른 세션 3개) + test 1. **그-외 0 · 파티션 불가 0.** 대표값(그-외) `미검증`, 전체 완료율 0/1, 최신 판정 모델 gpt-4.1-mini 3 · gpt-5-mini 4(기본 이외 4 = ⑪의 소급 제외분), insufficient_context 2/7 미발동. 판정·검토 미처리 0건 — 마지막 사건 `ev-527f00a4…`(코드리뷰 세션의 heredoc 자기차단)은 ⑨대로 기본 모델 1회만 판정(`correct_block`, 과금 1회), 검토 `unnecessary`.

## 다음 할 일 (구체적 첫 행동 1개)

**관측 대기.** `docs/003-개정분-적대리뷰.md`의 finding은 전량 결정·반영됐다(결정기록 D13~D22, 프로토콜 ⑩·⑪, spec §3.1·§9.1·§9.6 재개정, metrics 근거∩파티션, 보고서에 파티션 불가 건수·최신 판정 모델 분포·insufficient_context 임계 줄). **그-외 사건이 기록되면 즉시 프로토콜 ⑩(1)** — 세션 ID로 transcript를 열어 실제 저장소를 확인하고 결과를 변경 기록에 append한다. 결정을 낼 때는 판정 가능 사건 전부를 `--evidence`로 인용한다(⑩(6)). 대조 판정을 할 때는 대조 모델 rejudge → 기본 모델 rejudge 순(⑨, 사건당 3회 과금). 그 뒤는: 2026-09-26경 4주 종료 판정까지 그-외 자연 발동이 없으면 "운영 빈도 미검증"으로 종료(D12). 강제 발동 금지.

## 미결 결정

1. **종료 조건은 그-외 파티션으로 판정한다**(결정기록 D12, 프로토콜 변경 기록 ⑤). 전체 지표로는 `block-dangerous-git`이 이미 2세션을 넘었지만 전부 도구개발 자기차단이다. 그-외 자연 발동이 없으면 미검증으로 종료.
2. **판정기 신뢰도** — 두 모델 대조 6건 중 5건 불일치(83%), 전표는 `docs/기준선-측정.md` 부록 "대조 6건 전표". 순서 규칙·정정은 프로토콜 ⑨·⑪. 남은 것은 O2에서 "판정기가 증거로 쓸 만한가"의 정성 판단뿐이며, 그 판단이 지표를 무효화할 수 있다(⑪(e)).
3. **가드 수명주기 결정** — 오탐 구조(명령 문자열 전문 매칭, heredoc 무구분)는 O2의 `keep`/`modify` 재료. 관측 중 가드를 고치지 않는다.
4. **MCP 서버 전역 등록** — 다음 주기. 그-외 사건이 0건이라 판단 재료가 아직 없다.
5. **외부 검증(X1)** — O2 뒤에만.
6. **판정 사유 절단**(`judge.py`의 `PolicyVerdict.reason`)은 맹목 그대로 — 계약 문면만 한정했고(D11) 코드는 다음 주기. 처방: recorder와 같은 되물림을 공용 함수로, 판정 번들은 건드리지 않는다(`context_bundle_hash` 불연속 회피).
7. **판정 번들 순수성이 자유 텍스트(`reason`·`target_path`)로 우회된다** — 홈 경로·세션 ID가 외부 판정 API로 나간다. 이번 주기 변경 없음, 다음 주기 입력.
8. **짧은 비준수 세션 ID가 needle이 되면 사유 본문이 0자까지 준다**(`recorder._sensitive_values`, spec §3.3이 허용). Claude Code는 UUID만 주므로 합성·타 실행기 한정. 다음 주기: `session_id_format`이 conforming일 때만 원본을 needle로 삼는 §3.2 개정 검토(E3 규칙과 일관).
9. **자리표시 복합값 `claude:unknown`이 여전히 needle**(계약 문면대로, docstring 근거와만 어긋남) · **별칭화가 대문자 UUID를 못 가림**(Claude Code는 소문자만 줌). 둘 다 다음 주기.
10. **파티션 키가 cwd basename** — spec §9.1 한계 + 프로토콜 ⑩ 확인 절차로 닫았고 기록기 변경(저장소 식별)은 다음 주기. `project == "unknown"`은 파티션 밖.

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
- **관찰-프로토콜 본문은 append만**, 변경 기록이 본문을 이긴다(⑥). ⑪이 ⑨의 사실 오류를 정정했다 — ⑨만 읽지 말 것.
