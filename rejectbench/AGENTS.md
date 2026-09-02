# rejectbench 패키지 — 모듈 규칙

v7 도메인 구현 전체가 이 패키지다. 행동의 정본은 `.dryforge/`의 주기 문서이며, 끝난 주기는 번호 폴더에 있고(최신 아카이브 `002/`) 진행 중 주기(003, 운영 경험 개선 증분)는 루트 3종이다.

## 모듈 맵

- `records` — 7개 레코드 스키마와 enum. 전역 규칙(스키마 버전·고유 id·UTC aware)과 필드 조합 강제(origin↔origin_evidence 닫힌 표, unregistered↔참조 셋 배타)는 전부 여기서 생성 시점에 막는다. 세션 ID 복합값의 **분해 함수 `split_session_id`**(첫 `:` 기준, 이후 전부가 원본)와 E2 진단 술어 `session_id_format`(`SESSION_ID_RAW_RULE` 한 곳, 8~128자 `[A-Za-z0-9_-]`)도 여기 있다 — 기록기의 needle 산출과 조회 표면의 가림 분류가 **같은 분해 함수**를 쓴다. 파서는 `guard_event`의 `session_id_format` 키 하나가 없는 7.0 구형만 `unchecked`로 수용하고 나머지 키 불일치는 손상 줄이다.
- `hashing` — 정규화 직렬화 해시. `content_hash` 도메인은 의미 5필드(purpose·policy·exceptions·allow_examples·block_examples)만이다.
- `origin` — 출처 결정표와 강등 전이 규칙(순수 함수).
- `store` — append 전용 JSONL, flock + O_APPEND 단일 write. `production_root()`는 패키지 파일 위치 기준 `<repo>/data/v7`라서 어느 저장소에서 호출돼도 중앙으로 간다.
- `dataset` — append 순서 보존 적재와 참조 무결성 검사. 선행성 판정은 append 순서가 근거고 벽시계는 이상 신호 표시용이다.
- `metrics` — 확정값/보류값/미처리 분류와 판정 가능 가드(분모) 단일 정의.
- `registry`, `cli` — GuardSpec 등록부(버전 강제=content_hash 비교)와 전체 서브커맨드.
- `recorder`, `scrub`, `wrapper` — 발동 기록 경로. 래퍼는 가드의 stdout/stderr/exit를 그대로 투명 전달하고, 기록은 최선 노력이다. 차단 사유 절단은 공백 구분 단위 되물림 → 이 사건의 민감값 세 종(홈 절대 경로·복합 세션 ID·원본 세션 ID) 가로지름 회피(항상) → 상한 50% 하한 폴백 순이고, 세션 ID 형식 진단(`session_id_format`)은 자리표시·술어 예외 모두 `unchecked`다. 두 경로의 예외는 `assemble_event` 안에서 닫혀 사건이 LossRecord로 강등되지 않는다.
- `judge`, `rubric` — 세션 뒤 정책 판정(OpenAI, stdlib urllib). 교정 레코드는 메인 store가 아니라 사이드카 `calibration.jsonl`에 쌓인다.
- `review`, `decision` — 전수 검토 큐와 수명주기 결정.
- `report` — Markdown 보고서. 기준선은 store 루트 `baseline.json` 관례를 읽는다(없으면 "미측정" 출력).
- `mcp_server` — 읽기 전용 조회 표면(stdio MCP, 도구 3종). 기존 공개 함수를 합성만 하고 아무것도 쓰지 않는다. **패키지에서 MCP SDK를 import해도 되는 유일한 모듈**이며, 나머지 모듈은 SDK 없이 그대로 돌아야 한다(테스트가 이 경계를 못박는다).

## 불변식 (어기면 데이터가 오염된다)

