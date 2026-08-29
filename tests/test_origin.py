"""origin 결정표와 amendment 전이 규칙."""

from __future__ import annotations

import pytest

from rejectbench import (
    Dataset,
    Origin,
    OriginEvidence,
    OriginTransitionError,
    amend_origin,
    decide_origin,
    value_hash,
)
from tests.factories import make_event, make_spec, ts


class TestDecisionTable:
    def test_test_flag_on_is_always_test(self):
        assert decide_origin(context_available=True, test_flag=True) == (
            Origin.TEST,
            OriginEvidence.EXPLICIT_FLAG,
        )

    def test_flag_absent_is_operation_with_default_inherited(self):
        assert decide_origin(context_available=True, test_flag=False) == (
            Origin.OPERATION,
            OriginEvidence.DEFAULT_INHERITED,
        )

    def test_no_execution_context_is_unknown(self):
        assert decide_origin(context_available=False) == (
            Origin.UNKNOWN,
            OriginEvidence.NO_CONTEXT,
        )

    def test_no_context_wins_even_if_flag_claimed(self):
        # 맥락 자체가 없으면 플래그 값은 근거가 될 수 없다.
        assert decide_origin(context_available=False, test_flag=True) == (
            Origin.UNKNOWN,
            OriginEvidence.NO_CONTEXT,
        )


class TestDemotionAmendment:
    def test_operation_to_test_demotion_produces_amendment(self):
        event = make_event(make_spec())
        amendment = amend_origin(
            event,
            new_origin=Origin.TEST,
            reason="검토에서 강제 발동으로 확인",
            amendment_id="am-1",
            amended_at=ts(200),
        )
        assert amendment.target_id == event.event_id
        assert amendment.field == "origin"
        assert amendment.new_value == "test"
        assert amendment.previous_value_hash == value_hash("operation")
        assert amendment.reason

    def test_demotion_requires_reason(self):
        event = make_event(make_spec())
        with pytest.raises(Exception):
            amend_origin(
                event,
                new_origin=Origin.TEST,
                reason="",
                amendment_id="am-1",
                amended_at=ts(200),
            )

    def test_unknown_to_test_correction_allowed(self):
        event = make_event(
            make_spec(),
            origin=Origin.UNKNOWN,
            origin_evidence=OriginEvidence.NO_CONTEXT,
        )
        amendment = amend_origin(
            event,
            new_origin=Origin.TEST,
            reason="본인 시험 실행으로 확인",
            amendment_id="am-1",
            amended_at=ts(200),
        )
        assert amendment.new_value == "test"

    def test_unknown_to_operation_promotion_forbidden(self):
        event = make_event(
            make_spec(),
            origin=Origin.UNKNOWN,
            origin_evidence=OriginEvidence.NO_CONTEXT,
        )
        with pytest.raises(OriginTransitionError):
            amend_origin(
                event,
                new_origin=Origin.OPERATION,
                reason="아마 운영이었을 것",
                amendment_id="am-1",
                amended_at=ts(200),
            )

    def test_test_to_operation_promotion_forbidden(self):
        event = make_event(
            make_spec(),
            origin=Origin.TEST,
            origin_evidence=OriginEvidence.EXPLICIT_FLAG,
        )
        with pytest.raises(OriginTransitionError):
            amend_origin(
                event,
                new_origin=Origin.OPERATION,
                reason="사실 운영이었다",
                amendment_id="am-1",
                amended_at=ts(200),
            )


class TestEffectiveOrigin:
    def test_amendment_changes_effective_origin_without_touching_original(self):
        spec = make_spec()
        event = make_event(spec)
        amendment = amend_origin(
            event,
            new_origin=Origin.TEST,
            reason="검토 강등",
            amendment_id="am-1",
            amended_at=ts(200),
        )
        ds = Dataset([spec, event, amendment])
        assert ds.effective_origin(event.event_id) is Origin.TEST
        # 원본 레코드는 그대로다 — append 수정만 존재한다.
        stored = ds.events[event.event_id]
        assert stored.origin is Origin.OPERATION

    def test_without_amendment_effective_origin_is_recorded_origin(self):
        spec = make_spec()
        event = make_event(spec)
        ds = Dataset([spec, event])
        assert ds.effective_origin(event.event_id) is Origin.OPERATION
