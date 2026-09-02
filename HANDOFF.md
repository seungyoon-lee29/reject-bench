# 핸드오프 — 003 E7 대기 (2026-09-02)

## 지금 하던 것

003 주기(운영 경험 개선 증분)에서 **계약 개정 → 기준선 측정 → 미결 정리 → E0 → E1 → E2 → E3 → E4 → E5 → E6**까지 끝냈다. E0까지는 main에 머지돼 있고 **E1~E6는 브랜치 `dryforge/003-e1-truncation-retreat`에 커밋돼 있다(push·PR 안 함)**. 남은 것은 **E7**(보고서 파티션) 하나다.

계약 정본은 `.dryforge/{spec,plan,handoff}.md` 3종. 태스크는 선행 절차 하나 + E0~E7 여덟 개이고 **E0~E6 완료**다. 각 태스크 체크박스는 `.dryforge/plan.md`에 있다.

**끝난 것**

- **계약 개정** (PR #3) — 적대 리뷰 27건 반영, 태스크 5→8개. 개정지시와 다르게 쓴 4건은 `docs/운영경험개선-증분-결정기록.md`의 "개정 중 이 기록과 다르게 쓴 것" 절에 근거와 함께 있다.
- **기준선 측정** (PR #4) — `docs/기준선-측정.md`가 정본. 도구 경로가 소요 −43%·연 파일 −54%지만 **조회 표면만으로는 6항목 중 5개가 상한**이다(행동 의도는 스키마에 없고, 명령 내용은 `<command omitted>`, 세션 별칭이 transcript 조인 키를 끊는다).
- **판정 CLI 주입구** (PR #5) — `judge --model-settings JSON`(전체 교체). `AGENTS.md:42`가 처방한 경로.
- **E0 배선 수정** (PR #6) — 전역 훅에 인라인 `PYTHONPATH` 접두. 저장소 밖 cwd에서 `ModuleNotFoundError` → `IMPORT OK`. 캡처는 `docs/배선-목록.md`의 "E0 배선 수정" 절.
- **E0 실기동 검증** — 다른 저장소(`provenance`) cwd에서 래퍼를 직접 불러 가드 차단(exit 2)과 **임시 store 기록**까지 확인했다. `project: provenance`, `origin: test`(`explicit_flag`). 운영 store 무접촉.
- **E6 정본 갱신** (커밋 `docs: update canonical docs for the extended redaction contract`) — `rejectbench/AGENTS.md`의 낡은 비노출 두 문장을 E3 계약(두 부류·자격은 UUID 문법·잔여 한계 둘·파묻힌 UUID 양성 대조)으로 바꾸고, 모듈 맵(`records`·`recorder`)·절단 되물림·collect.py 퇴역·함정 2건을 이 주기 상태로 맞췄다. 루트 `CLAUDE.md` 비협상 ⑥을 같은 내용으로 고치고 `AGENTS.md`를 사본으로 동기화 — `diff CLAUDE.md AGENTS.md` exit 0. 전체 게이트 520건.
- **E5 관찰 프로토콜 추기** (커밋 `2a9a6f0`) — `docs/관찰-프로토콜.md` "변경 기록"에 append 8건(머리말 1줄 포함, 기존 줄 삭제·수정 0 — `git diff --numstat` 삭제 0). ① 기준선 이월 규칙 ② 기구 변경 E0·E1·E2·E4(커밋 해시 명기, E3는 조회 표면이라 제외) ③ 관측 무대 변화(`protect-live-reports` 기대 발동 0 수렴, `block-dangerous-git` 단독 의존) ④ 관측 범위 소급 정정(2026-08-29~09-01 구간은 사실상 reject-bench 세션만) ⑤ 자기차단 산입 규칙 + **D12 두 선등록**(종료 조건은 그-외 파티션으로 판정 / `insufficient_context` 임계 확정 5건 이상에서 50% 이상, 전체 지표 기준) ⑥ 본문 vs 변경 기록 승자 규칙(변경 기록 안) ⑦ 이월 누계 표기(현재 0건) ⑧ ADR 0001 부분 supersede. `docs/adr/0001-…md`는 frontmatter `status: partially superseded`와 끝의 "Supersede 기록" 절만 더했다(본문 보존).
- **E4 v0 수집기 완전 퇴역** (커밋 `e439c82`) — 사용자 전역 `~/.claude/settings.json`에서 `hooks/collect.py` 호출 4건만 제거(22→18건, 최상위 키 16 그대로, 남는 훅이 없는 `PermissionDenied`는 이벤트 키 제거, 래퍼 배선 불변), 저장소에서 `hooks/collect.py` 삭제, `docs/배선-목록.md`·`README.md` 갱신. 스냅샷 `~/.claude/settings.json.rejectbench-pre-e4-20260902`(E0 스냅샷과 함께 남긴다). 편집·검증 3종·원자 교체·복원을 한 스크립트에 박았고 복원은 발동하지 않았다. 스크립트와 별도의 읽기 조회로 재검증: 유효 JSON / collect.py 0건 / 래퍼 1건, 스냅샷 대비 diff는 제거 줄만(collect.py 줄 4·추가 0). 캡처 표는 `docs/배선-목록.md`의 "E4 v0 수집기 퇴역" 절. 전체 게이트 520건(연속성 훅 테스트 잔존).
- **E3 조회 표면 가림 확장** (커밋 `cc1d635`) — `mcp_server.OutputBoundary`가 저장 세션 ID를 출력 시점 모양으로 두 부류로 가른다: `records.split_session_id`로 분해한 원본이 **UUID 문법**(`_UUID_SYNTAX`, §4 술어 아님)이면 복합값·원본 둘 다 **무경계 부분문자열** needle로 같은 별칭에, 그 외(자리표시·비준수·`:` 없는 값)는 002 토큰 경계 유지. needle → 정본 저장 ID 표(`_needles`) 하나로 통합했고 단일 패스·긴 needle 우선·홈 치환 전 별칭화는 그대로. 신규 테스트 9건(파묻힘 양성 대조·자리표시/비준수 비훼손·날짜꼴/hex/event_id 불변·코너 규정·stdio 왕복·첫-콜론 분해·접두 겹침), 전체 게이트 520건 통과. **실기동 왕복**: 하드 게이트 3 명령을 저장소 밖 cwd에서 임시 store로 별도 기동해 `guard_evidence` 호출 → 원본 0회, `reason: "transcript /x/S1.jsonl; glued fooS1bar; whole S1"`. 돌연변이 7/7 red(둘은 처음 살아남아 픽스처를 더했다 — 접두 겹침 `claude:s`/`claude:s-1` 자유 텍스트는 002 이후 한 번도 없던 픽스처). `rejectbench/AGENTS.md:27·:29`와 루트 `CLAUDE.md`/`AGENTS.md` ⑥은 **이제 낡았다** — E6가 갱신한다.
- **E2 세션 ID 적재 형식 규칙** (커밋 `73070dd`) — `records.py`에 분해 함수 `split_session_id`(첫 `:` 기준, 이후 전부가 원본)·술어 `session_id_format`·명명 상수 `SESSION_ID_RAW_RULE`(8~128, `[A-Za-z0-9_-]`)·`SessionIdFormat` 3상 enum·`GuardEvent.session_id_format`(기본값 `unchecked`, `null` 불허)·`SCHEMA_VERSION` 7.1·파서 완화(`guard_event`이고 누락 키가 `session_id_format` 하나일 때만 `unchecked`, 호출자 dict 무변경). `recorder.py`는 자리표시면 `unchecked`, 술어 예외도 `unchecked`(폴백은 `assemble_event` 안에서 닫힘), `_sensitive_values`가 원본 needle을 페이로드가 아니라 `split_session_id`로 얻는다(자리표시 `unknown`은 needle 아님 — 음성 대조로 고정). 신규 테스트 39건(records 31·recorder 8), 전체 게이트 511건 통과, 저장소 밖 import 스모크 3회 OK.
  **사건 수 보존 대조**(운영 store 읽기 전용, 완화 전후): guard_event 7·손상 줄 0·operation 6·판정 가능 4·검토 큐 0·완료율 0/1 전부 동일. 돌연변이 13종 중 12종 red(초과 키 무시·`$` 앵커·하이픈 탈락·하한/상한 off-by-one·마지막 콜론 분해·예외 미포착·자리표시 검사·미검사·예외→conforming·needle에 자리표시 포함·needle에서 원본 누락), 1종(`record_type` 검사 제거)은 **등가 돌연변이** — 타 레코드는 그 키를 기대하지 않아 관측 차이가 없다. 보고서·조회 표면에는 이 필드를 노출하지 않았다(spec §4.9). store 실존 버전은 아직 `{7.0}`뿐이고 기록기 현행은 7.1 — E7 헤더가 둘을 병기한다.
- **E1 절단 되물림** (커밋 `12e2269`) — `rejectbench/recorder.py`의 4000자 맹목 절단을 ① 공백류 되물림 → ② 민감값 세 종(홈 절대 경로·복합 세션 ID·원본 세션 ID) 가로지름 회피(고정점, 항상) → ③ 상한 50% 미만이면 정돈을 버리는 폴백으로 바꿨다. 테스트 28건, 전체 게이트 472건 통과.
  적대 검증(5축 병렬 + finding별 반증, 47에이전트)에서 확인 14 / 반증 28. 반영한 것: **되물림 상한 경계의 음성 대조 부재**(없으면 사유 앞머리 홈 경로 하나로 본문이 0자가 되는 과잉 구현이 전량 green으로 지나갔다), 개행 절이 공허했던 테스트, 위양성 구조였던 테스트 헬퍼, 정규식 역추적 2차 시간(병리 입력 40ms → 선형 0.08ms), 실행 빈도를 잘못 적은 주석, 동어반복 상수 단언. 돌연변이 8종 전부 red 확인.
- **검토 큐 2건** — 둘 다 `unnecessary` 기록. 강등하지 않았다(진짜 operation 사건이라 강등은 데이터 세탁이고 D2와 충돌).
- **판정 기본값 정합성** — `DEFAULT_MODEL_ID`를 `gpt-4.1-mini`로. gpt-5 계열이 `temperature` 고정을 거부해 기본값끼리 배타적이었다. 결정성(`AGENTS.md:57`)을 지키는 쪽을 택했고 상수 수준 회귀 테스트를 걸었다.

**현재 표본** (2026-09-02 store 실측 — 이전 판의 "운영 사건 2건"은 낡은 값이었다)

- `guard_event` 7건 = **operation 6 + test 1**. operation 6건은 전부 `block-dangerous-git`·`project: reject-bench`(도구개발 파티션)
- **서로 다른 operation 세션 2개**: `claude:90d3bc6…`(08-29, 2건, 판정·검토 완료) · `claude:c92a961…`(09-01, **4건, 판정 0·검토 0**)
- 4건은 넷 다 `heredoc: True`에 `<command omitted>` — 문서 텍스트를 heredoc으로 쓰다 가드가 **명령 문자열 전문**을 매칭한 자기차단. 선례 2건과 성격이 같아 넷 다 `unnecessary`로 검토했다(강등하지 않았다 — 진짜 operation 사건이고 D2와 충돌한다)
- **그-외 파티션의 operation 사건은 0건** — reject-bench 밖 자연 발동은 아직 없다(미결 4의 발동 조건이기도 하다)

**현재 지표** (2026-09-02 `uv run python -m rejectbench.cli report`, 미처리 4건 판정·검토 후)

- 대표 지표(증거 기반 결정 완료율): **0/1 (0%)** — 보고서는 `block-dangerous-git`을 판정 가능 가드로 렌더한다(운영 세션 2·판정 가능 세션 2). **단 이건 전체 지표다.** D12에 따라 종료 조건은 그-외로 판정하고, 그-외 분모는 0이라 **미검증**이다. E7 파티션이 이 두 값을 나란히 내면 문면이 정리된다
- 정책 불일치율 **0/4** · 사용자 불필요 차단율 **6/6(100%)** · LLM-사용자 불일치율 **4/4(100%)** — 전부 "정책상 옳은 차단 + 사용자 불필요"
- PolicyVerdict 미처리 **0건** · 보류(`insufficient_context`) **2건** · UtilityReview 미처리 0건 · 보류 0건 — **검토 큐가 비었다**
- LossRecord 2건(`verdict_failure` — 판정 1차 실패의 흔적, 정상) · 손상 줄 0 · drift 0 · post-remove 0
- 스키마 버전: store 실존 `{7.0}` 29건(E2 뒤에도 재작성 없음), 기록기 현행 7.1 — 다음 자연 발동부터 7.1·`session_id_format` 채워진 레코드가 쌓인다

## 다음 할 일 (구체적 첫 행동 1개)

**E7의 첫 체크박스 — 실패 테스트를 먼저 쓴다.** `tests/test_report.py`에 도구개발(`project == "reject-bench"`)/그-외 사건이 섞인 임시 store 픽스처를 만들고, `operation_event_ids` 파생 지표 전부(정책 불일치율·불필요 차단율·불일치율·`correct_but_unnecessary`·`incorrect_but_useful`·운영 사건 수·판정/검토 미처리·보류 현황·증거 기반 결정 완료율)에 **전체·도구개발·그-외 세 값**이 각각 맞게 나오고, 분모 0인 파티션은 `미검증`으로 렌더링되며, **대표값은 그-외**라는 문면이 보고서에 있고, 스키마 버전 헤더가 기록기 현행(7.1)과 스냅샷 실존 집합(`{7.0}` 등)을 **다르면 병기·같으면 한 값**으로 내는지 단언한다. 합산 검산을 넣지 않는다는 사실도 테스트로 고정한다(결정 완료율은 가드 단위라 두 벌의 합이 전체와 다를 수 있음 — 한 가드가 전체에서는 판정 가능이면서 어느 파티션에서도 불가인 픽스처). 미적용 대상(손실·정정·강등·손상 줄·총 레코드·시험/unknown/unregistered 집계·기준선·교정 사이드카·가드별 표의 `project` 열)은 단일 값 그대로.

red 확인 후 `.dryforge/spec.md` §9대로 `rejectbench/report.py`에 구현한다 — 지표 정의는 `metrics`의 단일 정의를 재사용하고 재정의하지 않는다(파티션은 사건 집합을 가른 `Dataset` 사본이 아니라 `operation_event_ids`의 부분집합으로 계산하는 쪽이 안전한지 먼저 `metrics.py`·`report.py`를 읽고 정한다). 비노출 계약(홈 경로·세션 식별자·사건 id·enforcement 경로·guard_hint 미렌더링) 회귀 확인. 커밋 `feat: partition operation metrics by tool-development origin`. 그 뒤 완료 게이트(`uv run pytest` exit 0 캡처 + 정본 3종 갱신 확인)를 점검하고 사용자에게 **push·PR 여부**를 묻는다 — push는 사용자 요청 시만.

## 미결 결정

1. **판정 가능 가드는 그-외 기준으로 아직 0이다.** 전체 지표로는 `block-dangerous-git`이 서로 다른 2세션을 채워 종료 조건의 표본 절을 이미 넘었지만, **두 세션 다 도구개발(`project: reject-bench`)이다.** 2026-09-02 결정: **종료 조건은 그-외 파티션으로 판정한다**(결정기록 D12) — 자기차단만으로 성립을 선언하면 대표 주장이 자기생성 증거에 얹히고, 그건 ADR 0001이 기각한 순환이자 E7 파티션이 막으려던 것이다. 4주 종료 판정은 2026-09-26경이고, 그때까지 그-외 자연 발동이 없으면 **"운영 빈도 미검증"을 그대로 기록하고 종료**한다. **강제 발동은 표본 규율이 금지한다.**
2. **판정기 신뢰도** — 교정 8/8을 통과한 두 모델이 같은 사건에 다른 답을 냈다(`correct_block` vs `insufficient_context`). 기록은 `docs/기준선-측정.md`의 "부록 — 판정기 안정성 관찰". `기획-입력.md:70`은 `insufficient_context`가 지배적이면 "캡처 설계 실패"로 규정하는데 **"지배적"이 정의돼 있지 않았다.**
   **2026-09-02 선등록(판정 실행 전에 못 박았다)**: **확정 판정 5건 이상에서 `insufficient_context` 50% 이상**이면 캡처 설계 실패로 선언한다. 이 임계는 종료 조건과 달리 **전체 지표 기준**이다 — 캡처 설계는 기구의 성질이라 파티션과 무관하고, 그-외로 재면 분모가 0이라 영원히 발동하지 않는다. 결과를 보고 선을 그으면 사후 합리화라 실행 전에 정했다(결정기록 D12).
   **판정 실행 결과 — 임계는 미발동이지만 더 나쁜 신호가 나왔다.** `insufficient_context` 2/6 = 33%로 임계 아래다. 그런데 **두 모델 대조 6건 중 5건(83%)이 불일치**다:

   | 세션 | gpt-4.1-mini | gpt-5-mini |
   |---|---|---|
   | 90d3bc6 ×2 | correct_block · insufficient_context | correct_block · correct_block |
   | c92a961 ×4 | incorrect_block ×4 | correct_block ×3 · insufficient_context |

   **그리고 확정값은 실행 순서가 정했다.** 확정 판정은 사건별 최신 레코드인데, 두 번째 모델을 `--rejudge`로 나중에 돌렸기 때문에 gpt-5-mini의 답이 전부 확정이 됐다. 순서를 뒤집었으면 정책 불일치율이 0/4가 아니라 4/6이 됐을 것이다. **불일치율 83%와 이 순서 의존성은 어느 선등록 규칙에도 안 걸린다** — O2에서 "판정기가 증거로 쓸 만한가"를 별도로 판단해야 한다. 임계를 하나 더 선등록할지도 미결이다.
3. **가드 자체의 수명주기 결정** — 오탐 구조(명령 문자열 전문 매칭, 인용·heredoc 무구분)는 O2의 `keep`/`modify` 재료다. **지금 고치지 말 것** — 관측 중 가드를 바꾸면 기구 변경이 또 하나 는다.
4. **MCP 서버 전역 등록** — 다음 주기. E0이 교차 저장소 사건을 실제로 만든 뒤 판단한다(2026-09-02 현재 그-외 사건 0건 — 아직 판단 재료가 없다).
5. **외부 검증(X1)** — O2 뒤에만.
6. **판정 사유 적재의 맹목 절단** — `judge.py:665-666`이 `PolicyVerdict.reason`을 되물림 없이 4000자에서 자르고, 판정 프롬프트가 `rubric.py:117`의 `target_path`(홈 절대 경로)와 `:120`의 사유 텍스트를 실으므로 **E1이 없앤 것과 같은 세 종의 파편**을 계속 만든다. 그 파편은 `mcp_server.py:361`로 나가면서 출력 경계를 그대로 통과한다(실행 재현).
   **2026-09-02 처분(결정기록 D11)**: **계약 문면만 고쳤다** — `spec.md` §3 보장 범위에 "적재 경로로도 한정된다"를, §5 첫 잔여 한계에 판정 사유 경로를 넣었다. **코드는 다음 주기**다. 노출이 오늘 0이고(`PolicyVerdict.reason` 실측 최대 226자 = 상한의 5.7%, `rubric.py:49`가 "한두 문장" 지시), E1의 작업 대상은 두 파일뿐이며, judge.py를 고치면 E5②가 고정한 기구 변경 목록에 다섯 번째가 붙기 때문이다.
   **다음 주기 처방(확정)**: judge.py 절단을 recorder와 **같은 되물림으로 통일**하고 공용 함수로 뽑는다. 민감값은 판정 대상 사건에서 얻는다. **판정 번들은 건드리지 않는다** — 바꾸면 `context_bundle_hash`가 불연속해져 기존 판정과 비교가 끊기고 그 자체가 판정기 기구 변경이다.
7. **판정 번들의 순수성이 자유 텍스트로 우회된다** (2026-09-02 확인, 기록만) — `rubric.py:93-94`는 "비공개 세션 식별자와 출처 필드도 판정 입력이 아니므로 제외한다"고 명시하고 `AGENTS.md:25`가 그 순수성을 테스트로 지킨다. 그런데 `reason`·`target_path`가 자유 텍스트라 **같은 홈 경로와 세션 ID가 그 안에 실려 외부 판정 API로 나간다.** 취향이 아니라 계약과 동작의 불일치다. **이번 주기 변경 없음** — 번들 내용을 바꾸면 `context_bundle_hash`가 불연속해지고 그 자체가 관측창 안 기구 변경이다(미결 3과 같은 논리). 다음 주기 입력.

## 함정

- **전역 가드 자기차단이 곧 운영 사건이다.** 위험 패턴 텍스트를 heredoc/echo로 쓰면 전역 가드가 **명령 문자열 전문**을 매칭해 차단하고, 그 발동이 실제 `operation` 사건으로 기록된다. 파일 도구(Write/Edit)로 쓸 것.
- **CLI 인자 값도 명령 문자열이다.** `review record --note`·`review demote --reason`·`decide --rationale`·`judge --rejudge-reason`에 위험 패턴을 인용하면 그 명령 자체가 차단된다. 풀어서 쓸 것.
- **시험·강제 발동은 `REJECTBENCH_TEST_SESSION` 아래에서만.** 기록 대상 store는 `REJECTBENCH_STORE`로 임시 경로에 돌린다(기록기 전용 env — 조회 서버에는 통하지 않는다). E0 검증이 쓴 방식: 합성 페이로드를 파일로 쓰고 래퍼를 직접 호출.
- **E0 이후 작업 트리 코드가 전역에서 해석된다.** `pyproject.toml:15`가 `package = false`라 훅은 설치본이 아니라 작업 트리를 import한다. E1·E2 편집 중 중간 저장 상태가 import 불가면 **모든 저장소의 모든 Bash 호출**에서 가드가 죽는다 — 관측창 한복판에서. **체크박스마다 `python -c "import rejectbench.wrapper"` 스모크를 돌릴 것.**
- **판정은 `.env`를 자동으로 읽지 않는다.** 코드가 `os.environ`만 본다 — `set -a; . ./.env; set +a`로 주입한다. 키 값을 명령줄·파일·로그·레코드에 남기지 말 것.
- **판정 기본값을 gpt-5 계열로 되돌리지 말 것.** `temperature` 고정을 거부해 판정이 전량 실패한다. 되돌리면 `tests/test_judge.py::TestDefaultConfigCoherence`가 걸린다. 최신 모델이 필요하면 `--model`과 `--model-settings '{}'`를 함께 주되 그 판정은 비결정적이다.
- **테스트는 임시 store만.** 운영 `data/`에 닿으면 conftest autouse 게이트가 하드 실패시킨다. 네트워크 금지.
- **절단 되물림에는 하한이 두 개가 아니라 하나다.** 하한(상한의 50%)은 **공백 되물림에만** 걸린다. 민감값 되물림에는 하한이 없어서, 사유 앞머리에서 민감값이 절단점을 가로지르면 본문이 0자까지 줄 수 있다 — 계약(spec §3.3)이 그렇게 정한 것이다. 안전(파편 미생성)이 정돈보다 위다. 이 경로가 실제로 `insufficient_context`를 늘리는지는 관측 대상이다.
- **되물림은 "가로지를 때만"이다.** 본문 안에 온전히 든 등장까지 되물리면 사유가 통째로 사라진다. `_retreat_past_sensitive`의 탐색 창 하한 `max(0, cut - span + 1)`이 그 조건이고, 음성 대조 3건이 이 경계를 지킨다 — 지우지 말 것.
- **`session_id_format`은 진단값이지 게이트가 아니다.** 비준수여도 저장값·origin·가드 결과는 불변이고, 자리표시·구형·술어 예외는 셋 다 `unchecked`로 합쳐진다(의도된 선택, spec §4.6). 파서 완화는 `guard_event`의 `session_id_format` **하나** 누락에만 걸린다 — 다른 키 누락·초과 키는 여전히 손상 줄이고 테스트가 그걸 고정한다. dataclass 기본값이 `unchecked`라 기록기 밖에서 만든 GuardEvent는 검사 없이 `unchecked`다.
- **자리표시 `unknown`은 needle이 아니다.** `_sensitive_values`가 `split_session_id`로 원본을 얻지만 `context_available`이 거짓이면 원본을 빼고, E3의 준수 부류도 UUID 문법이라 자리표시는 들어가지 않는다. 일반 단어 `unknown`을 민감값으로 삼으면 사유가 부당하게 줄고 응답 텍스트가 훼손된다 — 음성 대조 `test_placeholder_raw_part_is_not_a_truncation_needle`이 이걸 고정한다.
- **E3 자격은 UUID 문법이지 §4 술어가 아니다.** `mcp_server._UUID_SYNTAX`를 `records.SESSION_ID_RAW_RULE`로 바꾸면 날짜꼴·hex 조각 세션 ID가 준수가 되어 무경계 치환이 `occurred_at`·`content_hash`·`event_id`를 뭉갠다 — `test_rule_shaped_but_non_uuid_ids_…`가 이걸 고정한다. 분해는 반드시 `records.split_session_id`(첫 `:`) — 두 벌 만들지 말 것.
- **미등록 가드 오류는 호출자 입력을 되비추지 않는다**(002 §4.2). 오류 경로에서 별칭 치환을 관찰하려면 메아리가 나가는 타입 오류(목록형 `guard_id` 등)를 써야 한다 — E3 양성 대조가 처음에 이걸로 헛발을 디뎠다.
- **`pytest -q`는 요약 줄이 안 나온다.** `pyproject.toml`의 `addopts`에 이미 `-q`가 있어 `-q`를 더 주면 `-qq`가 되고 `N passed` 줄이 사라진다. 건수가 필요하면 `uv run pytest | tail -1`. 돌연변이 판별 플러그인은 `FAILED` 줄로 세고, **이름으로 import된 바인딩(`rejectbench.X`·`recorder.X`)까지 같이 갈아끼워야** 테스트가 원본을 부르지 않는다.
- **전역 설정 편집은 스냅샷·롤백을 거친다**(`.dryforge/handoff.md` 하드 게이트 5). E0·E4 둘 다 끝났고 스냅샷 `~/.claude/settings.json.rejectbench-pre-e0-20260901`·`…pre-e4-20260902`는 git 밖 상태의 유일한 되돌림 수단이라 남긴다. 이 주기에 전역 설정을 더 만질 태스크는 없다.
- **v0 수집기는 되살리지 않는다.** 일반 텔레메트리 수집 종료는 사용자 결정이다. `PermissionRequest`·`PermissionDenied`의 관측이 전역에서 사라졌다는 사실은 E5②가 기구 변경으로 적는다.
- **`.claude/settings.json.bak-1786598695`, `.codex/config.toml.bak-1786598644`는 사용자 소유 비추적 백업.** 스테이징·수정·삭제 금지.
- **조회 금지는 해제됐다**(기준선 측정 완료). 다만 E3 검증·실기동은 임시 store로만 — 완성된 기동 명령 한 줄이 `.dryforge/handoff.md` 하드 게이트 3에 있다.
- `.dryforge/`의 완료 주기는 번호 폴더(`001/`, `002/`), 진행 중 주기만 루트. 하드 게이트 9에 따라 루트 3종은 git 추적 대상이다.
