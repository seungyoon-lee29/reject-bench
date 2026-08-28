"""지표 불변식: 판정 가능 가드 분모, 분자⊆분모, 보류값·미처리 구분, 분모 0 = 미검증."""

from __future__ import annotations

from rejectbench import (
    CaptureStatus,
    Dataset,
    Origin,
    OriginEvidence,
    Status,
    Utility,
    Verdict,
    amend_origin,
    decision_completion,
    review_status,
    verdict_status,
)
from tests.factories import (
    make_decision,
    make_event,
    make_review,
    make_spec,
    make_verdict,
    ts,
)


def confirmed_pair(event, i):
    return [
        make_verdict(event, verdict_id=f"vd-{i}"),
        make_review(event, review_id=f"rv-{i}"),
    ]


def two_session_guard(guard_id="guard-a", with_decision=False):
    """서로 다른 두 운영 세션 사건 + 확정 판정·검토를 갖춘 가드."""
    spec = make_spec(guard_id=guard_id)
    e1 = make_event(spec, event_id=f"{guard_id}-ev-1", session_id="claude:s-1")
    e2 = make_event(spec, event_id=f"{guard_id}-ev-2", session_id="claude:s-2")
    records = [spec, e1, e2, *confirmed_pair(e1, f"{guard_id}-1"), *confirmed_pair(e2, f"{guard_id}-2")]
    if with_decision:
        records.append(
            make_decision(
                guard_id=guard_id,
                decision_id=f"dc-{guard_id}",
                evidence_event_ids=(e1.event_id, e2.event_id),
            )
        )
    return records


class TestDenominator:
    def test_empty_dataset_is_unverified_not_success(self):
        completion = decision_completion(Dataset([]))
        assert completion.denominator == 0
        assert completion.numerator == 0
        assert completion.unverified is True
        assert completion.percentage is None
        assert completion.fraction == "0/0"

    def test_two_distinct_operation_sessions_with_confirmed_values_are_decidable(self):
        ds = Dataset(two_session_guard())
        completion = decision_completion(ds)
        assert completion.decidable_guard_ids == frozenset({"guard-a"})
        assert completion.denominator == 1
        assert completion.numerator == 0
        assert completion.unverified is False

    def test_single_session_guard_is_not_decidable(self):
        spec = make_spec()
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec, event_id="ev-2", session_id="claude:s-1")  # 같은 세션
        ds = Dataset([spec, e1, e2, *confirmed_pair(e1, 1), *confirmed_pair(e2, 2)])
        assert decision_completion(ds).denominator == 0

    def test_partial_capture_event_still_counts_when_confirmed(self):
        spec = make_spec()
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(
            spec, event_id="ev-2", session_id="claude:s-2", capture_status=CaptureStatus.PARTIAL
        )
        ds = Dataset([spec, e1, e2, *confirmed_pair(e1, 1), *confirmed_pair(e2, 2)])
        assert decision_completion(ds).denominator == 1


class TestHeldVsUnprocessed:
    def test_held_and_unprocessed_are_distinct_states(self):
        spec = make_spec()
        held = make_event(spec, event_id="ev-held", session_id="claude:s-1")
        untouched = make_event(spec, event_id="ev-raw", session_id="claude:s-2")
        ds = Dataset(
            [
                spec,
                held,
                untouched,
                make_verdict(held, verdict_id="vd-1", verdict=Verdict.INSUFFICIENT_CONTEXT),
                make_review(held, review_id="rv-1", utility=Utility.UNCERTAIN),
            ]
        )
        assert verdict_status(ds, "ev-held") is Status.HELD
        assert review_status(ds, "ev-held") is Status.HELD
        assert verdict_status(ds, "ev-raw") is Status.UNPROCESSED
        assert review_status(ds, "ev-raw") is Status.UNPROCESSED
        assert Status.HELD is not Status.UNPROCESSED

    def test_held_verdict_excludes_event_from_denominator(self):
        spec = make_spec()
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec, event_id="ev-2", session_id="claude:s-2")
        ds = Dataset(
            [
                spec,
                e1,
                e2,
                *confirmed_pair(e1, 1),
                make_verdict(e2, verdict_id="vd-2", verdict=Verdict.INSUFFICIENT_CONTEXT),
                make_review(e2, review_id="rv-2"),
            ]
        )
        assert decision_completion(ds).denominator == 0

    def test_uncertain_review_excludes_event_from_denominator(self):
        spec = make_spec()
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec, event_id="ev-2", session_id="claude:s-2")
        ds = Dataset(
            [
                spec,
                e1,
                e2,
                *confirmed_pair(e1, 1),
                make_verdict(e2, verdict_id="vd-2"),
                make_review(e2, review_id="rv-2", utility=Utility.UNCERTAIN),
            ]
        )
        assert decision_completion(ds).denominator == 0

    def test_unprocessed_event_excludes_but_other_confirmed_pair_can_still_qualify(self):
        spec = make_spec()
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec, event_id="ev-2", session_id="claude:s-2")
        e3 = make_event(spec, event_id="ev-3", session_id="claude:s-3")  # 미처리
        ds = Dataset([spec, e1, e2, e3, *confirmed_pair(e1, 1), *confirmed_pair(e2, 2)])
        # 미처리 사건은 판정 가능 사건이 아니지만 가드 자격을 박탈하지도 않는다.
        assert decision_completion(ds).denominator == 1

    def test_incorrect_block_and_unnecessary_are_confirmed_values(self):
        spec = make_spec()
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec, event_id="ev-2", session_id="claude:s-2")
        ds = Dataset(
            [
                spec,
                e1,
                e2,
                make_verdict(e1, verdict_id="vd-1", verdict=Verdict.INCORRECT_BLOCK),
                make_review(e1, review_id="rv-1", utility=Utility.UNNECESSARY),
                *confirmed_pair(e2, 2),
            ]
        )
        assert decision_completion(ds).denominator == 1

    def test_reverdict_uses_latest_and_preserves_previous(self):
        spec = make_spec()
        event = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        first = make_verdict(event, verdict_id="vd-1", verdict=Verdict.INSUFFICIENT_CONTEXT)
        second = make_verdict(event, verdict_id="vd-2", judged_at=ts(70))
        ds = Dataset([spec, event, first, second])
        assert verdict_status(ds, "ev-1") is Status.CONFIRMED
        # 재판정은 이전 레코드를 보존한다.
        assert len(ds.verdicts_for("ev-1")) == 2


