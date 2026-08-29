"""가드 수명주기 결정 (spec §3.5, §5 "세션 뒤" 5, §6·§7의 결정 관련 계약).

- 근거 사건 하드 게이트: `evidence_event_ids`의 각 사건은 같은 가드의 등록
  사건이고, 유효 출처가 `operation`이며, 정책 판정·유용성 검토가 모두
  확정값이어야 한다. 아니면 결정을 기록하지 않는다.
- 가치 검증 산입(`countable`)은 저장 필드가 아니라 언제나 파생 계산이다 —
  서로 다른 둘 이상의 `operation` 세션 근거 조건은 입력으로 우회할 수 없고,
  `metrics.decision_completion`과 같은 기준을 쓴다.
- 발동 사건 없는 가드의 결정은 기록 가능하되 별도 표기하고 지표 밖이다.
- `modify`는 등록부 경유 새 GuardSpec 버전 생성을 강제하고, 새 버전이 실제
  가드 구현물에 반영됐는지 `enforcement_ref` 대조(drift 개념 재사용)로
  확인하는 경로를 제공한다.
- `remove` 결정 뒤 같은 가드의 발동은 append 순서를 근거로 `post-remove`로
  판별·표시한다 (기록 자체는 recorder 소관).
- 결정 변경은 이전 결정을 덮지 않는다 — 새 레코드 append와 이력 조회뿐이다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from rejectbench.dataset import Dataset
from rejectbench.metrics import Status, review_status, verdict_status
from rejectbench.records import (
    CaptureStatus,
    Decision,
    EnforcementRef,
    GuardDecision,
    GuardSpec,
    Origin,
    Utility,
    Verdict,
)
from rejectbench.registry import GuardRegistry, RegistryError, enforcement_ref_for
from rejectbench.store import AppendStore


class DecisionError(Exception):
    """결정 경로 위반 — 미등록 가드, 부적격 근거, 등록부 밖 modify 버전 등."""


# --- 구현물 대조 (drift 개념 재사용) -----------------------------------------


class EnforcementStatus(StrEnum):
    IN_SYNC = "in_sync"
    DRIFT = "drift"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class EnforcementCheck:
    status: EnforcementStatus
    detail: str


def check_enforcement(spec: GuardSpec) -> EnforcementCheck:
    """spec의 `enforcement_ref`와 실제 구현물 파일 해시를 대조한다. 읽기만 한다."""
    ref = spec.enforcement_ref
    if ref is None:
        return EnforcementCheck(
            EnforcementStatus.UNVERIFIABLE, "enforcement_ref 없음 — 대조 불가"
        )
    try:
        current = enforcement_ref_for(ref.script_path)
    except RegistryError as exc:
        return EnforcementCheck(
            EnforcementStatus.UNVERIFIABLE, f"구현물 파일을 읽을 수 없다: {exc}"
        )
    if current.file_hash == ref.file_hash:
        return EnforcementCheck(
            EnforcementStatus.IN_SYNC, "구현물 해시가 enforcement_ref와 일치한다"
        )
    return EnforcementCheck(
        EnforcementStatus.DRIFT, "구현물 해시가 enforcement_ref와 다르다 — drift"
    )


# --- 근거 적격성과 산입 표기 --------------------------------------------------


def _guard_event_ids(dataset: Dataset, guard_id: str) -> list[str]:
    return [
        event_id
        for event_id, event in dataset.events.items()
        if not event.unregistered and event.guard_id == guard_id
    ]


def _evidence_problem(dataset: Dataset, guard_id: str, event_id: str) -> str | None:
    """근거 사건 부적격 사유. 적격이면 None — metrics의 판정 가능 정의와 같다."""
    event = dataset.events.get(event_id)
    if event is None:
        return "존재하지 않는 사건"
    if event.unregistered:
        return "unregistered 사건"
    if event.guard_id != guard_id:
        return "다른 가드의 사건"
    if dataset.effective_origin(event_id) is not Origin.OPERATION:
        return "유효 출처가 operation이 아니다"
    if verdict_status(dataset, event_id) is not Status.CONFIRMED:
        return "정책 판정이 확정값이 아니다 (보류 또는 미처리)"
    if review_status(dataset, event_id) is not Status.CONFIRMED:
        return "유용성 검토가 확정값이 아니다 (보류 또는 미처리)"
    return None


@dataclass(frozen=True)
class DecisionAnnotation:
    """가치 검증 산입 여부의 파생 표기 — 저장하지 않고 항상 재계산한다."""

    countable: bool
    no_event_guard: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DecisionOutcome:
    decision: GuardDecision
    annotation: DecisionAnnotation


def annotate_decision(dataset: Dataset, decision: GuardDecision) -> DecisionAnnotation:
    guard_events = _guard_event_ids(dataset, decision.guard_id)
    if not guard_events:
        return DecisionAnnotation(
            countable=False,
            no_event_guard=True,
            reasons=("발동 사건 없는 가드의 결정 — 별도 표기, 지표 밖",),
        )
    reasons: list[str] = []
    evidence = decision.evidence_event_ids
    if not evidence:
        reasons.append("근거 사건이 없다")
    for event_id in evidence:
        problem = _evidence_problem(dataset, decision.guard_id, event_id)
        if problem is not None:
            reasons.append(f"{event_id}: {problem}")
    if not reasons:
        sessions = {dataset.events[event_id].session_id for event_id in evidence}
        if len(sessions) < 2:
            reasons.append("서로 다른 operation 세션이 2개 미만이다")
    return DecisionAnnotation(
        countable=not reasons, no_event_guard=False, reasons=tuple(reasons)
    )


# --- 결정 기록 ----------------------------------------------------------------


def validate_decision_inputs(
    dataset: Dataset, *, guard_id: str, evidence_event_ids: tuple[str, ...]
) -> None:
    """기록 전 하드 게이트. 부적격 근거로는 결정을 기록하지 않는다."""
    if not any(gid == guard_id for gid, _ in dataset.specs_by_key):
        raise DecisionError(f"등록되지 않은 가드: {guard_id}")
    guard_events = _guard_event_ids(dataset, guard_id)
    evidence = tuple(evidence_event_ids)
    if guard_events and not evidence:
        raise DecisionError("발동 사건이 있는 가드의 결정은 근거 사건 연결이 필수다")
    if not guard_events and evidence:
        raise DecisionError("발동 사건 없는 가드의 결정에는 근거 사건을 연결할 수 없다")
    for event_id in evidence:
        problem = _evidence_problem(dataset, guard_id, event_id)
        if problem is not None:
            raise DecisionError(f"근거 사건 부적격 — {event_id}: {problem}")


def record_decision(
    store: AppendStore,
    *,
    guard_id: str,
    decision: Decision,
    evidence_event_ids: tuple[str, ...] = (),
    rationale: str,
    resulting_guard_version: int | None = None,
    decided_at: datetime | None = None,
    decision_id: str | None = None,
) -> DecisionOutcome:
    """`keep | modify | remove` 결정을 append한다. 이전 결정은 절대 덮지 않는다.

    `modify`는 결과 버전이 등록부에 실재해야 한다 — 새 버전 생성까지 한
    경로로 강제하려면 `record_modify`를 쓴다.
    """
    dataset = Dataset(store.load().records)
    evidence = tuple(evidence_event_ids)
    validate_decision_inputs(dataset, guard_id=guard_id, evidence_event_ids=evidence)
    if decision is Decision.MODIFY:
        if resulting_guard_version is None:
            raise DecisionError("modify 결정에는 resulting_guard_version이 필수다")
        if (guard_id, resulting_guard_version) not in dataset.specs_by_key:
            raise DecisionError(
                f"modify 결과 버전이 등록부에 없다: {guard_id} "
                f"v{resulting_guard_version} — 새 버전은 등록부를 경유해 생성한다"
            )
    record = GuardDecision(
        decision_id=decision_id or f"dc-{uuid.uuid4().hex}",
        guard_id=guard_id,
        decision=decision,
        evidence_event_ids=evidence,
        rationale=rationale,
        decided_at=decided_at or datetime.now(timezone.utc),
        resulting_guard_version=resulting_guard_version,
    )
    store.append(record)
    return DecisionOutcome(decision=record, annotation=annotate_decision(dataset, record))


@dataclass(frozen=True)
class ModifyOutcome:
    decision: GuardDecision
    annotation: DecisionAnnotation
    spec: GuardSpec
    enforcement: EnforcementCheck


def record_modify(
    store: AppendStore,
    *,
    guard_id: str,
    evidence_event_ids: tuple[str, ...],
    rationale: str,
    project: str,
    purpose: str,
    policy: str,
    exceptions: tuple[str, ...] = (),
    allow_examples: tuple[str, ...],
    block_examples: tuple[str, ...],
    enforcement_ref: EnforcementRef | None = None,
    decided_at: datetime | None = None,
    decision_id: str | None = None,
) -> ModifyOutcome:
    """modify 결정의 유일한 온전한 경로 — 등록부 경유 새 버전 생성을 강제한다.

    내용이 최신 버전과 같아 새 버전이 생성되지 않으면 결정도 기록하지 않는다.
    반환값에 새 버전의 `enforcement_ref` 대조 결과를 함께 담아, 구현물 반영
    여부(불일치면 drift)를 바로 드러낸다.
    """
    dataset = Dataset(store.load().records)
    evidence = tuple(evidence_event_ids)
    # 등록부 append 전에 근거를 먼저 검증한다 — 부적격 근거로 spec만 만들지 않는다.
    validate_decision_inputs(dataset, guard_id=guard_id, evidence_event_ids=evidence)
    registry = GuardRegistry(store)
    result = registry.register(
        guard_id=guard_id,
        project=project,
        purpose=purpose,
        policy=policy,
        exceptions=tuple(exceptions),
        allow_examples=tuple(allow_examples),
        block_examples=tuple(block_examples),
        enforcement_ref=enforcement_ref,
    )
    if not result.created:
        raise DecisionError(
            "modify인데 내용이 최신 버전과 같다 — 새 GuardSpec 버전이 생성되지 않았다"
        )
    outcome = record_decision(
        store,
        guard_id=guard_id,
        decision=Decision.MODIFY,
        evidence_event_ids=evidence,
        rationale=rationale,
        resulting_guard_version=result.spec.version,
        decided_at=decided_at,
        decision_id=decision_id,
    )
    return ModifyOutcome(
        decision=outcome.decision,
        annotation=outcome.annotation,
        spec=result.spec,
        enforcement=check_enforcement(result.spec),
    )


# --- 이력·post-remove ---------------------------------------------------------


def decision_history(dataset: Dataset, guard_id: str) -> list[GuardDecision]:
    """append 순서 그대로의 결정 이력 — 번복도 새 레코드로만 남는다."""
    return [d for d in dataset.decisions if d.guard_id == guard_id]


def _first_positions(dataset: Dataset) -> dict[str, int]:
    positions: dict[str, int] = {}
    for pos, record in enumerate(dataset.records):
        positions.setdefault(record.record_id, pos)
    return positions


def post_remove_event_ids(dataset: Dataset) -> list[str]:
    """`remove` 결정 뒤에 append된 같은 가드의 발동 사건 (append 순서 근거).

    가드의 유효 결정은 사건 시점(append 위치) 직전의 마지막 결정이다 —
    remove가 이후 결정으로 번복되면 그 뒤 사건은 post-remove가 아니다.
    사건 레코드에 이미 저장된 `post_remove` 표시도 존중한다.
    """
    positions = _first_positions(dataset)
    decisions_by_guard: dict[str, list[tuple[int, Decision]]] = {}
    for decision in dataset.decisions:
        decisions_by_guard.setdefault(decision.guard_id, []).append(
            (positions[decision.decision_id], decision.decision)
        )
    flagged: list[str] = []
    for event_id, event in dataset.events.items():
        if event.post_remove:
            flagged.append(event_id)
            continue
        if event.unregistered:
            continue
        history = decisions_by_guard.get(event.guard_id)
        if not history:
            continue
        event_pos = positions[event_id]
        effective: Decision | None = None
        for pos, value in history:
            if pos < event_pos:
                effective = value
        if effective is Decision.REMOVE:
            flagged.append(event_id)
    return flagged


def is_post_remove(dataset: Dataset, event_id: str) -> bool:
    return event_id in set(post_remove_event_ids(dataset))


# --- 가드별 최소 뷰 -----------------------------------------------------------


def _verdict_label(dataset: Dataset, event_id: str) -> str:
    latest = dataset.latest_verdict(event_id)
    if latest is None:
        return "미처리"
    if latest.verdict is Verdict.INSUFFICIENT_CONTEXT:
        return f"{latest.verdict.value}(보류)"
    return latest.verdict.value


def _utility_label(dataset: Dataset, event_id: str) -> str:
    latest = dataset.latest_review(event_id)
    if latest is None:
        return "미처리"
    if latest.utility is Utility.UNCERTAIN:
        return f"{latest.utility.value}(보류)"
    return latest.utility.value


@dataclass(frozen=True)
class EventRow:
    event_id: str
    occurred_at: datetime
    session_id: str
    effective_origin: Origin
    capture_status: CaptureStatus
    verdict_label: str
    utility_label: str
    decidable: bool
    post_remove: bool
    drift: bool


@dataclass(frozen=True)
class DecisionRow:
    decision: GuardDecision
    annotation: DecisionAnnotation


@dataclass(frozen=True)
class GuardView:
    guard_id: str
    project: str
    versions: tuple[int, ...]
    latest_version: int
    enforcement: EnforcementCheck
    operation_session_count: int
    decidable_session_count: int
    guard_decidable: bool
    events: tuple[EventRow, ...]
    decisions: tuple[DecisionRow, ...]
    post_remove_count: int


def build_guard_view(dataset: Dataset, guard_id: str) -> GuardView:
    """가드 하나의 세션 수·사건·두 판단 축·판정 가능 상태·결정 이력."""
    specs = sorted(
        (spec for (gid, _), spec in dataset.specs_by_key.items() if gid == guard_id),
        key=lambda spec: spec.version,
    )
    if not specs:
        raise DecisionError(f"등록되지 않은 가드: {guard_id}")
    latest = specs[-1]
    post_remove = set(post_remove_event_ids(dataset))
    rows: list[EventRow] = []
    operation_sessions: set[str] = set()
    decidable_sessions: set[str] = set()
    for event_id, event in dataset.events.items():
        if event.unregistered or event.guard_id != guard_id:
            continue
        effective = dataset.effective_origin(event_id)
        decidable = _evidence_problem(dataset, guard_id, event_id) is None
        if effective is Origin.OPERATION:
            operation_sessions.add(event.session_id)
        if decidable:
            decidable_sessions.add(event.session_id)
        rows.append(
            EventRow(
                event_id=event_id,
                occurred_at=event.occurred_at,
                session_id=event.session_id,
                effective_origin=effective,
                capture_status=event.capture_status,
                verdict_label=_verdict_label(dataset, event_id),
                utility_label=_utility_label(dataset, event_id),
                decidable=decidable,
                post_remove=event_id in post_remove,
                drift=event.drift,
            )
        )
    decision_rows = tuple(
        DecisionRow(decision=decision, annotation=annotate_decision(dataset, decision))
        for decision in dataset.decisions
        if decision.guard_id == guard_id
    )
    return GuardView(
        guard_id=guard_id,
        project=latest.project,
        versions=tuple(spec.version for spec in specs),
        latest_version=latest.version,
        enforcement=check_enforcement(latest),
        operation_session_count=len(operation_sessions),
        decidable_session_count=len(decidable_sessions),
        guard_decidable=len(decidable_sessions) >= 2,
        events=tuple(rows),
        decisions=decision_rows,
        post_remove_count=sum(1 for row in rows if row.post_remove),
    )


def render_guard_view(view: GuardView) -> str:
    """한 화면 텍스트 뷰 — 로컬 CLI 출력 전용, 파일로 내보내는 경로가 없다."""
    lines = [
        f"가드 {view.guard_id} — project {view.project}",
        "spec 버전: "
        + ", ".join(f"v{v}" for v in view.versions)
        + f" (최신 v{view.latest_version})",
        f"구현물 대조: {view.enforcement.status.value} — {view.enforcement.detail}",
        (
            f"운영 세션 수: {view.operation_session_count} · "
            f"판정 가능 세션 수: {view.decidable_session_count} → "
            f"판정 가능 가드: {'예' if view.guard_decidable else '아니오'} "
            "(기준: 서로 다른 operation 세션 2개 이상, 두 판단 모두 확정값)"
        ),
        f"사건 {len(view.events)}건 (post-remove {view.post_remove_count}건):",
    ]
    for row in view.events:
        flags = []
        if row.decidable:
            flags.append("판정 가능")
        if row.post_remove:
            flags.append("post-remove")
        if row.drift:
            flags.append("drift")
        if row.capture_status is CaptureStatus.PARTIAL:
            flags.append("partial")
        flag_text = f"  [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"  {row.event_id}  {row.occurred_at.isoformat()}  세션 {row.session_id}  "
            f"출처 {row.effective_origin.value}  판정 {row.verdict_label}  "
            f"검토 {row.utility_label}{flag_text}"
        )
    lines.append(f"결정 이력 {len(view.decisions)}건:")
    for decision_row in view.decisions:
        decision = decision_row.decision
        annotation = decision_row.annotation
        if annotation.countable:
            mark = "가치 검증 산입"
        elif annotation.no_event_guard:
            mark = "발동 사건 없음 — 지표 밖"
        else:
            mark = "산입 제외: " + "; ".join(annotation.reasons)
        version_text = (
            f" → v{decision.resulting_guard_version}"
            if decision.resulting_guard_version is not None
            else ""
        )
        evidence_text = ",".join(decision.evidence_event_ids) or "-"
        lines.append(
            f"  {decision.decision_id}  {decision.decided_at.isoformat()}  "
            f"{decision.decision.value}{version_text}  근거 {evidence_text}  [{mark}]"
        )
    return "\n".join(lines)
