# 운영 경험 개선 증분 — 실행 계획

작성 2026-09-01, 같은 날 적대 리뷰 결정 반영 개정. 행동 계약은 [`spec.md`](spec.md), 관장 문서는 [`handoff.md`](handoff.md)다.

## 상태 표시

- `[ ]` 미착수 · `[~]` 진행 중 · `[x]` 완료 및 검증됨

## 실행 모양

**선행 절차 하나 + 완전 순차 8태스크.** 기준선 측정을 먼저 끝내고 E0→E1→E2→E3→E4→E5→E6→E7을 순서대로 돈다.

- E0은 관측 자체를 고치므로 맨 앞이다. E1·E2가 같은 기록기 모듈을 연쇄 수정하고, E3가 E2의 복합값 분해 함수를 쓰며, E4는 사용자 전역 설정 파일(저장소 밖 외부 상태)을 만지고, E5·E6·E7이 결과를 문서·정본·보고서에 반영한다.
- 병렬 이득이 없어 그래프도 순차로 못 박는다. 다만 **논리 의존과 순서 제약은 그래프에서 구분해 표기한다**(`depends` vs `after`) — 산문과 YAML이 어긋나면 기계가 읽을 때 spec §11과 정면으로 충돌한다.

**선행 절차 — 기준선 측정 (빌드 아님, 태스크 아님)**

- [ ] `block-dangerous-git` 자기차단 2건에 대해 transcript+git 복원으로 기준선을 뜬다(`docs/관찰-프로토콜.md` 절차)
- [ ] **도구 보고서를 먼저 읽지 않는다** — 복원 오염 방지. 측정 완료 후 조회 금지가 해제된다
- [ ] 완료 사실과 측정 결과를 루트 `HANDOFF.md`에 남긴다

막힘 시 규칙(spec §11): 논리 의존(E2→E1 같은 파일, E3→E2 분해 함수, E6→E3 갱신 대상 확정, E7→E2 스키마 헤더)은 선행이 막히면 함께 멈춘다. 순서 제약뿐인 것(E1의 E0 선행, E4의 E0·E3 선행, E5의 E4 선행, E6의 E5 선행, E7의 E6 선행)은 선행이 막혀 중단됐어도 진행할 수 있다 — 단 E5의 기구 변경 추기(②)는 실제로 반영된 변경만 기록한다.

## E0 — 전역 래퍼 배선 수정

의존: 없음(기준선 측정 뒤). spec §2.

- [x] 편집 직전 전역 설정 스냅샷 — `~/.claude/settings.json.rejectbench-pre-e0-20260901` (사용자 소유 `.bak-*`와 다른 이름). 12228자/12326바이트 원본과 일치 확인
- [x] `~/.claude/settings.json` PreToolUse(matcher `Bash`) 명령에 인라인 `PYTHONPATH=/Users/ian/workspace/reject-bench` 접두 추가 — 대상 문자열 1회 확인 → 정밀 교체 → JSON 파싱·항목 수·최상위 키·길이 변화량(+45자) 검증 → 임시 파일 원자적 교체
- [x] 저장소 밖 cwd에서 `import rejectbench.wrapper` 성공 확인 — 수정 전 `ModuleNotFoundError`, 수정 후 `IMPORT OK`
- [x] 다른 훅 항목 불변 확인 — 훅 항목 22건·최상위 키 15개 그대로, collect.py 항목 4건 잔존(E4가 지운다)
- [x] `docs/배선-목록.md`의 새 배선 표를 수정된 명령으로 갱신하고 읽기 조회 캡처를 함께 적었다 ("E0 배선 수정" 절)
- [x] 검증 실패 시 스냅샷에서 복원 — **발동하지 않았다** (검증 전량 통과). 스냅샷은 E4까지 남겨 둔다