- **기록 실패가 가드 결과를 바꾸면 안 된다.** recorder/wrapper의 어떤 변경도 예외를 호출자 쪽 exit 2로 새게 하면 안 된다. 유실 사다리는 주 저장 → LossRecord → OS 임시 경로/stderr 순이고, "절대 보장"이라 주장하지 않는다.
- 원본 레코드는 불변 — 정정은 amendment append뿐. 유효 출처는 `dataset.effective_origin`으로 파생하며, `operation` 승격 전이는 스키마가 거부한다.
- 가치 산입(countable)·`post-remove` 표시·관측 도중 신규 가드 표기는 **저장값이 아니라 파생 계산이 정본**이다. recorder는 `post_remove=False`로 적고, 표시 시점에 `decision.post_remove_event_ids`를 쓴다. 산입을 입력으로 받는 함수를 추가하지 말 것.
- 판정 입력은 사건 + 그 사건이 참조한 정확한 GuardSpec + 루브릭뿐이다. 검토·결정·집계·세션 id를 bundle에 넣으면 테스트(`bundle 순수성`)가 막는다 — 그 테스트를 완화하지 말 것.
- 전문 저장 금지: `ActionSummary`는 구조화 필드만 받고 초과 키를 거부한다. reason은 `redact_command_echo`(명령 되풀이 제거) → `scrub_text`(비밀 치환) → 4000자 절단 순서다. 절단점은 맹목이 아니라 되물림이다(003 spec §3) — 되물림은 "가로지를 때만"이며, 본문 안에 온전히 든 민감값 등장까지 되물리면 사유가 통째로 사라진다. 이 보장은 `recorder`의 `GuardEvent.reason` 경로에만 있고 `judge.py`의 `PolicyVerdict.reason` 절단은 맹목 그대로다(다음 주기).
- **조회 표면의 비노출은 출력 직전 단일 경계다.** 응답으로 나가는 모든 레코드 유래 문자열 값이 직렬화 직전에 경계를 정확히 한 번 지나고, 홈 절대 경로는 꼬리를 보존한 채 `~`로, 세션 ID는 응답 안에서만 유효한 순번 별칭으로 바뀐다. 고정 JSON 키는 공개 응답 스키마이므로 경계가 변경하지 않는다. 세션 식별자가 홈 경로를 품으면 별칭화를 먼저 한다. 저장 세션 ID는 **출력 시점에, 값의 모양으로** 두 부류로 나뉜다(003 spec §5 — 저장 필드와 무관하므로 과거 레코드도 자동 포함): `records.split_session_id`로 분해한 원본 부분이 **UUID 문법**이면 복합값과 원본 둘 다 **단어-속-포함(임의 부분문자열)**까지 같은 별칭으로 바뀌어 원문 0회를 보장하고, 그 외(자리표시 `harness:unknown`·UUID 비충족·`:` 없는 값)는 값 전체·완전한 토큰일 때만 바뀐다 — `unknown` 같은 일반 단어와 `e`·`-` 같은 유효한 짧은 값이 일반 텍스트·날짜·경로를 훼손하지 않게 하기 위해서다. 자격은 E2 진단 술어(8~128자)가 **아니다**: 그 술어로 가르면 날짜꼴·hex 조각이 준수가 되어 무경계 치환이 타임스탬프·해시·event_id를 뭉갠다. 일반 값 전체·토큰이 ID와 우연히 같으면 비노출을 우선해 별칭화한다. 긴 needle 우선·단일 패스 치환(별칭 재치환 방지)은 두 부류 공통이다. 필드별로 골라 지우는 방식으로 되돌리지 말 것 — 오류 메시지와 구현물 대조 사유(등록된 전역 가드의 `enforcement_ref.script_path`는 홈 절대 경로다) 같은 구멍이 실제로 지적됐고, 경계가 한 곳이어야 새 필드가 늘어도 기본이 안전하다.
- **별칭↔원문 매핑은 어디에도 저장하지 않는다.** 호출 메모리에만 있고 응답이 끝나면 사라진다. 저장하면 쓰기 금지와 식별자 비지속 원칙을 동시에 깬다. 응답 간 별칭 안정성은 보장 대상이 아니다.
- **비노출은 양성 대조로만 통과된다.** 홈 경로와 세션 식별자를 실제 심은 픽스처로 도구 3종과 오류 경로의 응답 전체를 훑어 해당 원문 0회를 확인한다 — 값 전체·완전한 토큰 픽스처에 더해 **UUID 문법 준수 ID를 일반 단어 속에 파묻은 픽스처**(복합값·원본 단독 등장 모두), 자리표시·비준수 ID 주변 일반 텍스트의 비훼손, 준수 ID가 타임스탬프·`content_hash`·`event_id`와 겹칠 때 그 값들의 불변까지. 깨끗한 픽스처의 출력이 깨끗한 것은 증거가 아니다. **잔여 한계 둘**(계약 명시): 완전한 ID가 아닌 **조각**은 어느 부류에서도 매칭 대상이 아니다 — E1이 적재 시점에 이 사건의 민감값 세 종에 한해 새 파편 생성을 막지만, 타 세션 ID의 파편과 `judge.py`의 판정 사유 절단이 만드는 파편은 남을 수 있다. 그리고 **UUID 비충족 세션 ID의 원본 부분 단독 등장은 별칭 대상이 아니라 원문으로 나갈 수 있다** — 일반 텍스트 훼손 회피를 위한 의도적 선택이다.
- 조회 표면의 한 응답에서 **`records.jsonl`은 한 스냅샷만 근거로 삼는다.** 가드 목록·가드별 증거는 호출당 한 번 적재하고 등록부 조회도 그 `Dataset.specs_by_key`로 한다 — `GuardRegistry`는 생성자에서 자체 `load()`를 하므로 함께 쓰면 한 응답이 서로 다른 두 스냅샷을 보게 된다. 보고서는 기존 생성 함수가 안에서 스스로 적재하므로 두 번 읽는 것이 불가피한데, **정화 경계용 스냅샷을 보고서 생성 뒤에** 잡아 안전을 보장한다: store가 append 전용이라 나중 적재의 세션 식별자 집합은 앞선 것의 상위집합이고, 순서를 뒤집으면 그 사이에 들어온 세션이 별칭 없이 새어 나간다. 사이드카 `calibration.jsonl`은 예외로 경계 구성 뒤에 지연 적재된다 — 교정 레코드에는 세션 식별자가 없어 별칭 대상이 아니고, 거기서 나오는 문자열은 그대로 경계를 지난다.

