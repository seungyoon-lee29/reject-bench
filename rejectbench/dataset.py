"""append 순서를 보존한 레코드 집합과 참조 무결성 검사 (spec §3, §7).

Dataset은 레코드를 데이터로만 다룬다. 원본을 절대 변경하지 않고, amendment의
효과는 `effective_origin` 같은 파생 조회로만 드러난다.
"""

from __future__ import annotations

from dataclasses import dataclass

from rejectbench.origin import ORIGIN_FIELD, OriginTransitionError, validate_origin_transition
from rejectbench.records import (
    Amendment,
    Decision,
    GuardDecision,
    GuardEvent,
    GuardSpec,
    Origin,
    PolicyVerdict,
    Record,
    UtilityReview,
)


@dataclass(frozen=True)
class Violation:
    kind: str
    record_id: str
    detail: str


class Dataset:
    def __init__(self, records: list[Record] | tuple[Record, ...]):
        self.records: list[Record] = list(records)
        self._position: dict[str, int] = {}
        self._duplicate_ids: list[str] = []
        self.specs_by_key: dict[tuple[str, int], GuardSpec] = {}
        self._spec_position: dict[tuple[str, int], int] = {}
        self.events: dict[str, GuardEvent] = {}
        self._event_position: dict[str, int] = {}
        self._verdicts: dict[str, list[PolicyVerdict]] = {}
        self._reviews: dict[str, list[UtilityReview]] = {}
        self.decisions: list[GuardDecision] = []
        self._amendments: dict[str, list[Amendment]] = {}

        for pos, record in enumerate(self.records):
            rid = record.record_id
            if rid in self._position:
                self._duplicate_ids.append(rid)
            else:
                self._position[rid] = pos
            if isinstance(record, GuardSpec):
                key = (record.guard_id, record.version)
                if key not in self.specs_by_key:
                    self.specs_by_key[key] = record
                    self._spec_position[key] = pos
            elif isinstance(record, GuardEvent):
                if record.event_id not in self.events:
                    self.events[record.event_id] = record
                    self._event_position[record.event_id] = pos
            elif isinstance(record, PolicyVerdict):
                self._verdicts.setdefault(record.event_id, []).append(record)
            elif isinstance(record, UtilityReview):
                self._reviews.setdefault(record.event_id, []).append(record)
            elif isinstance(record, GuardDecision):
                self.decisions.append(record)
            elif isinstance(record, Amendment):
                self._amendments.setdefault(record.target_id, []).append(record)

    # --- 조회 ---------------------------------------------------------------

    def verdicts_for(self, event_id: str) -> list[PolicyVerdict]:
        """append 순서 그대로 — 재판정도 이전 레코드를 보존한다."""
        return list(self._verdicts.get(event_id, []))

    def reviews_for(self, event_id: str) -> list[UtilityReview]:
        return list(self._reviews.get(event_id, []))

    def latest_verdict(self, event_id: str) -> PolicyVerdict | None:
        verdicts = self._verdicts.get(event_id)
        return verdicts[-1] if verdicts else None

    def latest_review(self, event_id: str) -> UtilityReview | None:
        reviews = self._reviews.get(event_id)
        return reviews[-1] if reviews else None

    def amendments_for(self, target_id: str) -> list[Amendment]:
        return list(self._amendments.get(target_id, []))

    def effective_origin(self, event_id: str) -> Origin:
        """origin amendment를 append 순서로 적용한 유효 출처.

        금지 전이(예: unknown→operation 승격)는 적용하지 않는다 — 무결성
        검사에서 위반으로 드러난다.
        """
        event = self.events[event_id]
        current = event.origin
        for amendment in self._amendments.get(event_id, []):
            if amendment.field != ORIGIN_FIELD:
                continue
            try:
                new = Origin(amendment.new_value)
                validate_origin_transition(current, new)
            except (ValueError, OriginTransitionError):
                continue
            current = new
        return current

    # --- 무결성 -------------------------------------------------------------

    def check_integrity(self) -> list[Violation]:
        violations: list[Violation] = []
        for rid in self._duplicate_ids:
            violations.append(Violation("duplicate_record_id", rid, "고유 id 충돌"))
        for event in self.events.values():
            violations.extend(self._check_event(event))
        for event_id, verdicts in self._verdicts.items():
            for verdict in verdicts:
                violations.extend(self._check_verdict(event_id, verdict))
        for event_id, reviews in self._reviews.items():
            for review in reviews:
                if event_id not in self.events:
                    violations.append(
                        Violation("missing_event", review.record_id, f"사건 없음: {event_id}")
                    )
        for decision in self.decisions:
            violations.extend(self._check_decision(decision))
        for target_id, amendments in self._amendments.items():
            violations.extend(self._check_amendments(target_id, amendments))
        return violations

    def _check_event(self, event: GuardEvent) -> list[Violation]:
        if event.unregistered:
            # 미등록 발동은 참조 대신 표시+추정 정보로 기록된다 — 위반이 아니다.
            return []
        violations: list[Violation] = []
        key = (event.guard_id, event.guard_version)
        spec = self.specs_by_key.get(key)
        if spec is None:
            violations.append(
                Violation(
                    "missing_spec",
                    event.event_id,
                    f"참조 spec 없음: {event.guard_id} v{event.guard_version}",
                )
            )
            return violations
        if event.guard_spec_hash != spec.content_hash:
            violations.append(
                Violation(
                    "spec_hash_mismatch",
                    event.event_id,
                    "사건의 guard_spec_hash가 spec content_hash와 다르다",
                )
            )
        if event.origin is Origin.OPERATION:
            # 선행성은 벽시계에만 의존하지 않는다 — append 순서가 근거다.
            if self._spec_position[key] > self._event_position[event.event_id]:
                violations.append(
                    Violation(
                        "spec_not_before_event",
                        event.event_id,
                        "operation 사건이 spec보다 먼저 append됐다",
                    )
                )
            if spec.created_at > event.occurred_at:
                violations.append(
                    Violation(
                        "spec_created_after_event",
                        event.event_id,
                        "spec 생성 시각이 사건 시각보다 늦다 (벽시계 이상)",
                    )
                )
        return violations

    def _check_verdict(self, event_id: str, verdict: PolicyVerdict) -> list[Violation]:
        event = self.events.get(event_id)
        if event is None:
            return [Violation("missing_event", verdict.record_id, f"사건 없음: {event_id}")]
        if event.unregistered:
            # 판정 입력은 사건이 참조한 정확한 GuardSpec을 요구한다 (spec §3.3).
            return [
                Violation(
                    "verdict_for_unregistered_event",
                    verdict.record_id,
                    "미등록 사건은 참조 spec이 없어 판정 대상이 아니다",
                )
            ]
        if verdict.guard_spec_hash != event.guard_spec_hash:
            return [
                Violation(
                    "verdict_spec_hash_mismatch",
                    verdict.record_id,
                    "판정의 guard_spec_hash가 사건 참조와 다르다",
                )
            ]
        return []

    def _check_decision(self, decision: GuardDecision) -> list[Violation]:
        violations: list[Violation] = []
        for event_id in decision.evidence_event_ids:
            event = self.events.get(event_id)
            if event is None:
                violations.append(
                    Violation("missing_event", decision.record_id, f"근거 사건 없음: {event_id}")
                )
                continue
            if event.unregistered or event.guard_id != decision.guard_id:
                violations.append(
                    Violation(
                        "evidence_guard_mismatch",
                        decision.record_id,
                        f"근거 사건 {event_id}이 결정 대상 가드의 사건이 아니다",
                    )
                )
        if decision.decision is Decision.MODIFY:
            key = (decision.guard_id, decision.resulting_guard_version)
            if key not in self.specs_by_key:
                violations.append(
                    Violation(
                        "resulting_version_missing",
                        decision.record_id,
                        f"modify 결과 버전의 spec 없음: v{decision.resulting_guard_version}",
                    )
                )
        return violations

    def _check_amendments(self, target_id: str, amendments: list[Amendment]) -> list[Violation]:
        violations: list[Violation] = []
        if target_id not in self._position:
            for amendment in amendments:
                violations.append(
                    Violation(
                        "missing_amendment_target",
                        amendment.record_id,
                        f"원본 레코드 없음: {target_id}",
                    )
                )
            return violations
        event = self.events.get(target_id)
        if event is not None:
            current = event.origin
            for amendment in amendments:
                if amendment.field != ORIGIN_FIELD:
                    continue
                try:
                    new = Origin(amendment.new_value)
                except ValueError:
                    violations.append(
                        Violation(
                            "forbidden_origin_amendment",
                            amendment.record_id,
                            f"origin 값이 아니다: {amendment.new_value!r}",
                        )
                    )
                    continue
                try:
                    validate_origin_transition(current, new)
                except OriginTransitionError as exc:
                    violations.append(
                        Violation("forbidden_origin_amendment", amendment.record_id, str(exc))
                    )
                    continue
                current = new
        return violations