작업 대상: [파일] `docs/배선-목록.md` · **[외부·비버전관리 상태]** `~/.claude/settings.json`(PreToolUse `Bash` 명령 1건).
검증 게이트: 저장소 밖 import 성공 캡처 + 유효 JSON + 항목 불변 + `uv run pytest` 전체 통과.
생각 기반: 이 수정으로 작업 트리의 `rejectbench/` 코드가 **모든 저장소의 모든 Bash 호출**에서 해석된다. 이후 태스크의 중간 저장 상태가 import 불가면 가드가 전역에서 죽는다 — 관측창 한복판에서. 관측 범위 소급 정정은 E5④가 받는다.

## E1 — 차단 사유 절단의 공백 구분 단위 보존

의존: 없음(E0과는 순서 제약). spec §3.

- [x] 실패 테스트 먼저: 요구된 8종 전부 + 경계·음성 대조를 더해 28건. 신규 13건 red 확인 후 구현
- [x] 절단 되물림 구현: ① 공백류 되물림 → ② 민감값 세 종 가로지름 회피(고정점, **항상 적용**) → ③ 결과가 상한의 50% 미만이면 공백 되물림을 버리고 맹목 절단 + ②만 적용한 폴백 — `recorder.py`의 `_whitespace_cut`·`_retreat_past_sensitive`·`_cut_point`
- [x] 상한(4000)과 하한(50%)을 명명 상수로 두고 하한을 상한의 비율로 계산 — `_MIN_REASON = int(_MAX_REASON * _MIN_REASON_RATIO)`. 값 회귀는 `test_truncation_bounds_are_one_ratio_apart`(파생식 되풀이가 아니라 세 값을 리터럴로 고정 — 식 단언은 하드코딩을 못 잡는다)
- [x] 내부 폴백 확인: `_cut_point` 예외 주입 시 맹목 절단 + `recorded=True`, LossRecord 0건 — `test_truncation_failure_falls_back_to_blind_cut_and_still_records`
- [x] **import 스모크**: 저장소 밖 cwd에서 `IMPORT OK` (편집 3회 각각 뒤)
- [x] **추가(적대 검증 반영)**: 되물림의 상한 경계("가로지를 때만")를 고정하는 음성 대조 3건 — 없으면 사유 앞머리 홈 경로 하나로 본문이 0자가 되는 과잉 구현이 전량 green으로 지나간다. 돌연변이 8종(과잉 되물림 2·창 off-by-one·고정점 1회·되물림 제거·개행/탭 무시·하한 상수화) 전부 red 확인
- [x] **추가(적대 검증 반영)**: `_whitespace_cut`을 정규식에서 역방향 선형 훑기로 — 정규식 판은 앞 4000자가 한 덩어리인 입력(경로 덤프·compact JSON 한 줄)에서 역추적으로 40ms였다. 두 판의 절단점은 무작위 10만 건·경계 케이스 전부 일치

작업 대상: [파일] `rejectbench/recorder.py`, `tests/test_recorder.py`.
검증 게이트: 신규 테스트 red→green + `uv run pytest` 전체 통과 + import 스모크.
생각 기반: 공백 되물림은 **정돈**이고 안전 보장(민감값 조각 미생성)은 폴백 경로도 유지한다. 그래서 민감값 검사는 보조 규칙이 아니라 **항상 도는 규칙**이고, 되물림이 본문 절반 이상을 먹으면 정돈 쪽을 버린다. 개정 전 계획의 "두 규칙의 우선순위를 뒤집지 말 것"은 **이 개정으로 폐기됐다**(D5). 마커와 4000자 상한 의미는 불변.

## E2 — 세션 ID 적재 형식 규칙

의존: E1 (같은 파일 연쇄 수정). spec §4.

