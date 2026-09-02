"""v7 도메인 레코드 (spec §3).

전역 규칙: 모든 레코드는 스키마 버전, 고유 id, UTC 시각을 가진다. 원본은
변경하지 않고 append 수정(Amendment) 레코드만 허용한다. 레코드는 데이터로만
처리한다 — 어떤 필드도 실행하지 않는다.

스키마는 전문 저장을 허용하지 않는다: 파일 내용·프롬프트·전체 명령 인자·
도구 응답 전문을 담을 필드 자체가 없다 (spec §4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import StrEnum

from rejectbench.hashing import content_hash

# 7.1 (003 spec §4.7): GuardEvent에 `session_id_format`이 생겼다. 인상은 전역 상수
# 일괄이라 7종 레코드와 judge 사이드카 전부에 찍히지만, **이 값을 소비하는 코드
# 경로는 없다** — 구형(7.0) 수용 판별은 버전이 아니라 필드 부재 기준이다.
SCHEMA_VERSION = "7.1"

_MAX_LOSS_DETAIL = 500  # 원문 없는 최소 메타데이터 강제


class SchemaError(ValueError):
    """스키마·enum·필수 필드 위반."""


class Origin(StrEnum):
    OPERATION = "operation"
    TEST = "test"
    UNKNOWN = "unknown"


class OriginEvidence(StrEnum):
    EXPLICIT_FLAG = "explicit_flag"
    DEFAULT_INHERITED = "default_inherited"
    NO_CONTEXT = "no_context"


# spec §3.2 출처 결정표에 존재하는 조합만 허용한다.
_ORIGIN_PAIRS = {
    Origin.TEST: {OriginEvidence.EXPLICIT_FLAG},
    Origin.OPERATION: {OriginEvidence.DEFAULT_INHERITED},
    Origin.UNKNOWN: {OriginEvidence.NO_CONTEXT},
}


class CaptureStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class Verdict(StrEnum):
    CORRECT_BLOCK = "correct_block"
    INCORRECT_BLOCK = "incorrect_block"
    INSUFFICIENT_CONTEXT = "insufficient_context"  # 기록된 보류값


class Utility(StrEnum):
    USEFUL = "useful"
    UNNECESSARY = "unnecessary"
    UNCERTAIN = "uncertain"  # 기록된 보류값


class Decision(StrEnum):
    KEEP = "keep"
    MODIFY = "modify"
    REMOVE = "remove"


class LossKind(StrEnum):
    WRITE_FAILURE = "write_failure"
    PARTIAL_CAPTURE = "partial_capture"
    VERDICT_FAILURE = "verdict_failure"


# --- 세션 ID 적재 형식 (003 spec §4) ------------------------------------------
#
# 저장 세션 ID는 복합값 `harness:원본`이다. 형식 검사는 **원본 부분에만** 걸리고,
# 결과는 진단 필드일 뿐이다 — 저장값·origin·가드 결과를 바꾸지 않는다.


class SessionIdFormat(StrEnum):
    """`GuardEvent.session_id_format` — 3상, `null` 불허 (spec §4.4)."""

    CONFORMING = "conforming"
    NONCONFORMING = "nonconforming"
    # 자리표시(맥락 부재) · 7.0 구형(필드 부재) · 검사 술어 예외 — 세 사유를 합친다.
    # 진단력보다 사건 보존이 우선이고, 세 사유는 각각 capture_status·스키마 버전·
    # 손실 흔적으로 따로 남는다.
    UNCHECKED = "unchecked"


@dataclass(frozen=True)
class SessionIdRawRule:
    """§4.3 형식 술어 파라미터 — 명명 상수 한 곳.

    변경은 사유 있는 계약 개정으로만 하며, 그때 UUID 준수 테스트와 E3 양성
    대조를 재실행한다. 이 술어는 **E2 진단 전용**이다 — E3의 가림 자격은 UUID
    문법으로 따로 정의하므로 여기를 튜닝해도 E3 보장 범위는 바뀌지 않는다.
    """

    min_len: int
    max_len: int
    charset: str  # 정규식 문자 클래스 본문 — 하이픈이 있어야 UUID가 충족한다

    def matches(self, raw: str) -> bool:
        # fullmatch — `$` 앵커는 끝 개행을 통과시킨다.
        return re.fullmatch(rf"[{self.charset}]{{{self.min_len},{self.max_len}}}", raw) is not None


SESSION_ID_RAW_RULE = SessionIdRawRule(min_len=8, max_len=128, charset="A-Za-z0-9_-")


def split_session_id(session_id: str) -> tuple[str, str | None]:
    """복합값 분해 — 첫 `:` 이전이 harness, **그 이후 전부**가 원본 (spec §4.1).

    `:`가 없으면 원본 부분이 없다(`None`). §4의 형식 검사와 §5(E3)의 needle
    산출이 **이 한 함수**를 쓴다 — 분해가 갈라지면 두 절이 다른 값을 본다.
    """
    harness, separator, raw = session_id.partition(":")
    return (harness, raw) if separator else (session_id, None)


def session_id_format(session_id: str) -> SessionIdFormat:
    """§4 진단 술어 — 원본 부분만 검사한다.

    자리표시(맥락 부재)는 값의 모양이 아니라 호출자가 아는 맥락으로 가르므로
    여기서는 판별하지 않는다 — 기록기가 `unchecked`를 직접 놓는다.
    """
    _, raw = split_session_id(session_id)
    if raw is None:
        return SessionIdFormat.NONCONFORMING
    if SESSION_ID_RAW_RULE.matches(raw):
        return SessionIdFormat.CONFORMING
    return SessionIdFormat.NONCONFORMING


def _require_utc(value, name: str) -> None:
    if not isinstance(value, datetime):
        raise SchemaError(f"{name}: datetime이 아니다")
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
        raise SchemaError(f"{name}: UTC aware datetime이어야 한다")


def _require_nonempty_str(value, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{name}: 비어 있지 않은 문자열이어야 한다")


def _require_str(value, name: str) -> None:
    if not isinstance(value, str):
        raise SchemaError(f"{name}: 문자열이어야 한다")


def _require_str_tuple(value, name: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(v, str) for v in value):
        raise SchemaError(f"{name}: 문자열 튜플이어야 한다")


def _require_enum(value, enum_cls, name: str) -> None:
    if not isinstance(value, enum_cls):
        raise SchemaError(f"{name}: {enum_cls.__name__} 값이어야 한다")


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse_dt(value, name: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{name}: ISO 8601 시각이 아니다") from exc


def _parse_enum(value, enum_cls, name: str):
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise SchemaError(f"{name}: 허용되지 않는 값 {value!r}") from exc


@dataclass(frozen=True)
class EnforcementRef:
    """가드 구현물 참조 — 정책의 대체물이 아니라 drift 감지용 메타데이터."""

    script_path: str
    file_hash: str

    def __post_init__(self):
        _require_nonempty_str(self.script_path, "enforcement_ref.script_path")
        _require_nonempty_str(self.file_hash, "enforcement_ref.file_hash")


@dataclass(frozen=True)
class ActionSummary:
    """구조화 행동 요약 — 판정 최소 필드만. 전체 명령·파일 내용을 담을 자리가 없다."""

    tool_name: str
    command_verb: str | None = None
    target_path: str | None = None
    heredoc: bool = False

    def __post_init__(self):
        _require_nonempty_str(self.tool_name, "action.tool_name")
        for name in ("tool_name", "command_verb", "target_path"):
            value = getattr(self, name)
            if value is not None and ("\n" in value or "\r" in value):
                raise SchemaError(f"action.{name}: 줄바꿈을 허용하지 않는다")
        if self.command_verb is not None and (
            not self.command_verb or any(c.isspace() for c in self.command_verb)
        ):
            raise SchemaError("action.command_verb: 공백 없는 단일 토큰이어야 한다")
        if not isinstance(self.heredoc, bool):
            raise SchemaError("action.heredoc: bool이어야 한다")


@dataclass(frozen=True)
class GuardSpec:
    """사건 전에 고정되는 가드 맥락 (spec §3.1)."""

    spec_id: str
    guard_id: str
    version: int
    project: str
    purpose: str
    policy: str
    exceptions: tuple[str, ...]
    allow_examples: tuple[str, ...]
    block_examples: tuple[str, ...]
    created_at: datetime
    content_hash: str
    enforcement_ref: EnforcementRef | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        _require_nonempty_str(self.spec_id, "spec_id")
        _require_nonempty_str(self.guard_id, "guard_id")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise SchemaError("version: 1 이상의 정수여야 한다")
        _require_nonempty_str(self.project, "project")
        _require_str(self.purpose, "purpose")
        _require_str(self.policy, "policy")
        _require_str_tuple(self.exceptions, "exceptions")
        _require_str_tuple(self.allow_examples, "allow_examples")
        _require_str_tuple(self.block_examples, "block_examples")
        _require_utc(self.created_at, "created_at")
        expected = content_hash(
            purpose=self.purpose,
            policy=self.policy,
            exceptions=self.exceptions,
            allow_examples=self.allow_examples,
            block_examples=self.block_examples,
        )
        if self.content_hash != expected:
            raise SchemaError("content_hash: 의미 5필드의 정규화 해시와 일치하지 않는다")

    @property
    def record_id(self) -> str:
        return self.spec_id

    @property
    def record_time(self) -> datetime:
        return self.created_at

    @classmethod
    def create(
        cls,
        *,
        spec_id: str,
        guard_id: str,
        version: int,
        project: str,
        purpose: str,
        policy: str,
        exceptions: tuple[str, ...],
        allow_examples: tuple[str, ...],
        block_examples: tuple[str, ...],
        created_at: datetime,
        enforcement_ref: EnforcementRef | None = None,
    ) -> "GuardSpec":
        """content_hash를 계산해 붙이는 편의 생성자."""
        return cls(
            spec_id=spec_id,
            guard_id=guard_id,
            version=version,
            project=project,
            purpose=purpose,
            policy=policy,
            exceptions=tuple(exceptions),
            allow_examples=tuple(allow_examples),
            block_examples=tuple(block_examples),
            created_at=created_at,
            content_hash=content_hash(
                purpose=purpose,
                policy=policy,
                exceptions=tuple(exceptions),
                allow_examples=tuple(allow_examples),
                block_examples=tuple(block_examples),
            ),
            enforcement_ref=enforcement_ref,
        )


@dataclass(frozen=True)
class GuardEvent:
    """가드 발동 사건 (spec §3.2).

    등록된 발동은 `guard_id`·`guard_version`·`guard_spec_hash` 셋을 모두
    참조한다. 미등록 발동은 셋 대신 `unregistered` 표시와 `guard_hint`
    (스크립트 경로 등 가드 추정 정보)를 가진다. 사후 등록을 소급 연결하지
    않으므로 두 형태는 배타적이다.
    """

    event_id: str
    occurred_at: datetime
    session_id: str
    project: str
    action: ActionSummary
    reason: str
    origin: Origin
    origin_evidence: OriginEvidence
    capture_status: CaptureStatus = CaptureStatus.COMPLETE
    guard_id: str | None = None
    guard_version: int | None = None
    guard_spec_hash: str | None = None
    unregistered: bool = False
    guard_hint: str | None = None
    drift: bool = False  # 구현물 해시 ≠ enforcement_ref (감지는 T3/T5)
    post_remove: bool = False  # remove 결정 뒤 발동 (감지는 T5)
    # 003 spec §4 — 진단 필드. 기본값 unchecked는 "검사하지 않았다"는 사실 그대로다.
    session_id_format: SessionIdFormat = SessionIdFormat.UNCHECKED
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        _require_nonempty_str(self.event_id, "event_id")
        _require_utc(self.occurred_at, "occurred_at")
        _require_nonempty_str(self.session_id, "session_id")
        _require_nonempty_str(self.project, "project")
        if not isinstance(self.action, ActionSummary):
            raise SchemaError("action: ActionSummary여야 한다")
        _require_str(self.reason, "reason")
        _require_enum(self.origin, Origin, "origin")
        _require_enum(self.origin_evidence, OriginEvidence, "origin_evidence")
        if self.origin_evidence not in _ORIGIN_PAIRS[self.origin]:
            raise SchemaError(
                f"origin_evidence: 결정표에 없는 조합 ({self.origin.value}, "
                f"{self.origin_evidence.value})"
            )
        _require_enum(self.capture_status, CaptureStatus, "capture_status")
        _require_enum(self.session_id_format, SessionIdFormat, "session_id_format")
        for name in ("unregistered", "drift", "post_remove"):
            if not isinstance(getattr(self, name), bool):
                raise SchemaError(f"{name}: bool이어야 한다")
        if self.unregistered:
            if (
                self.guard_id is not None
                or self.guard_version is not None
                or self.guard_spec_hash is not None
            ):
                raise SchemaError("unregistered 사건은 가드 참조 셋을 가질 수 없다")
            _require_nonempty_str(self.guard_hint, "guard_hint")
        else:
            _require_nonempty_str(self.guard_id, "guard_id")
            if (
                not isinstance(self.guard_version, int)
                or isinstance(self.guard_version, bool)
                or self.guard_version < 1
            ):
                raise SchemaError("guard_version: 1 이상의 정수여야 한다")
            _require_nonempty_str(self.guard_spec_hash, "guard_spec_hash")

    @property
    def record_id(self) -> str:
        return self.event_id

    @property
    def record_time(self) -> datetime:
        return self.occurred_at


@dataclass(frozen=True)
class PolicyVerdict:
    """LLM의 정책 일치성 판단 (spec §3.3)."""

    verdict_id: str
    event_id: str
    verdict: Verdict
    reason: str
    context_bundle_hash: str
    guard_spec_hash: str
    rubric_hash: str
    model_id: str
    model_settings_hash: str
    judged_at: datetime
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        _require_nonempty_str(self.verdict_id, "verdict_id")
        _require_nonempty_str(self.event_id, "event_id")
        _require_enum(self.verdict, Verdict, "verdict")
        _require_str(self.reason, "reason")
        _require_nonempty_str(self.context_bundle_hash, "context_bundle_hash")
        _require_nonempty_str(self.guard_spec_hash, "guard_spec_hash")
        _require_nonempty_str(self.rubric_hash, "rubric_hash")
        _require_nonempty_str(self.model_id, "model_id")
        _require_nonempty_str(self.model_settings_hash, "model_settings_hash")
        _require_utc(self.judged_at, "judged_at")

    @property
    def record_id(self) -> str:
        return self.verdict_id

    @property
    def record_time(self) -> datetime:
        return self.judged_at


@dataclass(frozen=True)
class UtilityReview:
    """사용자의 실제 유용성 검토 (spec §3.4)."""

    review_id: str
    event_id: str
    utility: Utility
    note: str
    reviewed_at: datetime
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        _require_nonempty_str(self.review_id, "review_id")
        _require_nonempty_str(self.event_id, "event_id")
        _require_enum(self.utility, Utility, "utility")
        _require_str(self.note, "note")
        _require_utc(self.reviewed_at, "reviewed_at")

    @property
    def record_id(self) -> str:
        return self.review_id

    @property
    def record_time(self) -> datetime:
        return self.reviewed_at


@dataclass(frozen=True)
class GuardDecision:
    """수명주기 결정 (spec §3.5). 결정 변경은 새 결정 레코드로만 남긴다."""

    decision_id: str
    guard_id: str
    decision: Decision
    evidence_event_ids: tuple[str, ...]
    rationale: str
    decided_at: datetime
    resulting_guard_version: int | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        _require_nonempty_str(self.decision_id, "decision_id")
        _require_nonempty_str(self.guard_id, "guard_id")
        _require_enum(self.decision, Decision, "decision")
        _require_str_tuple(self.evidence_event_ids, "evidence_event_ids")
        if len(set(self.evidence_event_ids)) != len(self.evidence_event_ids):
            raise SchemaError("evidence_event_ids: 중복을 허용하지 않는다")
        _require_str(self.rationale, "rationale")
        _require_utc(self.decided_at, "decided_at")
        if self.decision is Decision.MODIFY:
            if (
                not isinstance(self.resulting_guard_version, int)
                or isinstance(self.resulting_guard_version, bool)
                or self.resulting_guard_version < 1
            ):
                raise SchemaError("resulting_guard_version: modify 결정에 필수다")
        elif self.resulting_guard_version is not None:
            raise SchemaError("resulting_guard_version: modify 외 결정에는 없어야 한다")

    @property
    def record_id(self) -> str:
        return self.decision_id

    @property
    def record_time(self) -> datetime:
        return self.decided_at


@dataclass(frozen=True)
class LossRecord:
    """기록 실패·부분 적재·판정 실패의 원문 없는 최소 메타데이터 (spec §3.6)."""

    loss_id: str
    recorded_at: datetime
    kind: LossKind
    detail: str
    subject_ref: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        _require_nonempty_str(self.loss_id, "loss_id")
        _require_utc(self.recorded_at, "recorded_at")
        _require_enum(self.kind, LossKind, "kind")
        _require_str(self.detail, "detail")
        if len(self.detail) > _MAX_LOSS_DETAIL:
            raise SchemaError(
                f"detail: 최소 메타데이터만 허용한다 (최대 {_MAX_LOSS_DETAIL}자)"
            )
        if self.subject_ref is not None:
            _require_nonempty_str(self.subject_ref, "subject_ref")

    @property
    def record_id(self) -> str:
        return self.loss_id

    @property
    def record_time(self) -> datetime:
        return self.recorded_at


@dataclass(frozen=True)
class Amendment:
    """append 수정 레코드 (spec §3.6). 원본 레코드를 절대 덮어쓰지 않는다."""

    amendment_id: str
    target_id: str
    field: str
    previous_value_hash: str
    new_value: str
    reason: str
    amended_at: datetime
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        _require_nonempty_str(self.amendment_id, "amendment_id")
        _require_nonempty_str(self.target_id, "target_id")
        _require_nonempty_str(self.field, "field")
        _require_nonempty_str(self.previous_value_hash, "previous_value_hash")
        _require_str(self.new_value, "new_value")
        _require_nonempty_str(self.reason, "reason")
        _require_utc(self.amended_at, "amended_at")

    @property
    def record_id(self) -> str:
        return self.amendment_id

    @property
    def record_time(self) -> datetime:
        return self.amended_at


# --- 직렬화 -----------------------------------------------------------------


def _spec_to_json(r: GuardSpec) -> dict:
    return {
        "record_type": "guard_spec",
        "schema_version": r.schema_version,
        "spec_id": r.spec_id,
        "guard_id": r.guard_id,
        "version": r.version,
        "project": r.project,
        "purpose": r.purpose,
        "policy": r.policy,
        "exceptions": list(r.exceptions),
        "allow_examples": list(r.allow_examples),
        "block_examples": list(r.block_examples),
        "created_at": _iso(r.created_at),
        "content_hash": r.content_hash,
        "enforcement_ref": (
            None
            if r.enforcement_ref is None
            else {
                "script_path": r.enforcement_ref.script_path,
                "file_hash": r.enforcement_ref.file_hash,
            }
        ),
    }


def _spec_from_json(p: dict) -> GuardSpec:
    ref = p["enforcement_ref"]
    return GuardSpec(
        spec_id=p["spec_id"],
        guard_id=p["guard_id"],
        version=p["version"],
        project=p["project"],
        purpose=p["purpose"],
        policy=p["policy"],
        exceptions=tuple(p["exceptions"]),
        allow_examples=tuple(p["allow_examples"]),
        block_examples=tuple(p["block_examples"]),
        created_at=_parse_dt(p["created_at"], "created_at"),
        content_hash=p["content_hash"],
        enforcement_ref=None if ref is None else EnforcementRef(**ref),
        schema_version=p["schema_version"],
    )


def _event_to_json(r: GuardEvent) -> dict:
    return {
        "record_type": "guard_event",
        "schema_version": r.schema_version,
        "event_id": r.event_id,
        "occurred_at": _iso(r.occurred_at),
        "session_id": r.session_id,
        "project": r.project,
        "action": {
            "tool_name": r.action.tool_name,
            "command_verb": r.action.command_verb,
            "target_path": r.action.target_path,
            "heredoc": r.action.heredoc,
        },
        "reason": r.reason,
        "origin": r.origin.value,
        "origin_evidence": r.origin_evidence.value,
        "capture_status": r.capture_status.value,
        "guard_id": r.guard_id,
        "guard_version": r.guard_version,
        "guard_spec_hash": r.guard_spec_hash,
        "unregistered": r.unregistered,
        "guard_hint": r.guard_hint,
        "drift": r.drift,
        "post_remove": r.post_remove,
        "session_id_format": r.session_id_format.value,
    }


def _event_from_json(p: dict) -> GuardEvent:
    action = p["action"]
    if not isinstance(action, dict) or set(action) != {
        "tool_name",
        "command_verb",
        "target_path",
        "heredoc",
    }:
        raise SchemaError("action: 구조화 행동 요약 필드만 허용한다")
    return GuardEvent(
        event_id=p["event_id"],
        occurred_at=_parse_dt(p["occurred_at"], "occurred_at"),
        session_id=p["session_id"],
        project=p["project"],
        action=ActionSummary(**action),
        reason=p["reason"],
        origin=_parse_enum(p["origin"], Origin, "origin"),
        origin_evidence=_parse_enum(p["origin_evidence"], OriginEvidence, "origin_evidence"),
        capture_status=_parse_enum(p["capture_status"], CaptureStatus, "capture_status"),
        guard_id=p["guard_id"],
        guard_version=p["guard_version"],
        guard_spec_hash=p["guard_spec_hash"],
        unregistered=p["unregistered"],
        guard_hint=p["guard_hint"],
        drift=p["drift"],
        post_remove=p["post_remove"],
        session_id_format=_parse_enum(
            p["session_id_format"], SessionIdFormat, "session_id_format"
        ),
        schema_version=p["schema_version"],
    )


def _verdict_to_json(r: PolicyVerdict) -> dict:
    return {
        "record_type": "policy_verdict",
        "schema_version": r.schema_version,
        "verdict_id": r.verdict_id,
        "event_id": r.event_id,
        "verdict": r.verdict.value,
        "reason": r.reason,
        "context_bundle_hash": r.context_bundle_hash,
        "guard_spec_hash": r.guard_spec_hash,
        "rubric_hash": r.rubric_hash,
        "model_id": r.model_id,
        "model_settings_hash": r.model_settings_hash,
        "judged_at": _iso(r.judged_at),
    }


def _verdict_from_json(p: dict) -> PolicyVerdict:
    return PolicyVerdict(
        verdict_id=p["verdict_id"],
        event_id=p["event_id"],
        verdict=_parse_enum(p["verdict"], Verdict, "verdict"),
        reason=p["reason"],
        context_bundle_hash=p["context_bundle_hash"],
        guard_spec_hash=p["guard_spec_hash"],
        rubric_hash=p["rubric_hash"],
        model_id=p["model_id"],
        model_settings_hash=p["model_settings_hash"],
        judged_at=_parse_dt(p["judged_at"], "judged_at"),
        schema_version=p["schema_version"],
    )


def _review_to_json(r: UtilityReview) -> dict:
    return {
        "record_type": "utility_review",
        "schema_version": r.schema_version,
        "review_id": r.review_id,
        "event_id": r.event_id,
        "utility": r.utility.value,
        "note": r.note,
        "reviewed_at": _iso(r.reviewed_at),
    }


def _review_from_json(p: dict) -> UtilityReview:
    return UtilityReview(
        review_id=p["review_id"],
        event_id=p["event_id"],
        utility=_parse_enum(p["utility"], Utility, "utility"),
        note=p["note"],
        reviewed_at=_parse_dt(p["reviewed_at"], "reviewed_at"),
        schema_version=p["schema_version"],
    )


def _decision_to_json(r: GuardDecision) -> dict:
    return {
        "record_type": "guard_decision",
        "schema_version": r.schema_version,
        "decision_id": r.decision_id,
        "guard_id": r.guard_id,
        "decision": r.decision.value,
        "evidence_event_ids": list(r.evidence_event_ids),
        "rationale": r.rationale,
        "decided_at": _iso(r.decided_at),
        "resulting_guard_version": r.resulting_guard_version,
    }


def _decision_from_json(p: dict) -> GuardDecision:
    return GuardDecision(
        decision_id=p["decision_id"],
        guard_id=p["guard_id"],
        decision=_parse_enum(p["decision"], Decision, "decision"),
        evidence_event_ids=tuple(p["evidence_event_ids"]),
        rationale=p["rationale"],
        decided_at=_parse_dt(p["decided_at"], "decided_at"),
        resulting_guard_version=p["resulting_guard_version"],
        schema_version=p["schema_version"],
    )


def _loss_to_json(r: LossRecord) -> dict:
    return {
        "record_type": "loss_record",
        "schema_version": r.schema_version,
        "loss_id": r.loss_id,
        "recorded_at": _iso(r.recorded_at),
        "kind": r.kind.value,
        "detail": r.detail,
        "subject_ref": r.subject_ref,
    }


def _loss_from_json(p: dict) -> LossRecord:
    return LossRecord(
        loss_id=p["loss_id"],
        recorded_at=_parse_dt(p["recorded_at"], "recorded_at"),
        kind=_parse_enum(p["kind"], LossKind, "kind"),
        detail=p["detail"],
        subject_ref=p["subject_ref"],
        schema_version=p["schema_version"],
    )


def _amendment_to_json(r: Amendment) -> dict:
    return {
        "record_type": "amendment",
        "schema_version": r.schema_version,
        "amendment_id": r.amendment_id,
        "target_id": r.target_id,
        "field": r.field,
        "previous_value_hash": r.previous_value_hash,
        "new_value": r.new_value,
        "reason": r.reason,
        "amended_at": _iso(r.amended_at),
    }


def _amendment_from_json(p: dict) -> Amendment:
    return Amendment(
        amendment_id=p["amendment_id"],
        target_id=p["target_id"],
        field=p["field"],
        previous_value_hash=p["previous_value_hash"],
        new_value=p["new_value"],
        reason=p["reason"],
        amended_at=_parse_dt(p["amended_at"], "amended_at"),
        schema_version=p["schema_version"],
    )


_SERIALIZERS = {
    GuardSpec: ("guard_spec", _spec_to_json),
    GuardEvent: ("guard_event", _event_to_json),
    PolicyVerdict: ("policy_verdict", _verdict_to_json),
    UtilityReview: ("utility_review", _review_to_json),
    GuardDecision: ("guard_decision", _decision_to_json),
    LossRecord: ("loss_record", _loss_to_json),
    Amendment: ("amendment", _amendment_to_json),
}

_PARSERS = {
    "guard_spec": (GuardSpec, _spec_from_json),
    "guard_event": (GuardEvent, _event_from_json),
    "policy_verdict": (PolicyVerdict, _verdict_from_json),
    "utility_review": (UtilityReview, _review_from_json),
    "guard_decision": (GuardDecision, _decision_from_json),
    "loss_record": (LossRecord, _loss_from_json),
    "amendment": (Amendment, _amendment_from_json),
}

Record = GuardSpec | GuardEvent | PolicyVerdict | UtilityReview | GuardDecision | LossRecord | Amendment


def _expected_keys(record_type: str) -> set[str]:
    # 모든 레코드에서 dataclass 필드명과 JSON 키가 동일하다.
    cls, _ = _PARSERS[record_type]
    return {f.name for f in fields(cls)}


# 7.0 구형 수용 (003 spec §4.8) — `guard_event`이고 누락 키가 **이것 하나**일 때만
# `unchecked`로 읽는다. 다른 필드 누락·초과 키·다른 record_type은 여전히 손상 줄이다.
# 완화가 없으면 손상되는 것은 guard_event 줄뿐이라 등록부는 멀쩡해 보이면서
# 운영 지표의 분모·사건 목록·검토 큐에서 사건이 조용히 사라진다.
_LEGACY_EVENT_KEY = "session_id_format"


def _accept_legacy_event(payload: dict, missing: set[str], extra: set[str]) -> dict | None:
    if payload.get("record_type") != "guard_event" or extra or missing != {_LEGACY_EVENT_KEY}:
        return None
    # 호출자의 dict를 바꾸지 않는다 — 기존 저장 레코드는 한 건도 재작성하지 않는다.
    return {**payload, _LEGACY_EVENT_KEY: SessionIdFormat.UNCHECKED.value}


def to_json(record: Record) -> dict:
    for cls, (_, serializer) in _SERIALIZERS.items():
        if isinstance(record, cls):
            return serializer(record)
    raise SchemaError(f"직렬화할 수 없는 레코드 타입: {type(record).__name__}")


def record_from_json(payload: dict) -> Record:
    if not isinstance(payload, dict):
        raise SchemaError("레코드 payload는 객체여야 한다")
    record_type = payload.get("record_type")
    if record_type not in _PARSERS:
        raise SchemaError(f"record_type: 알 수 없는 값 {record_type!r}")
    keys = set(payload) - {"record_type"}
    expected = _expected_keys(record_type)
    if keys != expected:
        missing = expected - keys
        extra = keys - expected
        accepted = _accept_legacy_event(payload, missing, extra)
        if accepted is None:
            raise SchemaError(
                f"{record_type}: 키 불일치 (누락 {sorted(missing)}, 초과 {sorted(extra)})"
            )
        payload = accepted
    _, parser = _PARSERS[record_type]
    try:
        return parser(payload)
    except SchemaError:
        raise
    except (TypeError, KeyError, ValueError) as exc:
        raise SchemaError(f"{record_type}: payload 파싱 실패 — {exc}") from exc


def _bind_to_json_methods() -> None:
    for cls in _SERIALIZERS:
        cls.to_json = to_json  # type: ignore[attr-defined]


_bind_to_json_methods()
