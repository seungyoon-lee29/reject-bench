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

호출마다 store를 다시 읽는다 — 캐시도, 상주 상태도 없다. 한 호출은 `load()`
스냅샷 하나만 쓰므로 기록기가 동시에 append 중이어도 응답이 섞이지 않는다.
꼬리 부분 줄은 기존 적재 규약대로 건너뛰고 손상 줄 수로 드러낸다.

## 출력 경계 (spec §5)

응답으로 나가는 **모든 문자열**(오류 메시지 포함)은 직렬화 직전에 `OutputBoundary`를
정확히 한 번 지난다. 필드별로 골라 지우지 않는다 — 새 필드가 늘어도 기본이 안전해야
하기 때문이다. 경계가 하는 일은 둘이다.

- 홈 절대 경로 접두사를 `~`로 치환한다. 꼬리는 보존한다 — `target_path`는 판단에
  필요한 값이라 생략이 아니라 치환이어야 한다.
- 레코드의 세션 식별자 원문을 순번 별칭(`S1`, `S2`, …)으로 치환한다. 별칭은 한
  응답 안에서만 유효하고, 별칭↔원문 표는 **어디에도 저장하지 않는다** (호출이
  끝나면 메모리에서 사라진다).

비밀 제거는 v1 적재 시점 규칙이 이미 담당한다 — 이 경계는 그 위에 홈 경로와 세션
식별자를 더 막을 뿐 적재 시점 제거를 대체하지 않는다.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from rejectbench.dataset import Dataset
from rejectbench.decision import DecisionError, EventRow, GuardView, build_guard_view, check_enforcement
from rejectbench.judge import JudgeCalibration, find_calibration, load_calibrations
from rejectbench.records import CaptureStatus, GuardSpec, PolicyVerdict, UtilityReview
from rejectbench.report import generate_report
from rejectbench.store import AppendStore, LoadResult, production_root

SERVER_NAME = "rejectbench-evidence"
SERVER_VERSION = "7.0.0"

LIST_GUARDS_TOOL = "list_guards"
GUARD_EVIDENCE_TOOL = "guard_evidence"
GET_REPORT_TOOL = "get_report"

#: 판정 가능 가드 기준 — decision/metrics의 단일 정의를 문구로 함께 실어 보낸다.
DECIDABLE_CRITERION = (
    "서로 다른 operation 세션 2개 이상의 사건이 있고, 그 사건들의 정책 판정·"
    "유용성 검토가 모두 확정값인 가드"
)
UNPROCESSED = "미처리"

ALIAS_PREFIX = "S"


# --- 출력 경계 ----------------------------------------------------------------


class OutputBoundary:
    """응답 직렬화 직전의 단일 정화 경계 (spec §5).

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
        # 긴 것부터 치환해야 접두사가 겹치는 식별자를 잘라먹지 않는다.
        self._session_ids = tuple(
            sorted({sid for sid in session_ids if sid}, key=len, reverse=True)
        )
        self._aliases: dict[str, str] = {}

    @property
    def aliases(self) -> dict[str, str]:
        """호출 안에서 발급된 별칭 표 — 저장하지 않는다 (진단용 읽기 전용 사본)."""
        return dict(self._aliases)

    def _alias_for(self, session_id: str) -> str:
        alias = self._aliases.get(session_id)
        if alias is None:
            alias = f"{ALIAS_PREFIX}{len(self._aliases) + 1}"
            self._aliases[session_id] = alias
        return alias

    def text(self, value: str) -> str:
        out = value.replace(self._home, "~") if self._home else value
        if not self._session_ids:
            return out
        # 별칭 발급 순서는 문자열 안 첫 등장 순서다 (응답 전체로는 순회 순서).
        found = sorted(
            ((out.find(sid), sid) for sid in self._session_ids if sid in out),
            key=lambda pair: (pair[0], -len(pair[1])),
        )
        for _, session_id in found:
            self._alias_for(session_id)
        # 실제 치환은 긴 식별자부터 — 부분 문자열 겹침을 피한다.
        for session_id in self._session_ids:
            if session_id in out:
                out = out.replace(session_id, self._alias_for(session_id))
        return out

    def one_line(self, value: str) -> str:
        """오류 메시지용 — 정화 뒤 한 줄로 접는다 (스택 추적·줄바꿈 금지)."""
        return " ".join(self.text(value).split())

    def sanitize(self, value: Any) -> Any:
        """응답 구조를 재귀 순회하며 모든 문자열을 정확히 한 번 정화한다."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {
                (self.text(key) if isinstance(key, str) else key): self.sanitize(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.sanitize(item) for item in value]
        return value


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

    def fail(self, message: str) -> ToolError:
        """정화된 한 줄 사유만 담은 도구 오류 — 스택 추적도 저장 경로도 싣지 않는다."""
        return ToolError(self.boundary.one_line(message))

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


def _decision_payload(row) -> dict:
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


def _context_payload(spec: GuardSpec, view: GuardView) -> dict:
    enforcement = check_enforcement(spec)
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


def render_guard_evidence(
    store: AppendStore, guard_id: str, version: int | None = None
) -> str:
    """§4.2 — 가드 하나의 맥락·세션 집계·사건·결정 이력·레코드 건강."""
    snapshot = _Snapshot(store)
    try:
        view = build_guard_view(snapshot.dataset, guard_id)
    except DecisionError:
        raise snapshot.fail(f"등록되지 않은 가드: {guard_id}") from None
    if version is None:
        spec = snapshot.specs_for(guard_id)[-1]
    else:
        spec = snapshot.dataset.specs_by_key.get((guard_id, version))
        if spec is None:
            versions = ", ".join(f"v{v}" for v in view.versions)
            raise snapshot.fail(
                f"가드 {guard_id}에 없는 버전: v{version} (등록 버전: {versions})"
            )
    payload = {
        "guard_id": view.guard_id,
        "project": view.project,
        "context": _context_payload(spec, view),
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
    """
    snapshot = _Snapshot(store)
    return snapshot.boundary.text(generate_report(store, now=now))


# --- 서버 --------------------------------------------------------------------


def build_server(store: AppendStore, *, now: datetime | None = None) -> MCPServer:
    """세 도구만 노출하는 읽기 전용 서버. 쓰기 도구는 하나도 없다.

    `now`는 시각 의존 줄을 고정해 보고서 동일성을 대조하기 위한 주입 지점이다
    (기본값 None = 실제 현재 시각).
    """
    server = MCPServer(
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
        return render_list_guards(store)

    @server.tool(
        name=GUARD_EVIDENCE_TOOL,
        description=(
            "가드 하나의 증거 — spec 맥락(의미 5필드·구현물 대조), 세션 집계, "
            "사건 목록, 결정 이력, 손상 줄 수. version을 생략하면 최신 버전 맥락."
        ),
        structured_output=False,
    )
    def guard_evidence(guard_id: str, version: int | None = None) -> str:
        return render_guard_evidence(store, guard_id, version)

    @server.tool(
        name=GET_REPORT_TOOL,
        description="전체 보고서 Markdown (기존 보고서 생성 함수 출력 그대로).",
        structured_output=False,
    )
    def get_report() -> str:
        return render_report_text(store, now=now)

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