- [x] 실패 테스트 먼저: 요구된 7종 전부 + 경계(8자·128자·둘째 콜론·harness 부분 무검사)·초과 키·호출자 dict 무변경·store 수준 손상 줄 대조를 더해 39건(records 31·recorder 8). 두 슬라이스(records → recorder) 각각 red 확인 후 구현
- [x] 복합값 분해 함수 구현 — `records.split_session_id`(`str.partition` 첫 `:`, 없으면 원본 `None`). `records`는 `recorder`·`mcp_server` 둘 다 이미 import하는 최경량 모듈이라 E3가 그대로 쓴다. `recorder._sensitive_values`의 원본 needle도 이 함수로 모았다(E1 적대 검증 지적 반영) — 단 자리표시는 needle에서 뺀다
- [x] 형식 술어 구현 — `records.session_id_format`(원본 부분만, `re.fullmatch`). 파라미터는 `SESSION_ID_RAW_RULE = SessionIdRawRule(8, 128, "A-Za-z0-9_-")` 한 곳. 값 회귀는 리터럴 단언(`test_rule_parameters_are_one_named_constant`)
- [x] `GuardEvent.session_id_format` 추가 — `SessionIdFormat` StrEnum 3상, `_require_enum`으로 `null`·문자열 거부. dataclass 기본값 `unchecked`(검사 안 한 사실 그대로). 기록기는 자리표시 → `unchecked`, 술어 예외 → `unchecked`(폴백은 `assemble_event` 안 `_session_id_format`에서 닫힘, LossRecord 0건 확인)
- [x] `SCHEMA_VERSION` 7.1 인상 — 전역 상수 한 줄. 7종 + `JudgeCalibration` 기본값이 7.1을 따름을 테스트로 고정. 어떤 코드 경로도 이 값을 소비하지 않는다
- [x] 파서 완화 — `records._accept_legacy_event`: `guard_event` ∧ 누락 == {`session_id_format`} ∧ 초과 없음일 때만 사본에 `unchecked`를 채운다. 다른 키 누락·초과 키·타 record_type은 여전히 `SchemaError` → 손상 줄(store 수준에서 줄 번호까지 대조)
- [x] **import 스모크**: 저장소 밖 cwd에서 `IMPORT OK` (슬라이스마다 + 커밋 직전, 3회)
- [x] **사건 수 보존**(운영 store 읽기 전용, 완화 전후 대조): guard_event 7·손상 줄 0·operation 6·판정 가능 4·검토 큐 0·완료율 0/1·등록부 2가드 전부 동일. 실존 스키마 버전은 `{7.0}` 그대로(재작성 없음)
- [x] **추가(판별력)**: 돌연변이 13종 중 12종 red — 초과 키 무시·`$` 앵커·하이픈 탈락·하한/상한 off-by-one·마지막 콜론 분해·예외 미포착·자리표시 검사·미검사·예외→conforming·needle 자리표시 포함·needle 원본 누락. `record_type` 검사 제거 1종은 등가 돌연변이(타 레코드는 그 키를 기대하지 않는다). "마지막 콜론" 돌연변이를 형식값 수준에서 가르는 픽스처(`codex:run:<uuid>` → 비준수)를 이 과정에서 추가했다

작업 대상: [파일] `rejectbench/records.py`, `rejectbench/recorder.py`, `tests/test_records.py`, `tests/test_recorder.py`.
검증 게이트: 신규 테스트 red→green + 전체 통과(기존 직렬화·파싱 회귀 포함) + **사건 수 보존** — 완화 전후로 등록부·지표 분모·검토 큐의 사건 수가 같고 손상 줄 수가 늘지 않음 + import 스모크.
생각 기반: 검사 대상은 복합값이 아니라 **원본 부분**이다. 저장값·origin 규칙은 절대 불변 — 이 태스크는 관찰만 더한다. 단 "관찰만 더한다"가 예외로 사건을 잃게 만들면 성격과 모순이므로 내부 폴백이 필수다. 7.1이라는 값은 어떤 코드 경로도 소비하지 않는 순수 표기이며 구형 판별은 **필드 부재** 기준이다.

## E3 — 조회 표면 가림 확장

의존: E2 (분해 함수 재사용). spec §5.

