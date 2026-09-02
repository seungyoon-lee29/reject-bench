"""읽기 전용 MCP 증거 조회 서버 (T7-1, spec §3~§5).

AI 코딩 세션이 가드 발동 증거를 그 자리에서 물어볼 수 있게 하는 조회 표면이다.
v1 코어(recorder·judge·review·decision·report)는 이 모듈 없이도 그대로 돌아가고,
MCP SDK import는 이 모듈 안에만 있다.

## 읽기 전용 계약 (정밀 선언)

디스크에서 읽는 것은 정확히 둘뿐이다.

1. store 루트의 레코드 파일 — `records.jsonl`, 사이드카 `calibration.jsonl`,
   기준선 `baseline.json`
2. 등록된 `enforcement_ref`가 가리키는 가드 스크립트의 **바이트** (해시 대조용,
   `decision.check_enforcement` 재사용)

어떤 파일·디렉터리도 만들거나 고치거나 덧붙이지 않는다. store가 없어도
디렉터리를 만들지 않는다. 보고서를 파일로 내보내는 경로가 없다. 가드 스크립트는
바이트로 읽기만 하고 절대 실행하지 않는다.

## 신선도

호출마다 store를 다시 읽는다 — 캐시도, 상주 상태도 없다. 꼬리 부분 줄은 기존 적재
규약대로 건너뛰고 손상 줄 수로 드러낸다.

`list_guards`·`guard_evidence`는 한 호출에 `records.jsonl` 스냅샷 **하나**만 쓴다 —
`GuardRegistry`가 생성자에서 자체 `load()`를 하므로 등록부 조회도 이미 적재한
`Dataset.specs_by_key`로 한다. 사이드카 `calibration.jsonl`은 예외로 필요할 때
지연 적재된다(경계 구성 뒤일 수 있다). 교정 레코드에는 세션 식별자가 없어 별칭
대상이 아니고, 거기서 나온 문자열도 다른 값과 똑같이 경계를 지난다.

`get_report`만 한 호출에 두 번 적재한다. 보고서 본문의 정본은 v1 코어의
`generate_report`이고 그 함수가 자체 `load()`를 하기 때문이다(그 시그니처를 바꾸는
것은 기존 모듈 수정이라 범위 밖이다). 그래서 순서를 계약으로 못 박는다: **본문을
먼저 만들고, 출력 경계의 세션 목록은 그 뒤에 뜬 스냅샷에서 만든다.** store가 append
전용이라 나중 스냅샷의 세션 집합은 앞선 것의 상위집합이고, 따라서 본문에 들어간
식별자는 반드시 경계가 알고 있다. 반대 순서면 두 적재 사이에 들어온 세션이 본문에는
있고 별칭 표에는 없어 원문 그대로 새 나간다.

## 출력 경계 (spec §5)

응답으로 나가는 **모든 레코드 유래 문자열 값**(오류 메시지 포함)은 직렬화 직전에
`OutputBoundary`를 정확히 한 번 지난다. 고정 JSON 키는 공개 응답 스키마이므로 바꾸지
않는다. 필드별로 골라 지우지 않는다 — 새 필드가 늘어도 기본이 안전해야 하기 때문이다.
경계가 하는 일은 둘이다.

- 홈 절대 경로 접두사를 `~`로 치환한다. 꼬리는 보존한다 — `target_path`는 판단에
  필요한 값이라 생략이 아니라 치환이어야 한다.
- 세션 식별자 원문을 순번 별칭(`S1`, `S2`, …)으로 치환한다. 별칭은 한 응답 안에서만
  유효하고, 별칭↔원문 표는 **어디에도 저장하지 않는다** (호출이 끝나면 메모리에서
  사라진다).

세션 식별자는 **출력 시점에, 값의 모양으로** 두 부류로 나뉜다(003 spec §5 — 저장
필드와 무관하므로 과거 레코드도 자동 포함된다).

- **준수 복합값** — `records.split_session_id`로 분해한 원본 부분이 **UUID 문법**을
  충족하면, 복합값과 원본 부분 둘 다 **단어-속-포함(임의 부분문자열)**까지 같은 별칭으로
  치환한다. 원본 ID는 transcript 경로처럼 복합 접두 없이 사유 텍스트에 나타날 수 있고,
  "원문 0회"는 식별자 원문 자체를 뜻하기 때문이다. 이 부류에 한해 응답 원문 0회를
  보장한다. 자격은 E2의 진단 술어(8~128자)가 **아니다** — 그 술어로는 `2026-09-01` 같은
  날짜꼴이 준수가 되어 무경계 치환이 타임스탬프·해시·event_id를 뭉갠다.
- **그 외** — 자리표시(`harness:unknown`), UUID 문법 비충족 값, `:` 없는 값: 값 전체·
  완전한 토큰 매칭을 유지한다. `unknown` 같은 일반 단어와 `e`·`-` 같은 짧은 값이 일반
  텍스트·날짜·경로를 훼손하지 않게 하기 위함이다. 이 부류의 원본 부분 단독 등장은
  별칭 대상이 아니며 원문으로 나갈 수 있다 — 의도된 한계다.

완전한 ID가 아닌 **조각**(과거 절단이 남겼을 수 있는 파편)은 어느 부류에서도 매칭
대상이 아니다. E1이 적재 시점에 이 사건의 민감값 세 종에 한해 새 파편 생성을 막는다.

비밀 제거는 v1 적재 시점 규칙이 이미 담당한다 — 이 경계는 그 위에 홈 경로와 세션
식별자를 더 막을 뿐 적재 시점 제거를 대체하지 않는다.

**정화가 절단보다 먼저다.** 오류에 되돌려 주는 호출자 입력은 상한이 있는데, 먼저
자르면 잘린 조각(홈 경로 접두 `/Users/ia…`, 세션 식별자 접두 `claude:ses…`)이 온전한
일치가 아니게 되어 치환이 그 조각을 놓친다. 그래서 절단은 반드시 `OutputBoundary`
안에서, 정화한 **뒤에** 한다.

## 인자 검증도 경계 안쪽에서 한다

SDK는 도구 본문보다 **먼저** pydantic으로 인자를 검증한다. 그 단계에서 거부하면
오류 텍스트가 SDK가 만든 여러 줄(문서 URL·입력 원문 그대로 메아리)이고 이 경계를
지나지 않는다. 그래서 도구 인자 선언은 어떤 값도 거부하지 않도록 느슨하게 두고
(`Any`), 타입 검증은 도구 본문 안에서 해 정화된 한 줄 `ToolError`로 돌려준다.

느슨한 선언 탓에 pydantic이 발행 스키마에서 지워 버리는 계약(필수 표기, JSON-Schema
타입)의 정본은 **`TOOL_INPUTS` 하나**다. `_EvidenceServer.list_tools`가 그 표대로
`tools/list` 스키마를 되싣고, 도구 본문의 필수 검증도 같은 표를 읽는다 — 발행과
강제가 각각 따로 적혀 조용히 어긋나는 길을 없앤다.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import Tool as MCPTool
from pydantic import Field

from rejectbench.dataset import Dataset
from rejectbench.decision import (
    DecisionError,
    DecisionRow,
    EnforcementCheck,
    EnforcementStatus,
    EventRow,
    GuardView,
    build_guard_view,
    check_enforcement,
)
from rejectbench.judge import JudgeCalibration, find_calibration, load_calibrations
from rejectbench.records import (
    CaptureStatus,
    GuardSpec,
    PolicyVerdict,
    UtilityReview,
    split_session_id,
)
from rejectbench.report import generate_report
from rejectbench.store import AppendStore, LoadResult, production_root

SERVER_NAME = "rejectbench-evidence"
SERVER_VERSION = "7.0.0"
UNEXPECTED_TOOL_ERROR = "조회 중 오류가 발생했다"

LIST_GUARDS_TOOL = "list_guards"
GUARD_EVIDENCE_TOOL = "guard_evidence"
GET_REPORT_TOOL = "get_report"

GUARD_ID_INPUT = "조회할 가드 id (필수, 문자열). list_guards가 돌려주는 guard_id 값."
VERSION_INPUT = (
    "맥락을 볼 GuardSpec 버전 (선택, 정수). 생략하면 최신 버전. "
    "사건·결정 목록은 버전과 무관하게 가드 전체다."
)


@dataclass(frozen=True)
class ToolInput:
    """도구 인자 하나의 단일 선언.

    인자 자체는 `Any`로 느슨하게 선언한다(SDK가 먼저 거부하면 그 오류 텍스트가 출력
    경계를 지나지 않기 때문이다). 그래서 "필수인가"와 "어떤 JSON 타입인가"의 정본은
    pydantic이 아니라 이 선언이고, 발행 스키마(`list_tools`)와 본문 검증이 **같은
    것을 읽는다** — 한 곳만 고치면 둘 다 따라오므로 조용한 표류가 생기지 않는다.
    """

    #: 인자 이름 (발행 스키마의 property 키이자 본문이 받는 이름).
    name: str
    #: 발행 스키마에 실을 설명. 클라이언트가 보는 문구다.
    description: str
    #: 발행 스키마에 실을 JSON-Schema 타입 조각. 없으면 기계가 읽을 타입이 사라진다.
    json_schema: dict[str, Any] = field(default_factory=dict)
    #: 필수 여부. 발행 스키마의 `required`와 본문의 누락 검사가 이 값 하나를 본다.
    required: bool = False


#: 도구별 인자 선언표 — 인자가 없는 도구는 빈 항목으로 둔다(발행 스키마 대조에 쓰인다).
TOOL_INPUTS: dict[str, tuple[ToolInput, ...]] = {
    LIST_GUARDS_TOOL: (),
    GET_REPORT_TOOL: (),
    GUARD_EVIDENCE_TOOL: (
        ToolInput(
            name="guard_id",
            description=GUARD_ID_INPUT,
            json_schema={"type": "string"},
            required=True,
        ),
        ToolInput(
            name="version",
            description=VERSION_INPUT,
            json_schema={"anyOf": [{"type": "integer"}, {"type": "null"}]},
        ),
    ),
}

#: 잘못된 인자를 되돌려 줄 때의 메아리 상한 — 진단에는 충분하고 응답은 한 줄로 남는다.
#: 자르기는 **정화 뒤**에만 한다 (`OutputBoundary.echo`).
MAX_ECHO = 120

#: 문자열로 온 version이 정수로 받아들여지는 형태 — 평범한 십진수 하나뿐이다.
#: `int()`에 그대로 맡기면 파이썬의 관대함(자릿수 구분 `_`, 전각·아랍숫자, `+` 부호)이
#: 새어 `"1_0"`이 조용히 10이 된다. `\d`는 유니코드 숫자를 포함하므로 `[0-9]`로 못 박는다.
_PLAIN_INT = re.compile(r"-?[0-9]+")
_ASCII_SPACE = " \t\n\r\f\v"

#: 판정 가능 가드 기준 — decision/metrics의 단일 정의를 문구로 함께 실어 보낸다.
DECIDABLE_CRITERION = (
    "서로 다른 operation 세션 2개 이상의 사건이 있고, 그 사건들의 정책 판정·"
    "유용성 검토가 모두 확정값인 가드"
)
UNPROCESSED = "미처리"

ALIAS_PREFIX = "S"

#: `fail(echo=...)`의 "메아리 없음" 표식 — `None`도 되돌려 줄 값이라 쓸 수 없다.
_NO_ECHO = object()

#: E3 가림 자격 — 원본 부분의 **UUID 문법** (8-4-4-4-12 hex). E2의 진단 술어
#: (`records.SESSION_ID_RAW_RULE`, 8~128자)와는 별개다: 그 술어로 자격을 정하면 날짜꼴·
#: hex 조각이 준수가 되어 무경계 치환이 타임스탬프·해시·event_id를 뭉갠다(003 spec §5).
#: 여기서 탈락한 값은 보호를 잃는 게 아니라 "그 외" 부류의 토큰 경계 매칭으로 떨어진다.
_UUID_SYNTAX = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _uuid_raw_part(session_id: str) -> str | None:
    """준수 복합값이면 그 원본 부분, 아니면 None. 분해는 E2와 같은 함수다."""
    _, raw = split_session_id(session_id)
    if raw is not None and _UUID_SYNTAX.fullmatch(raw):
        return raw
    return None


# --- 출력 경계 ----------------------------------------------------------------


class OutputBoundary:
    """응답 직렬화 직전의 단일 정화 경계 (002 spec §5, 003 spec §5).

    호출마다 새로 만든다. 세션 별칭 표는 이 객체의 수명(=한 호출) 안에만 있다.
    """

    def __init__(self, *, session_ids: tuple[str, ...] = (), home: str | None = None):
        if home is None:
            try:
                home = str(Path.home())
            except (RuntimeError, OSError):  # 홈을 알 수 없는 환경
                home = ""
        # 루트("/")를 홈으로 잡으면 모든 경로를 뭉갠다 — 그런 경우는 치환하지 않는다.
        self._home = home if home and home not in ("/", "") else ""
        # 긴 것부터 매칭해야 접두사가 겹치는 식별자를 잘라먹지 않는다. 별칭 번호는
        # store 순서가 아니라 실제 응답 문자열의 첫 등장 순서에 발급한다.
        stored = tuple(
            sorted(dict.fromkeys(sid for sid in session_ids if sid), key=len, reverse=True)
        )
        # needle → 별칭의 정본이 되는 저장 세션 ID. 준수 복합값은 원본 부분도 needle이고
        # **같은 정본**을 가리킨다(동일 별칭). 서로 다른 준수 복합값이 원본을 공유하면
        # 먼저 등록된(긴) 쪽의 별칭으로 — 가려짐은 보장하고 어느 별칭인지는 보장하지 않는다.
        self._needles: dict[str, str] = {}
        # 단어-속-포함까지 치환하는 needle. 그 외는 완전한 토큰일 때만 찾는다 — 구두점
        # 하나(`-`)도 유효한 저장값이라 무경계 치환을 전부에 허용하면 날짜·경로·가드 ID까지
        # 훼손된다.
        unbounded: set[str] = set()
        for sid in stored:
            self._needles.setdefault(sid, sid)
            raw = _uuid_raw_part(sid)
            if raw is not None:
                self._needles.setdefault(raw, sid)
                unbounded.update((sid, raw))
        self._aliases: dict[str, str] = {}
        # 한 번에 훑는 치환기 — 별칭이 다시 치환 대상이 되는 것을 원천 차단한다.
        patterns = [
            re.escape(needle) if needle in unbounded else rf"(?<!\w){re.escape(needle)}(?!\w)"
            for needle in sorted(self._needles, key=len, reverse=True)
        ]
        self._pattern = (
            re.compile("|".join(f"(?:{pattern})" for pattern in patterns)) if patterns else None
        )

    def _alias_for(self, session_id: str) -> str:
        alias = self._aliases.get(session_id)
        if alias is None:
            alias = f"{ALIAS_PREFIX}{len(self._aliases) + 1}"
            self._aliases[session_id] = alias
        return alias

    def _alias_for_needle(self, needle: str) -> str:
        return self._alias_for(self._needles[needle])

    def text(self, value: str) -> str:
        # 세션 식별자가 홈 경로를 품을 수 있으므로 별칭화가 홈 치환보다 먼저다.
        if value in self._needles:
            return self._alias_for_needle(value)
        out = value
        # 원본을 왼쪽부터 한 번만 훑는다 — 별칭 발급 순서가 곧 첫 등장 순서이고,
        # 이미 치환된 자리는 다시 보지 않으므로 같은 문자열이 두 번 바뀌지 않는다.
        if self._pattern is not None:
            out = self._pattern.sub(lambda match: self._alias_for_needle(match.group(0)), out)
        return out.replace(self._home, "~") if self._home else out

    def one_line(self, value: str) -> str:
        """오류 메시지용 — 정화 뒤 한 줄로 접는다 (스택 추적·줄바꿈 금지)."""
        return " ".join(self.text(value).split())

    def echo(self, value: Any) -> str:
        """오류에 되돌려 주는 호출자 입력 — 정화하고 한 줄로 접은 **뒤에** 자른다.

        **순서가 곧 안전이다.** 먼저 자르면 잘린 조각(`/Users/ia…`, `claude:ses…`)이
        온전한 일치가 아니게 되어 치환이 그 조각을 놓친다 — 정화 전 절단은 경계에
        구멍을 낸다. 문자열이 아닌 값은 `repr`로 적는다(서버 정보가 아니라 호출자
        입력이므로 되돌려 주는 것이 진단에 쓸모 있다). 공백뿐인 문자열도 `repr`로
        적는다 — 한 줄로 접으면 아무것도 남지 않아 사유가 잘린 것처럼 보인다.
        """
        try:
            raw = value if isinstance(value, str) and value.strip() else repr(value)
        except (RecursionError, ValueError):
            raw = f"<{type(value).__name__}>"
        text = self.one_line(raw)
        return text if len(text) <= MAX_ECHO else text[:MAX_ECHO] + "…"

    def sanitize(self, value: Any) -> Any:
        """응답 구조를 재귀 순회하며 레코드 유래 문자열 값을 한 번 정화한다.

        모르는 타입은 조용히 통과시키지 않고 막는다 — 새 필드가 늘어도 기본이
        안전해야 하므로, 순회할 수 없는 값은 노출이 아니라 실패로 끝낸다.
        """
        if isinstance(value, str):
            return self.text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, dict):
            # 키는 공개 응답 스키마이고 레코드 유래 문자열 값이 아니다. 세션 ID가
            # 짧거나 키 이름과 같아도 `events` 같은 계약 키를 바꾸면 안 된다.
            return {key: self.sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.sanitize(item) for item in value]
        if isinstance(value, (set, frozenset)):
            # JSON에 집합이 없으니 목록으로 편다. 응답이 흔들리지 않게 정렬한다.
            return sorted((self.sanitize(item) for item in value), key=str)
        raise TypeError(f"정화할 수 없는 값 타입이다 — 응답에 실을 수 없다: {type(value).__name__}")


# --- 한 호출의 스냅샷 ----------------------------------------------------------


class _Snapshot:
    """한 호출이 보는 store 스냅샷. 호출마다 새로 읽고 재사용하지 않는다."""

    def __init__(self, store: AppendStore):
        self.store = store
        self.load: LoadResult = store.load()
        self.dataset = Dataset(self.load.records)
        self._calibrations: tuple[list[JudgeCalibration], int] | None = None
        self.boundary = OutputBoundary(session_ids=self._session_ids())

    @property
    def calibrations(self) -> list[JudgeCalibration]:
        """사이드카는 필요한 도구에서만 읽는다 — 호출당 읽기 범위를 최소로 유지."""
        if self._calibrations is None:
            self._calibrations = load_calibrations(self.store.root)
        return self._calibrations[0]

    @property
    def calibration_corrupt(self) -> int:
        self.calibrations  # noqa: B018 - 지연 적재를 강제한다
        assert self._calibrations is not None
        return self._calibrations[1]

    def _session_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(event.session_id for event in self.dataset.events.values())
        )

    def specs_for(self, guard_id: str) -> list[GuardSpec]:
        return sorted(
            (spec for (gid, _), spec in self.dataset.specs_by_key.items() if gid == guard_id),
            key=lambda spec: spec.version,
        )

    def fail(self, message: str, *, echo: Any = _NO_ECHO) -> ToolError:
        """정화된 한 줄 사유만 담은 도구 오류 — 스택 추적도 저장 경로도 싣지 않는다.

        `echo`는 되돌려 줄 호출자 입력이다. 사유와 메아리는 **각각 한 번씩** 경계를
        지난다(메아리는 그 뒤에 상한으로 잘린다) — 이미 정화된 문자열을 다시 정화하지
        않는다.
        """
        reason = self.boundary.one_line(message)
        if echo is not _NO_ECHO:
            reason = f"{reason} {self.boundary.echo(echo)}"
        return ToolError(reason)

    def emit(self, payload: dict) -> str:
        return json.dumps(self.boundary.sanitize(payload), ensure_ascii=False, indent=2)


# --- 투영 --------------------------------------------------------------------


def _calibration_status(
    verdict: PolicyVerdict, calibrations: list[JudgeCalibration]
) -> str:
    """`passed | failed | none` — judge의 단일 정의(최신 교정 레코드 조회) 재사용."""
    record = find_calibration(
        calibrations,
        guard_spec_hash=verdict.guard_spec_hash,
        rubric_hash=verdict.rubric_hash,
        model_id=verdict.model_id,
        model_settings_hash=verdict.model_settings_hash,
    )
    if record is None:
        return "none"
    return "passed" if record.passed else "failed"


def _verdict_payload(
    verdict: PolicyVerdict | None, label: str, calibrations: list[JudgeCalibration]
) -> dict:
    if verdict is None:
        return {"status": UNPROCESSED}
    return {
        "verdict": verdict.verdict.value,
        "label": label,
        "reason": verdict.reason,
        "model_id": verdict.model_id,
        "judged_at": verdict.judged_at.isoformat(),
        "calibration_status": _calibration_status(verdict, calibrations),
    }


def _review_payload(review: UtilityReview | None, label: str) -> dict:
    if review is None:
        return {"status": UNPROCESSED}
    return {
        "utility": review.utility.value,
        "label": label,
        "note": review.note,
        "reviewed_at": review.reviewed_at.isoformat(),
    }


def _event_payload(snapshot: _Snapshot, row: EventRow) -> dict:
    # GuardView의 행은 요약이라 reason·행동 요약·판정 상세가 없다 — Dataset에서 채운다.
    event = snapshot.dataset.events[row.event_id]
    action = event.action
    return {
        "event_id": row.event_id,
        "occurred_at": row.occurred_at.isoformat(),
        # 원문 세션 식별자를 넣고 출력 경계에서 별칭으로 바꾼다 (spec §5).
        "session": row.session_id,
        "effective_origin": row.effective_origin.value,
        "capture_status": row.capture_status.value,
        "action": {
            "tool_name": action.tool_name,
            "command_verb": action.command_verb,
            "target_path": action.target_path,
            "heredoc": action.heredoc,
        },
        "reason": event.reason,
        "policy_verdict": _verdict_payload(
            snapshot.dataset.latest_verdict(row.event_id),
            row.verdict_label,
            snapshot.calibrations,
        ),
        "utility_review": _review_payload(
            snapshot.dataset.latest_review(row.event_id), row.utility_label
        ),
        "decidable": row.decidable,
        "markers": {
            "post_remove": row.post_remove,
            "drift": row.drift,
            "partial": row.capture_status is CaptureStatus.PARTIAL,
        },
    }


def _decision_payload(row: DecisionRow) -> dict:
    decision = row.decision
    annotation = row.annotation
    return {
        "decision_id": decision.decision_id,
        "decision": decision.decision.value,
        "decided_at": decision.decided_at.isoformat(),
        "evidence_event_ids": list(decision.evidence_event_ids),
        "rationale": decision.rationale,
        "resulting_guard_version": decision.resulting_guard_version,
        # 산입은 저장값이 아니라 파생 계산이다 (decision.annotate_decision).
        "countable": annotation.countable,
        "no_event_guard": annotation.no_event_guard,
        "exclusion_reasons": list(annotation.reasons),
    }


def _unreadable_enforcement(spec: GuardSpec, exc: OSError) -> EnforcementCheck:
    """구현물 파일이 있는데 읽히지 않는 경우 — §4.2가 요구하는 `unverifiable` + 사유.

    `check_enforcement`는 "파일 없음"(RegistryError)만 잡는다. 권한 등으로 읽기가
    막히면 `read_bytes()`의 OSError가 그대로 올라와 증거 응답 전체를 날린다. 기존
    모듈을 고치지 않고 이 표면에서 같은 의미로 접는다.
    """
    ref = spec.enforcement_ref
    where = ref.script_path if ref is not None else "(경로 없음)"
    return EnforcementCheck(
        EnforcementStatus.UNVERIFIABLE,
        f"구현물 파일을 읽을 수 없다: {where} ({type(exc).__name__})",
    )


def _safe_check_enforcement(spec: GuardSpec) -> EnforcementCheck:
    try:
        return check_enforcement(spec)
    except OSError as exc:
        return _unreadable_enforcement(spec, exc)


def _dataset_without_enforcement_ref(snapshot: _Snapshot, guard_id: str) -> Dataset:
    """대조 참조만 떼어낸 사본 데이터셋 — 구현물을 읽을 수 없을 때만 쓰는 우회.

    이미 적재한 레코드에서 해당 가드의 GuardSpec만 `enforcement_ref=None`으로 바꾼
    사본이다. 디스크는 건드리지 않고(읽기 전용 계약), 의미 5필드·`content_hash`는
    그대로라 뷰의 집계·사건·결정은 원본과 같다. 대조 결과만 호출자가 되붙인다.
    """
    records = [
        replace(record, enforcement_ref=None)
        if isinstance(record, GuardSpec) and record.guard_id == guard_id
        else record
        for record in snapshot.load.records
    ]
    return Dataset(records)


def _context_payload(spec: GuardSpec, view: GuardView, enforcement: EnforcementCheck) -> dict:
    return {
        "version": spec.version,
        "latest_version": view.latest_version,
        "versions": list(view.versions),
        "content_hash": spec.content_hash,
        "purpose": spec.purpose,
        "policy": spec.policy,
        "exceptions": list(spec.exceptions),
        "allow_examples": list(spec.allow_examples),
        "block_examples": list(spec.block_examples),
        "enforcement": {
            "status": enforcement.status.value,
            "reason": enforcement.detail,
        },
    }


# --- 도구 본문 ----------------------------------------------------------------


def render_list_guards(store: AppendStore) -> str:
    """§4.1 — 등록된 가드 전체. 하나도 없으면 빈 목록(오류가 아니다)."""
    snapshot = _Snapshot(store)
    guards = []
    for guard_id in sorted({gid for gid, _ in snapshot.dataset.specs_by_key}):
        specs = snapshot.specs_for(guard_id)
        latest = specs[-1]
        guards.append(
            {
                "guard_id": guard_id,
                "project": latest.project,
                "latest_version": latest.version,
                "versions": [spec.version for spec in specs],
            }
        )
    return snapshot.emit({"guards": guards})


def _check_required_inputs(snapshot: _Snapshot, tool: str, values: dict[str, Any]) -> None:
    """선언표가 필수라고 한 인자가 비면 여기서 막는다.

    발행 스키마의 `required`와 **같은 표**를 읽는다 — 필수라고 알려 놓고 본문은 통과
    시키는(또는 그 반대의) 표류가 생길 수 없다.
    """
    for spec in TOOL_INPUTS.get(tool, ()):
        if spec.required and values.get(spec.name) is None:
            raise snapshot.fail(f"{spec.name}가 필요하다 — {spec.description}")


def _checked_guard_id(snapshot: _Snapshot, value: Any) -> str:
    # 누락(None)은 `_check_required_inputs`가 선언표를 근거로 이미 막았다.
    if not isinstance(value, str):
        raise snapshot.fail("guard_id는 문자열이어야 한다:", echo=value)
    if not value.strip():
        raise snapshot.fail("guard_id는 비어 있지 않은 문자열이어야 한다")
    return value


def _checked_version(snapshot: _Snapshot, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise snapshot.fail("version은 정수여야 한다:", echo=value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _PLAIN_INT.fullmatch(value.strip(_ASCII_SPACE)):
        # 일부 클라이언트는 숫자도 문자열로 보낸다 — 평범한 십진수면 받아들인다.
        # `int()`에 곧장 맡기지 않는 이유: `"1_0"`이 조용히 10이 되는 등 파이썬의
        # 관대함이 호출자 입력을 다시 해석해 버린다. 공백도 ASCII만 걷어낸다 —
        # 기본 `strip()`은 유니코드 공백까지 먹어서 `[0-9]`로 좁힌 뜻이 어긋난다.
        try:
            return int(value.strip(_ASCII_SPACE))
        except ValueError:
            # 십진수 문자열이어도 자릿수가 CPython 한도(기본 4300)를 넘으면
            # 변환이 터진다. 여기서 잡지 않으면 예외가 도구 밖으로 새고, 그
            # 응답은 정화 경계를 지나지 않은 SDK 문구가 된다.
            pass
    raise snapshot.fail("version은 정수여야 한다:", echo=value)


def render_guard_evidence(store: AppendStore, guard_id: Any, version: Any = None) -> str:
    """§4.2 — 가드 하나의 맥락·세션 집계·사건·결정 이력·레코드 건강.

    인자는 클라이언트가 보낸 원값 그대로 받는다(SDK 단계에서 거부되면 그 오류가
    출력 경계를 지나지 않기 때문이다 — 모듈 문서 "인자 검증" 참고). 검증은 여기서
    하고, 어긋나면 정화된 한 줄 `ToolError`다.
    """
    snapshot = _Snapshot(store)
    _check_required_inputs(
        snapshot, GUARD_EVIDENCE_TOOL, {"guard_id": guard_id, "version": version}
    )
    guard_id = _checked_guard_id(snapshot, guard_id)
    version = _checked_version(snapshot, version)
    unreadable: EnforcementCheck | None = None
    try:
        view = build_guard_view(snapshot.dataset, guard_id)
    except DecisionError:
        raise snapshot.fail("등록되지 않은 가드") from None
    except OSError as exc:
        # 뷰 구성이 최신 spec의 구현물 대조를 내부에서 하므로, 읽기 실패가 증거
        # 전체를 막는다. 참조를 뗀 사본으로 뷰만 세우고 사유는 아래에서 되붙인다.
        unreadable = _unreadable_enforcement(snapshot.specs_for(guard_id)[-1], exc)
        view = build_guard_view(_dataset_without_enforcement_ref(snapshot, guard_id), guard_id)
    if version is None:
        spec = snapshot.specs_for(guard_id)[-1]
    else:
        spec = snapshot.dataset.specs_by_key.get((guard_id, version))
        if spec is None:
            versions = ", ".join(f"v{v}" for v in view.versions)
            raise snapshot.fail(f"가드에 없는 버전 (등록 버전: {versions})")
    if spec.version == view.latest_version:
        # 뷰가 이미 최신 spec을 대조했다 — 같은 응답 안에서 파일을 다시 읽지 않는다.
        # (다시 읽으면 그 사이 바뀐 바이트 때문에 한 응답 안 두 상태가 어긋날 수 있다.)
        enforcement = unreadable if unreadable is not None else view.enforcement
    else:
        enforcement = _safe_check_enforcement(spec)
    payload = {
        "guard_id": view.guard_id,
        "project": view.project,
        "context": _context_payload(spec, view, enforcement),
        "sessions": {
            "operation_session_count": view.operation_session_count,
            "decidable_session_count": view.decidable_session_count,
            "guard_decidable": view.guard_decidable,
            "criterion": DECIDABLE_CRITERION,
        },
        "events": [_event_payload(snapshot, row) for row in view.events],
        "decisions": [_decision_payload(row) for row in view.decisions],
        "record_health": {
            "corrupt_lines": len(snapshot.load.corrupt),
            "calibration_corrupt_lines": snapshot.calibration_corrupt,
        },
    }
    return snapshot.emit(payload)


def render_report_text(store: AppendStore, *, now: datetime | None = None) -> str:
    """§4.3 — 기존 보고서 생성 함수의 Markdown 그대로.

    보고서 본문의 정본은 v1 코어다 — 내용을 고치지 않는다. 같은 출력 경계를 한 번
    통과시키지만, 보고서는 집계만 담고(세션 식별자가 없고) 생성 함수가 이미 홈
    경로를 `~`로 바꾸므로 결과는 원문과 동일하다. 새 필드가 생겨도 기본이 안전한
    쪽을 유지하기 위한 이중 방어다.

    **순서가 계약이다**: 본문을 먼저 만들고 경계를 그 **뒤에** 뜬다. `generate_report`가
    자체 `load()`를 하므로 한 호출에 적재가 둘인데, store가 append 전용이라 나중
    스냅샷의 세션 집합은 앞선 것의 상위집합이다 — 그래서 본문이 본 식별자는 반드시
    경계가 안다. 반대 순서면 두 적재 사이에 들어온 세션이 별칭 없이 새 나간다.
    """
    text = generate_report(store, now=now)
    snapshot = _Snapshot(store)
    return snapshot.boundary.text(text)


# --- 서버 --------------------------------------------------------------------


def published_input_schema(
    schema: dict[str, Any], inputs: tuple[ToolInput, ...]
) -> dict[str, Any]:
    """선언표(`TOOL_INPUTS`)대로 발행 스키마를 되돌린다.

    느슨한 `Any` 선언 때문에 pydantic이 지운 것은 둘이다 — 필수 표기와 JSON-Schema
    타입. 타입이 없으면 클라이언트(특히 LLM)는 한국어 설명문으로만 인자 타입을
    추측해야 한다. 검증은 여전히 도구 본문이 하고, 여기서는 **발행되는 계약만**
    복원한다.

    원본은 건드리지 않는다 — 넘어오는 dict는 SDK 툴 매니저가 들고 있는 캐시라서,
    제자리 수정은 호출마다 스키마가 달라지는 길이 된다.
    """
    patched = dict(schema)
    properties = {name: dict(value) for name, value in patched.get("properties", {}).items()}
    for spec in inputs:
        prop = dict(properties.get(spec.name, {}))
        prop.update(spec.json_schema)
        prop["description"] = spec.description
        if spec.required:
            # 느슨한 선언 때문에 붙은 `default: null`은 "필수"와 모순이라 걷어낸다.
            prop.pop("default", None)
        properties[spec.name] = prop
    patched["properties"] = properties
    declared = {spec.name for spec in inputs if spec.required}
    patched["required"] = sorted(set(patched.get("required", ())) | declared)
    return patched


class _EvidenceServer(MCPServer):
    """`tools/list`의 입력 스키마만 바로잡아 내보내는 읽기 전용 서버.

    도구 인자는 pydantic이 아무 값도 거부하지 않도록 느슨하게 선언한다 — 거부하면
    그 오류 텍스트가 출력 경계를 지나지 않기 때문이다(모듈 문서 "인자 검증").
    그 대가로 발행 스키마에서 사라지는 필수 표기와 타입을 `TOOL_INPUTS`대로 되살려,
    클라이언트가 보는 계약은 그대로 "guard_id 필수 문자열 / version 선택 정수"다.
    """

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        return [
            tool.model_copy(
                update={
                    "input_schema": published_input_schema(
                        tool.input_schema, TOOL_INPUTS[tool.name]
                    )
                }
            )
            if TOOL_INPUTS.get(tool.name)
            else tool
            for tool in tools
        ]


def _run_tool(render: Callable[[], str]) -> str:
    """SDK 바깥으로 예상 밖 예외와 그 traceback이 새지 않게 막는다."""
    try:
        return render()
    except ToolError:
        raise
    except Exception:
        raise ToolError(UNEXPECTED_TOOL_ERROR) from None


def build_server(store: AppendStore, *, now: datetime | None = None) -> MCPServer:
    """세 도구만 노출하는 읽기 전용 서버. 쓰기 도구는 하나도 없다.

    `now`는 시각 의존 줄을 고정해 보고서 동일성을 대조하기 위한 주입 지점이다
    (기본값 None = 실제 현재 시각).
    """
    server = _EvidenceServer(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        instructions=(
            "Reject Bench 가드 발동 증거의 읽기 전용 조회 표면이다. "
            "쓰기 도구가 없고 store를 절대 변경하지 않는다. "
            "응답의 세션 표기는 응답 안에서만 유효한 별칭(S1, S2, …)이고, "
            "홈 절대 경로는 `~`로 치환된다."
        ),
    )

    @server.tool(
        name=LIST_GUARDS_TOOL,
        description="등록된 가드 목록 (guard_id·project·latest_version·versions).",
        structured_output=False,
    )
    def list_guards() -> str:
        return _run_tool(lambda: render_list_guards(store))

    @server.tool(
        name=GUARD_EVIDENCE_TOOL,
        description=(
            "가드 하나의 증거 — spec 맥락(의미 5필드·구현물 대조), 세션 집계, "
            "사건 목록, 결정 이력, 손상 줄 수. version을 생략하면 최신 버전 맥락."
        ),
        structured_output=False,
    )
    def guard_evidence(
        guard_id: Annotated[Any, Field(description=GUARD_ID_INPUT)] = None,
        version: Annotated[Any, Field(description=VERSION_INPUT)] = None,
    ) -> str:
        # 타입 선언이 느슨한 것은 의도다 — SDK가 먼저 거부하면 그 오류 텍스트가
        # 출력 경계를 지나지 않는다. 검증은 도구 본문(render_guard_evidence)에서.
        return _run_tool(lambda: render_guard_evidence(store, guard_id, version))

    @server.tool(
        name=GET_REPORT_TOOL,
        description="전체 보고서 Markdown (기존 보고서 생성 함수 출력 그대로).",
        structured_output=False,
    )
    def get_report() -> str:
        return _run_tool(lambda: render_report_text(store, now=now))

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rejectbench.mcp_server",
        description="Reject Bench 증거 조회 MCP 서버 (읽기 전용, stdio)",
    )
    parser.add_argument(
        "--store",
        default=None,
        metavar="DIR",
        help="조회할 store 루트 (기본값: 운영 중앙 경로). 없어도 만들지 않는다.",
    )
    args = parser.parse_args(argv)
    root = Path(args.store).expanduser() if args.store else production_root()
    build_server(AppendStore(root)).run("stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover - 실행 진입점
    raise SystemExit(main())
