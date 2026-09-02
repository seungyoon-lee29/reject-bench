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

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from rejectbench.dataset import Dataset
from rejectbench.records import GuardEvent, Origin, SchemaError, Utility, Verdict

#: 운영 사건 집합을 더 좁히는 술어 (003 spec §9 파티션). `None`이면 전체다. 지표의
#: 정의는 그대로고 **어느 사건 위에서 재느냐**만 달라진다 — 보고서가 정의를 재정의하지
#: 않고 이 한 정의를 파티션마다 재사용하게 하는 자리다.
EventFilter = Callable[[GuardEvent], bool] | None


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


def operation_event_ids(dataset: Dataset, *, event_filter: EventFilter = None) -> list[str]:
    """운영 지표 대상 사건 — 등록된, 유효 출처 operation 사건만 (필터는 그 안쪽)."""
    return [
        event_id
        for event_id, event in dataset.events.items()
        if not event.unregistered
        and dataset.effective_origin(event_id) is Origin.OPERATION
        and (event_filter is None or event_filter(event))
    ]


def decidable_event_ids(dataset: Dataset, *, event_filter: EventFilter = None) -> list[str]:
    """판정 가능 사건 — operation 사건 중 판정·검토가 모두 확정값인 것."""
    return [
        event_id
        for event_id in operation_event_ids(dataset, event_filter=event_filter)
        if verdict_status(dataset, event_id) is Status.CONFIRMED
        and review_status(dataset, event_id) is Status.CONFIRMED
    ]


def _decidable_events_by_guard(
    dataset: Dataset, *, event_filter: EventFilter = None
) -> dict[str, list[str]]:
    by_guard: dict[str, list[str]] = {}
    for event_id in decidable_event_ids(dataset, event_filter=event_filter):
        guard_id = dataset.events[event_id].guard_id
        by_guard.setdefault(guard_id, []).append(event_id)
    return by_guard


@dataclass(frozen=True)
class Completion:
    """증거 기반 결정 완료율의 원수. 분자⊆분모는 구성상 항상 성립한다."""

    decidable_guard_ids: frozenset[str]
    decided_guard_ids: frozenset[str]

    def __post_init__(self):
        if not self.decided_guard_ids <= self.decidable_guard_ids:
            raise SchemaError("분자⊆분모 위반: 결정 가드가 판정 가능 집합 밖에 있다")

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


def decision_completion(dataset: Dataset, *, event_filter: EventFilter = None) -> Completion:
    """증거 기반 결정 완료율의 원수. 파티션 위에서는 **근거 ∩ 파티션**으로 잰다 (003 spec §9.6).

    인용된 근거는 전부 판정 가능 사건이어야 한다(전체 기준 — 보류·미처리 사건을 인용한
    결정은 어느 벌에서도 세지 않는다). 그 위에서 파티션 완료율은 근거를 파티션으로 걸러
    남은 사건이 2세션 이상일 때 "결정됨"이다 — 근거를 좁게 골라 인용하는 것으로 파티션
    값을 올릴 수 없게, 결정은 그 시점 판정 가능 사건 전부를 인용한다(프로토콜 ⑩).

    가드 단위 지표라 파티션 두 벌의 합은 전체와 같지 않을 수 있다 — 한 가드가 전체에서는
    판정 가능(서로 다른 2세션)이면서 어느 파티션 안에서도 판정 불가일 수 있다. 합으로
    검산하지 말 것.
    """
    by_guard = _decidable_events_by_guard(dataset, event_filter=event_filter)
    by_guard_all = (
        by_guard if event_filter is None else _decidable_events_by_guard(dataset)
    )

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
        cited = set(decision.evidence_event_ids)
        if not cited or not cited <= set(by_guard_all.get(guard_id, [])):
            continue  # 판정 가능하지 않은 사건을 인용한 결정은 근거로 인정하지 않는다
        evidence = cited & set(by_guard.get(guard_id, []))
        evidence_sessions = {dataset.events[eid].session_id for eid in evidence}
        if len(evidence_sessions) >= 2:
            decided.add(guard_id)

    return Completion(
        decidable_guard_ids=frozenset(decidable),
        decided_guard_ids=frozenset(decided),
    )