- [x] 실패 테스트 먼저: `buried_store` 픽스처 — 복합값 `claude:<uuid>`와 원본 `<uuid>`를 사유·판정 사유·검토 메모·결정 근거·spec 의미 필드에 `foo<uuid>bar`·`x<복합값>y`·`/<uuid>.jsonl` 꼴로 파묻고 도구 3종 + 메아리가 나가는 오류 경로(목록형 `guard_id`)의 응답 전체에서 원문 0회, 별칭이 주변 글자를 보존한 채 나타남을 단언. 신규 9건 중 4건 red 확인 후 구현(나머지는 현행 동작을 지키는 대조라 처음부터 green이 맞다)
- [x] 자격 술어 — `mcp_server._UUID_SYNTAX`(8-4-4-4-12 hex, `fullmatch`) + `_uuid_raw_part`. 분해는 **E2의 `records.split_session_id`** 그대로(첫 `:` 기준 — 마지막 `:` 분해 돌연변이를 `codex:run:<uuid>` 픽스처가 잡는다)
- [x] needle 분류 — `OutputBoundary._needles`(needle → 정본 저장 ID) 하나로 통합. 준수 복합값은 복합값·원본 둘 다 무경계 부분문자열 needle이고 같은 정본(동일 별칭), 그 외는 `(?<!\w)…(?!\w)` 토큰 경계 유지. 단일 패스 치환·긴 needle 우선·홈 치환 전 별칭화·오류 경로 단일 통과는 그대로
- [x] **비훼손 검증** — 자리표시 `claude:unknown` 사건의 사유 `unknown state; unknownish…`와 정책 텍스트가 글자 하나 안 바뀜, 비준수 `claude:sess-1`의 `prefix…suffix` 접착 등장이 002대로 원문 유지
- [x] **준수 부류 비훼손 픽스처** — §4 술어로는 준수인 날짜꼴(`2026-08-01`)·12자 hex 조각·`planted0001`을 세션 ID로 심고 `occurred_at`·`content_hash`·`event_id` 불변 + 세션 필드는 별칭. §4 술어를 자격으로 쓰는 돌연변이가 이 테스트에서만 red다
- [x] 002 양성 대조 전체 통과(값 전체·토큰·오류 경로·홈 경로·정화 후 절단) — 기존 89건 무수정
- [x] 임시 store 실기동 왕복 — (a) 테스트 `test_stdio_round_trip_aliases_a_buried_uuid`(하위 프로세스 stdio) (b) 하드 게이트 3의 명령 한 줄(`PYTHONPATH=… uv run --project … python -m rejectbench.mcp_server --store <임시>`)을 **저장소 밖 cwd**에서 별도 기동해 `guard_evidence` 호출: 도구 3종 목록, `is_error: false`, 원본 UUID·복합값 0회, `reason: "transcript /x/S1.jsonl; glued fooS1bar; whole S1"`. 운영 store 무접촉
- [x] **추가(판별력)**: 돌연변이 7종 전부 red — §4 술어 자격·준수 부류 없음(002 대조군)·전부 무경계·마지막 콜론 분해·원본에 별도 별칭·준수도 토큰 경계·짧은 needle 우선. 마지막 둘은 처음 살아남아 픽스처를 더했다: 접두 겹침 ID(`claude:s`·`claude:s-1`)의 자유 텍스트 픽스처는 002 이후 한 번도 없었고, 짧은 needle 우선이면 `S1-1`로 샌다

작업 대상: [파일] `rejectbench/mcp_server.py`, `tests/test_mcp_server.py`.
검증 게이트: 신규+기존 양성 대조 전체 통과, 실기동 왕복은 임시 store로만.
생각 기반: 분류는 출력 시점 모양 기준(저장 필드 무관)이다. 자격을 UUID로 좁히는 것은 **안전 방향 실패** — 탈락한 값은 보호를 잃는 게 아니라 002 수준 토큰 경계 매칭으로 떨어진다. 긴 needle 우선·별칭 재치환 방지·홈 치환 전 별칭화 순서는 기존 구현의 계약 — 유지하며 확장한다.

## E4 — v0 수집기 완전 퇴역

의존: 없음(E0·E3과는 순서 제약 — 전역 설정 파일은 순차·직접 수정). spec §6.

