"""지표 원시 계산 (spec §6). 보고서 표면은 T6 소관 — 여기는 불변식만 구현한다.

- 판정 가능 가드(분모): 서로 다른 둘 이상의 `operation` 세션 사건이 있고,
  그 사건들의 정책 판정과 유용성 검토가 모두 확정값인 가드.
- 분자는 분모의 부분집합으로만 센다 (구성상 강제).
- 보류값(`insufficient_context`/`uncertain`)은 기록된 상태로, 레코드가 없는
  미처리와 구분한다. 둘 다 판정 가능 사건에서 제외되지만 사건이 가드 자격을
  박탈하지는 않는다.
- test/unknown/unregistered 사건은 운영 지표의 분자·분모에 들어가지 않는다.
- 분모 0은 성공이 아니라 미검증이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rejectbench.dataset import Dataset
from rejectbench.records import Origin, Utility, Verdict


class Status(StrEnum):
    CONFIRMED = "confirmed"
    HELD = "held"  # 기록된 보류값
    UNPROCESSED = "unprocessed"  # 레코드 자체가 없음


def verdict_status(dataset: Dataset, event_id: str) -> Status:
    latest = dataset.latest_verdict(event_id)
    if latest is None:
        return Status.UNPROCESSED
    if latest.verdict is Verdict.INSUFFICIENT_CONTEXT:
        return Status.HELD
    return Status.CONFIRMED


def review_status(dataset: Dataset, event_id: str) -> Status:
    latest = dataset.latest_review(event_id)
    if latest is None:
        return Status.UNPROCESSED
    if latest.utility is Utility.UNCERTAIN:
        return Status.HELD
    return Status.CONFIRMED


def operation_event_ids(dataset: Dataset) -> list[str]:
    """운영 지표 대상 사건 — 등록된, 유효 출처 operation 사건만."""
    return [
        event_id
        for event_id, event in dataset.events.items()
        if not event.unregistered and dataset.effective_origin(event_id) is Origin.OPERATION
    ]


def decidable_event_ids(dataset: Dataset) -> list[str]:
    """판정 가능 사건 — operation 사건 중 판정·검토가 모두 확정값인 것."""
    return [
        event_id
        for event_id in operation_event_ids(dataset)
        if verdict_status(dataset, event_id) is Status.CONFIRMED
        and review_status(dataset, event_id) is Status.CONFIRMED
    ]


def _decidable_events_by_guard(dataset: Dataset) -> dict[str, list[str]]:
    by_guard: dict[str, list[str]] = {}
    for event_id in decidable_event_ids(dataset):
        guard_id = dataset.events[event_id].guard_id
        by_guard.setdefault(guard_id, []).append(event_id)
    return by_guard


@dataclass(frozen=True)
class Completion:
    """증거 기반 결정 완료율의 원수. 분자⊆분모는 구성상 항상 성립한다."""

    decidable_guard_ids: frozenset[str]
    decided_guard_ids: frozenset[str]

    def __post_init__(self):
        assert self.decided_guard_ids <= self.decidable_guard_ids

    @property
    def denominator(self) -> int:
        return len(self.decidable_guard_ids)

    @property
    def numerator(self) -> int:
        return len(self.decided_guard_ids)

    @property
    def unverified(self) -> bool:
        """분모 0은 성공이 아니라 미검증이다."""
        return self.denominator == 0

    @property
    def fraction(self) -> str:
        return f"{self.numerator}/{self.denominator}"

    @property
    def percentage(self) -> float | None:
        if self.unverified:
            return None
        return 100.0 * self.numerator / self.denominator


def decision_completion(dataset: Dataset) -> Completion:
    by_guard = _decidable_events_by_guard(dataset)

    decidable: set[str] = set()
    for guard_id, event_ids in by_guard.items():
        sessions = {dataset.events[eid].session_id for eid in event_ids}
        if len(sessions) >= 2:
            decidable.add(guard_id)

    decided: set[str] = set()
    for decision in dataset.decisions:
        guard_id = decision.guard_id
        if guard_id not in decidable:
            # 분자는 분모의 부분집합으로만 센다.
            continue
        guard_decidable = set(by_guard.get(guard_id, []))
        evidence = set(decision.evidence_event_ids)
        if not evidence or not evidence <= guard_decidable:
            continue
        evidence_sessions = {dataset.events[eid].session_id for eid in evidence}
        if len(evidence_sessions) >= 2:
            decided.add(guard_id)

    return Completion(
        decidable_guard_ids=frozenset(decidable),
        decided_guard_ids=frozenset(decided),
    )