class TestOriginExclusion:
    def test_test_origin_events_do_not_enter_denominator(self):
        spec = make_spec()
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(
            spec,
            event_id="ev-2",
            session_id="claude:s-2",
            origin=Origin.TEST,
            origin_evidence=OriginEvidence.EXPLICIT_FLAG,
        )
        ds = Dataset([spec, e1, e2, *confirmed_pair(e1, 1), *confirmed_pair(e2, 2)])
        assert decision_completion(ds).denominator == 0

    def test_unknown_origin_events_do_not_enter_denominator(self):
        spec = make_spec()
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(
            spec,
            event_id="ev-2",
            session_id="claude:s-2",
            origin=Origin.UNKNOWN,
            origin_evidence=OriginEvidence.NO_CONTEXT,
        )
        ds = Dataset([spec, e1, e2, *confirmed_pair(e1, 1), *confirmed_pair(e2, 2)])
        assert decision_completion(ds).denominator == 0

    def test_unregistered_events_do_not_enter_metrics(self):
        e1 = make_event(
            None, event_id="ev-1", session_id="claude:s-1", unregistered=True, guard_hint="g.sh"
        )
        e2 = make_event(
            None, event_id="ev-2", session_id="claude:s-2", unregistered=True, guard_hint="g.sh"
        )
        ds = Dataset([e1, e2])
        completion = decision_completion(ds)
        assert completion.denominator == 0
        assert completion.unverified is True

    def test_unregistered_event_cannot_be_retroactively_linked(self):
        # 사후 GuardSpec 등록은 과거 unregistered 사건을 소급 연결하지 않는다.
        from rejectbench import Amendment

        spec = make_spec()
        e1 = make_event(
            None, event_id="ev-1", session_id="claude:s-1", unregistered=True, guard_hint="g.sh"
        )
        link_attempt = Amendment(
            amendment_id="am-1",
            target_id="ev-1",
            field="guard_id",
            previous_value_hash="sha256:" + "a" * 64,
            new_value=spec.guard_id,
            reason="사후 등록 연결 시도",
            amended_at=ts(300),
        )
        ds = Dataset([e1, spec, link_attempt, *confirmed_pair(e1, 1)])
        from rejectbench import operation_event_ids

        assert operation_event_ids(ds) == []
        assert decision_completion(ds).denominator == 0

    def test_demoted_event_leaves_the_denominator(self):
        records = two_session_guard()
        event = records[2]  # ev-2
        records.append(
            amend_origin(
                event,
                new_origin=Origin.TEST,
                reason="검토에서 강제 발동으로 확인",
                amendment_id="am-1",
                amended_at=ts(300),
            )
        )
        ds = Dataset(records)
        assert decision_completion(ds).denominator == 0


class TestNumerator:
    def test_qualifying_decision_counts_and_is_subset(self):
        ds = Dataset(two_session_guard(with_decision=True))
        completion = decision_completion(ds)
        assert completion.numerator == 1
        assert completion.denominator == 1
        assert completion.decided_guard_ids <= completion.decidable_guard_ids
        assert completion.fraction == "1/1"
        assert completion.percentage == 100.0

    def test_decision_on_non_decidable_guard_never_inflates_numerator(self):
        spec = make_spec()
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        ds = Dataset(
            [
                spec,
                e1,
                *confirmed_pair(e1, 1),
                make_decision(guard_id=spec.guard_id, evidence_event_ids=(e1.event_id,)),
            ]
        )
        completion = decision_completion(ds)
        # 분자는 분모의 부분집합으로만 센다 — 세션 1개 가드는 분모 밖이다.
        assert completion.denominator == 0
        assert completion.numerator == 0
        assert completion.unverified is True

    def test_decision_with_single_session_evidence_does_not_count(self):
        records = two_session_guard()
        e1_id = records[1].event_id
        records.append(
            make_decision(guard_id="guard-a", evidence_event_ids=(e1_id,))
        )
        completion = decision_completion(Dataset(records))
        assert completion.denominator == 1
        assert completion.numerator == 0

    def test_mixed_guards_count_independently(self):
        records = two_session_guard("guard-a", with_decision=True)
        records += two_session_guard("guard-b", with_decision=False)
        completion = decision_completion(Dataset(records))
        assert completion.denominator == 2
        assert completion.numerator == 1
        assert completion.fraction == "1/2"