- [x] **스냅샷 먼저** — `~/.claude/settings.json.rejectbench-pre-e4-20260902`(바이트 일치 확인). 편집·검증·교체·복원을 한 스크립트에 박았다: 메모리 편집 → 임시 파일(원본 서식 보존) → 재파싱 + 검증 3종 + "collect.py 외 항목 불변" → `os.replace` → 교체 후 재검증, 실패 시 스냅샷 복원. **복원은 발동하지 않았다**
- [x] 전역 설정에서 collect.py 호출 4건만 제거 — 사전 조회로 정확히 4건(각각 단독 그룹)임을 확인하고 제거. 빈 그룹은 남기지 않았고, 남는 훅이 없는 `PermissionDenied`는 이벤트 키를 뺐다. 같은 이벤트의 다른 훅 3건·나머지 이벤트·래퍼 배선(PreToolUse `Bash`, `PYTHONPATH` 접두 포함) 불변 — 22→18건, 최상위 키 16 그대로
- [x] `hooks/collect.py` 삭제(`git rm`). `continuity.py`·`codex_handoff_gate.py`·각 테스트·`data/events.jsonl` 보존, `pyproject.toml`의 `testpaths`/`pythonpath` 무변경
- [x] `docs/배선-목록.md` — collect.py 언급 모든 줄 갱신: E0 캡처 표의 "E4가 지운다", v0 절 제목·본문, cutover 대사의 죽은 번호 참조("HANDOFF 미결 3" → 항목명 "v0 수집기 퇴역")
- [x] `README.md` "기존 수집기 v0" 문단을 퇴역 사실로
- [x] 읽기 조회로 확인(스크립트와 별도 조회): 유효 JSON / collect.py 0건 / 래퍼 1건 + 스냅샷 대비 `diff`가 제거 줄뿐(collect.py 줄 4, 추가 줄 0)
- [x] 캡처를 `docs/배선-목록.md`의 "E4 v0 수집기 퇴역" 절에 표로 적었다. `uv run pytest` 520건 통과(연속성 훅 테스트 잔존)

작업 대상: [파일] `hooks/collect.py`(삭제), `docs/배선-목록.md`, `README.md` · **[외부·비버전관리 상태]** `~/.claude/settings.json`(항목 4건 제거).
검증 게이트: 읽기 조회 3종 캡처(목적지 `docs/배선-목록.md`) + `uv run pytest` 전체 통과(연속성 테스트 잔존 확인).
생각 기반: 전역 설정에는 이 저장소와 무관한 훅이 다수 있다(알림·핸드오프 게이트·타 도구). **collect.py 명령이 든 항목만** 정밀 제거한다. 사용자 소유 백업 파일(`.claude/settings.json.bak-*`, `.codex/config.toml.bak-*`)은 절대 건드리지 않는다. risk를 MECHANICAL로 둔 것은 의도다 — 전역 파일은 격리 사본이 아니라 본 흐름에서 순차·직접 수정하는 쪽이 안전하고, 검증이 읽기 조회 3종 + 스냅샷 롤백으로 객관화돼 있다.

## E5 — 관찰 프로토콜 추기

의존: 없음(E4와는 순서 제약 — 모든 변경 확정 뒤 최종 문서화). spec §7.

- [x] `docs/관찰-프로토콜.md` "변경 기록"에 append 8건(+ 머리말 1줄). 각 항목에 일자·사유(결정기록 참조)와 "사건 정의·출처 규칙·평가 양식·종료 조건 불변"을 달았다. ⑤에는 D12의 두 선등록(종료 조건은 그-외 파티션으로 판정 / `insufficient_context` 임계 = 확정 5건 이상에서 50% 이상, 전체 지표 기준)을 함께 적었다 — 종료 조건 문면은 불변이고 "어느 벌로 읽는가"의 해석 고정이다. ⑥은 변경 기록 안에 있다
- [x] ② — E0(2026-09-01)·E1(`12e2269`)·E2(`73070dd`)·E4(`e439c82`) 넷만. 전부 실제로 반영·커밋된 변경이고, E3는 조회 표면이라 기구 목록 밖임을 명시
- [x] 기존 본문 무수정 — `git diff --numstat`로 삭제 0줄 확인(아래 검증 게이트). ADR 0001은 frontmatter `status`를 `partially superseded`로 바꾸고 끝에 "Supersede 기록" 절만 덧붙였다(본문 보존 — 상태 표기는 ADR 관례)