## 경계

- `hooks/`는 세션 연속성 인프라(`continuity.py`·`codex_handoff_gate.py`)로 이 패키지와 무관하다 — 수정 금지. v0 수집기 `hooks/collect.py`는 2026-09-02 완전 퇴역했다(003 E4, 전역 배선 4건 제거 — 캡처는 `docs/배선-목록.md`). 되살리지 말 것.
- 운영 store `data/`에 테스트가 닿으면 `tests/conftest.py`의 autouse 게이트가 하드 실패시킨다. 이 게이트를 끄지 말 것.
- 실제 가드 스크립트(전역 `block-dangerous-git.sh`, reply-gate `protect-live-reports.sh`)는 해시 계산용 바이트 읽기만 한다. 실행·수정 금지(래퍼가 배선 경로에서 실행하는 것만 예외).

## 함정

- 배선 명령은 반드시 `uv run --project /Users/ian/workspace/reject-bench python -m rejectbench.wrapper …` 형태여야 한다. 맨 `python3 -m`은 패키지를 못 찾는다(package=false, pythonpath는 pytest 전용).
- 전역 가드는 Bash 명령 문자열 **전문**을 패턴 매칭한다. 위험 패턴 텍스트가 포함된 픽스처·문서를 heredoc/echo로 쓰면 자기 차단된다 — 파일 도구(Write/Edit)로 쓸 것. (이 발동 자체는 도구가 기록한다.)
- 판정 기본 설정 `{"temperature": 0}`은 모델이 거부할 수 있다 — 그 경우 미처리로 안전 실패하며, settings 주입으로 조정하고 `model_settings_hash`가 바뀐 재판정 규율을 따른다.
- 같은 (spec·rubric·model·settings) 조합의 교정 실패 기록은 재사용된다 — 동일 설정 강제 재교정 경로는 없고, 설정을 바꾸면 자연히 재교정된다. 일시적 API 오류로 미통과가 박제될 수 있으니 그 경우 settings를 바꿔 재교정한다.
- test 플래그가 켜져 있어도 실행 맥락(세션 식별)이 없으면 `unknown`이 우선이다 — spec §3.2 규칙 1의 자구("플래그 켜짐 → 항상 test")와 다른 보수적 선택이며, 필요하면 사유 있는 amendment로 `test` 강등해 정정한다.
- **MCP SDK는 사용자가 표준 라이브러리 직접 구현 권고를 듣고도 기각하고 택한 결정이다**(프로토콜 개정 추종 부담 회피). "의존성을 줄이는 개선"으로 stdlib 재구현으로 되돌리지 말 것. 범위는 조회 표면뿐이고, 그 밖의 모듈에 SDK를 끌어들이는 것도 같은 이유로 금지다.
- 설치된 SDK는 mcp 2.x다 — v1의 `FastMCP`는 `mcp.server.mcpserver.MCPServer`로 이름이 바뀌었고 `mcp.server.fastmcp`는 안내 메시지를 내며 import에 실패한다. `ToolError`를 던지면 SDK가 `Error executing tool <이름>: ` 접두를 붙여 내보내므로, 사유 문자열 자체는 이미 정화된 한 줄이어야 한다. 반대로 예상 못 한 예외가 새면 SDK가 사유를 지우고 `Error executing tool <이름>`만 남겨, 호출자는 아무 단서도 못 받는다.
- 위 배선 명령이 절대 경로여야 하는 실제 원인은 `uv run --project`가 uv의 환경 탐색 경로만 정할 뿐 **작업 디렉터리를 바꾸지 않는다**는 데 있다 — 실행 디렉터리가 저장소 밖이면 `--project`를 줘도 모듈을 못 찾는다. `.mcp.json` 등록은 `env.PYTHONPATH`로 이 의존을 없앴으니, 새 실행 지점을 만들 때도 실행 디렉터리를 가정하지 말 것. 이 등록은 가드 발동을 잡는 훅 호출 지점 목록과는 별개다.
- SDK 하나를 더하면서 lock에 패키지 30개(이 플랫폼 설치 기준 28개)가 들어왔고 그중에는 HTTP 서버 스택(starlette·uvicorn·sse-starlette)과 암호 라이브러리가 섞여 있다. 이 표면은 **stdio만** 쓴다 — 원격 전송은 계약상 범위 밖이고, 그 스택이 깔려 있다는 사실이 HTTP 노출을 허용하지 않는다. 반대로 "안 쓰는 의존성"으로 보고 걷어내려 하지도 말 것: 전부 SDK의 무조건 의존이라 개별 제거가 불가능하다.
- 그래서 예외를 흘리지 말고 정화된 `ToolError`로 바꿔 던진다. 새는 예외는 서버 **stderr**에 전체 트레이스백까지 찍는데, stderr는 정화 경계 밖이고 MCP 호스트가 로그로 걷어간다 — 새로 `raise`를 넣을 때 메시지에 원본 경로나 세션 식별자를 담지 말 것. 형식이 맞아도 변환이 터질 수 있다(자릿수 한도를 넘는 `int()` 등) — 검증과 변환을 함께 감쌀 것.
- `decide modify`에서 `--enforcement-script`를 생략하면 새 버전에 enforcement_ref가 없어 이후 발동이 구버전에 연결되고 반영 확인이 `unverifiable`로 뜬다 — modify 시 스크립트 경로를 항상 넘길 것.
- E0 이후 **작업 트리 코드가 모든 저장소의 Bash 훅에서 해석된다**(`package = false` + 인라인 `PYTHONPATH`). `records`·`recorder`·`wrapper`를 고치는 중간 저장 상태가 import 불가면 전역 가드가 관측창 한복판에서 죽는다 — 편집마다 저장소 밖 cwd에서 `import rejectbench.wrapper` 스모크를 돌릴 것.
- `session_id_format`은 진단값이지 게이트가 아니다 — 비준수여도 저장값·origin·가드 결과는 불변이고, 조회 표면의 가림 분류에도 쓰지 않는다(그쪽 자격은 `mcp_server._UUID_SYNTAX`). 자리표시의 원본 `unknown`은 기록기 needle에도, 조회 표면 준수 부류에도 들어가지 않는다.

## 테스트 규칙

- 임시 디렉터리만. 네트워크 금지(판정은 fake 전송 주입). 실제 가드는 mock 스크립트로 인터페이스(exit 2+stderr / deny JSON+exit 0)만 재현.
- 새 행동은 red→green으로: 실패 테스트 확인 후 구현. 전체 게이트는 `uv run pytest`(hooks 연속성 회귀 포함) 하나다.