작업 대상: [파일] `docs/관찰-프로토콜.md`(append 전용), `docs/adr/0001-관측대상-실존-가드-앵커.md`(supersede 기록 — ADR 관례에 따른 상태 표기).
검증 게이트: diff 검사 — `관찰-프로토콜.md`의 기존 줄 삭제·수정 0건, 추가만 존재.
생각 기반: 이 문서는 선등록 산출물이다. 여덟 건 모두 일자·사유를 달고, 종료 조건·평가 양식이 불변임을 명시한다. ⑥은 본문에 넣으면 append-only와 충돌하므로 **변경 기록 안에** 둔다.

## E6 — 정본 갱신

의존: E3 (갱신 대상이 E3 구현으로 확정된다). spec §8.

- [ ] `rejectbench/AGENTS.md` 비노출 서술 갱신 — `:27`의 "단어에 붙은 동일 부분문자열은 … 보존한다"와 `:29`의 "임의 부분문자열까지 0회를 보장하려면 먼저 세션 ID 문법 … 을 적재 계약에 추가해야 한다"가 E3로 낡는다
- [ ] 루트 `CLAUDE.md` 비협상 ⑥ 갱신(준수 부류 부분문자열 별칭화 + 잔여 한계 둘)
- [ ] 루트 `AGENTS.md`를 같은 내용으로 갱신
- [ ] 루트 `HANDOFF.md` 미결 목록·함정을 이 주기 상태로 갱신
- [ ] **`diff CLAUDE.md AGENTS.md` exit 0** 확인

작업 대상: [파일] `rejectbench/AGENTS.md`, `CLAUDE.md`, `AGENTS.md`, `HANDOFF.md`.
검증 게이트: `diff CLAUDE.md AGENTS.md` exit 0 + `uv run pytest` 전체 통과.
생각 기반: 서열표상 `rejectbench/AGENTS.md`는 구현층 모듈 규칙의 정본이다. 낡은 채로 두면 다른 세션이 그 문면("부분문자열은 보존한다")을 따라 E3 구현을 되돌린다. 이건 마무리 관례가 아니라 **태스크**다 — 완료 게이트의 "spec 요구 전부 태스크로 소급 가능"이 이 태스크로 참이 된다.

## E7 — 보고서 파티션

의존: E2 (스키마 버전 헤더 처리가 7.1 인상 뒤에만 의미가 있다). spec §9.

- [ ] 실패 테스트 먼저: 도구개발/그-외 사건이 섞인 픽스처에서 세 값(전체·도구개발·그-외)이 각각 맞게 나오고, 분모 0 파티션이 `미검증`으로 렌더링됨
- [ ] 파티션 키 구현: `GuardEvent.project == "reject-bench"` → 도구개발, 그 외 → 그-외
- [ ] 적용 범위 — `operation_event_ids` 파생 지표 전부: 5개 비율, 운영 사건 수, 판정·검토 미처리/보류 현황, 결정 완료율
- [ ] 미적용 확인: 손실·정정·강등·손상 줄·총 레코드 수, 시험/unknown/unregistered 집계, 기준선·교정 사이드카는 단일 값 유지. **가드별 표는 기존 `project` 열이 판별자 — 열을 중복 추가하지 않는다**
- [ ] 대표값이 **그-외**임을 보고서 문면에 렌더링
- [ ] **합산 검산을 넣지 않는다** — 결정 완료율은 가드 단위라 두 벌의 합이 전체와 다를 수 있다(2세션 규칙). 이 사실을 테스트로 고정한다
- [ ] 스키마 버전 헤더: 기록기 현행 버전 + 스냅샷에 실제 존재하는 버전 집합 병기(둘이 같으면 한 값)
- [ ] 비노출 계약 회귀 확인: 홈 경로·세션 식별자·사건 id·enforcement 경로·guard_hint 미노출 그대로

작업 대상: [파일] `rejectbench/report.py`, `tests/test_report.py`.
검증 게이트: 신규 테스트 red→green + `uv run pytest` 전체 통과(기존 보고서 계약 회귀 포함).
생각 기반: 지표 정의는 `metrics`의 단일 정의를 재사용하고 이 모듈에서 재정의하지 않는다(`report.py:5-7`의 기존 규율). 파티션은 기존 시험/운영 분리의 **안쪽**에 들어간다 — 시험 사건은 여전히 운영 지표 밖이다. `Ratio.__post_init__`의 분자⊆분모는 각 파티션 안에서 독립적으로 성립해야 한다.

## 실행 그래프

기계 판독용. `depends`는 **논리 의존**(선행이 막히면 함께 멈춘다), `after`는 **순서 제약**(선행이 막혀도 진행할 수 있다)이다 — 산문 "막힘 시 규칙"과 spec §11이 이 구분에 걸려 있으므로 한 필드에 섞지 않는다.

```yaml
preconditions:
  - id: baseline
    kind: procedure          # 빌드 아님 — plan 태스크가 아니다
    blocks: [E0, E1, E2, E3, E4, E5, E6, E7]

tasks:
  - id: E0
    depends: []
    after: []
    risk: MECHANICAL         # 외부·비버전관리 상태(전역 설정) 수정
  - id: E1
    depends: []
    after: [E0]
    risk: RISKY
  - id: E2
    depends: [E1]
    after: []
    risk: RISKY
  - id: E3
    depends: [E2]
    after: []
    risk: RISKY
  - id: E4
    depends: []
    after: [E0, E3]
    risk: MECHANICAL         # 외부·비버전관리 상태(전역 설정) 수정
  - id: E5
    depends: []
    after: [E4]
    risk: MECHANICAL
  - id: E6
    depends: [E3]
    after: [E5]
    risk: MECHANICAL
  - id: E7
    depends: [E2]
    after: [E6]
    risk: RISKY
```

`regen_barriers`: 없음(코드 생성 단계 없음).

## 커밋 경계

권장 최소 커밋 순서 (커밋과 push는 별개, push는 사용자 요청 시만):

0. `docs: revise experience-improvement cycle contract` — **계약 3종 + `docs/운영경험개선-증분-적대리뷰.md` + `docs/운영경험개선-증분-결정기록.md` + 루트 `HANDOFF.md` + `.claude/skills/adversarial-review-blind/`**. `docs/운영경험개선-증분-개정지시.md`는 이 커밋에서 삭제한다(개정이 끝나면 임시 작업 문서다)
1. `fix: make the guard wrapper importable outside the repo` — E0
2. `fix: preserve whitespace-unit boundaries in reason truncation` — E1
3. `feat: validate session id format at ingest` — E2
4. `feat: alias conforming session ids down to substrings` — E3
5. `chore: retire v0 collector` — E4
6. `docs: append observation protocol amendments` — E5
7. `docs: update canonical docs for the extended redaction contract` — E6
8. `feat: partition operation metrics by tool-development origin` — E7

## 완료 게이트

- 여덟 태스크 전부 병합·검증, spec 요구 전부 태스크로 소급 가능(E6 신설로 성립), 미해결 차단 0건.
- 선행 절차(기준선 측정) 완료가 루트 `HANDOFF.md`에 기록돼 있다.
- 최종 전체 검증: `uv run pytest` exit 0 캡처. E3 실기동 왕복(임시 store) 1건 캡처. E0·E4 읽기 조회 캡처가 **`docs/배선-목록.md`에 실제로 기록**돼 있다.
- 정본 갱신 3종(`rejectbench/AGENTS.md`, 루트 `CLAUDE.md`/`AGENTS.md`, 루트 `HANDOFF.md`) 완료 + **`diff CLAUDE.md AGENTS.md` exit 0**.
- 운영 `data/` 비접촉을 유지한 채 완료. 조회 도구는 기준선 측정 완료 후에만 쓰고, E3 검증은 임시 store로만.
