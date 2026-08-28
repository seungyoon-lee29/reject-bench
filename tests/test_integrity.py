"""데이터셋 참조 무결성: 사건→spec, 판정→사건, 결정→근거, amendment→원본."""

from __future__ import annotations

from rejectbench import Amendment, Dataset, Decision, Origin, OriginEvidence, content_hash
from tests.factories import (
    make_decision,
    make_event,
    make_review,
    make_spec,
    make_verdict,
    ts,
)


def kinds(dataset: Dataset) -> list[str]:
    return [v.kind for v in dataset.check_integrity()]


def test_clean_dataset_has_no_violations():
    spec = make_spec()
    event = make_event(spec)
    ds = Dataset(
        [
            spec,
            event,
            make_verdict(event),
            make_review(event),
            make_decision(guard_id=spec.guard_id, evidence_event_ids=(event.event_id,)),
        ]
    )
    assert ds.check_integrity() == []


def test_duplicate_record_id_is_a_violation():
    spec = make_spec()
    ds = Dataset([spec, make_event(spec, event_id="ev-1"), make_event(spec, event_id="ev-1")])
    assert "duplicate_record_id" in kinds(ds)


def test_event_referencing_missing_spec():
    spec = make_spec()  # 등록하지 않고 참조만 시킨다
    ds = Dataset([make_event(spec)])
    assert "missing_spec" in kinds(ds)


def test_event_spec_hash_mismatch():
    spec = make_spec()
    other_hash = content_hash(
        purpose="다른 내용",
        policy="다른 정책",
        exceptions=(),
        allow_examples=("a",),
        block_examples=("b",),
    )
    event = make_event(spec, guard_spec_hash=other_hash)
    ds = Dataset([spec, event])
    assert "spec_hash_mismatch" in kinds(ds)


def test_operation_event_requires_spec_earlier_in_append_order():
    spec = make_spec(created_at=ts(0))
    event = make_event(spec, occurred_at=ts(10))
    # 벽시계는 spec이 먼저지만 append 순서가 뒤집혔다 — append 순서가 근거다.
    ds = Dataset([event, spec])
    assert "spec_not_before_event" in kinds(ds)
    ok = Dataset([spec, event])
    assert "spec_not_before_event" not in kinds(ok)


def test_wall_clock_anomaly_is_also_flagged():
    spec = make_spec(created_at=ts(50))
    event = make_event(spec, occurred_at=ts(10))
    ds = Dataset([spec, event])
    assert "spec_created_after_event" in kinds(ds)


def test_test_origin_event_is_not_subject_to_precedence():
    spec = make_spec()
    event = make_event(spec, origin=Origin.TEST, origin_evidence=OriginEvidence.EXPLICIT_FLAG)
    ds = Dataset([event, spec])
    assert "spec_not_before_event" not in kinds(ds)


def test_unregistered_event_is_recorded_without_spec_violations():
    event = make_event(None, unregistered=True, guard_hint="unknown.sh")
    ds = Dataset([event])
    assert ds.check_integrity() == []


def test_verdict_for_missing_event():
    spec = make_spec()
    event = make_event(spec)
    ds = Dataset([spec, make_verdict(event)])
    assert "missing_event" in kinds(ds)


def test_verdict_spec_hash_must_match_event_reference():
    spec = make_spec()
    event = make_event(spec)
    bad = make_verdict(event, guard_spec_hash="sha256:" + "9" * 64)
    ds = Dataset([spec, event, bad])
    assert "verdict_spec_hash_mismatch" in kinds(ds)


def test_verdict_for_unregistered_event_is_flagged():
    event = make_event(None, unregistered=True, guard_hint="unknown.sh")
    ds = Dataset([event, make_verdict(event)])
    assert "verdict_for_unregistered_event" in kinds(ds)


def test_review_for_missing_event():
    spec = make_spec()
    event = make_event(spec)
    ds = Dataset([spec, make_review(event)])
    assert "missing_event" in kinds(ds)


def test_decision_evidence_must_exist():
    spec = make_spec()
    ds = Dataset(
        [spec, make_decision(guard_id=spec.guard_id, evidence_event_ids=("ev-ghost",))]
    )
    assert "missing_event" in kinds(ds)


def test_decision_evidence_must_belong_to_same_guard():
    spec_a = make_spec(guard_id="guard-a")
    spec_b = make_spec(guard_id="guard-b", spec_id="spec-guard-b-v1")
    event_b = make_event(spec_b, event_id="ev-b")
    ds = Dataset(
        [
            spec_a,
            spec_b,
            event_b,
            make_decision(guard_id="guard-a", evidence_event_ids=("ev-b",)),
        ]
    )
    assert "evidence_guard_mismatch" in kinds(ds)


def test_modify_decision_must_link_existing_resulting_version():
    spec = make_spec(guard_id="guard-a", version=1)
    event = make_event(spec)
    dangling = make_decision(
        guard_id="guard-a",
        decision=Decision.MODIFY,
        resulting_guard_version=2,
        evidence_event_ids=(event.event_id,),
    )
    ds = Dataset([spec, event, dangling])
    assert "resulting_version_missing" in kinds(ds)

    v2 = make_spec(guard_id="guard-a", version=2, policy="개정된 정책")
    ok = Dataset([spec, event, dangling, v2])
    assert "resulting_version_missing" not in kinds(ok)


def test_amendment_target_must_exist():
    am = Amendment(
        amendment_id="am-1",
        target_id="ev-ghost",
        field="origin",
        previous_value_hash="sha256:" + "a" * 64,
        new_value="test",
        reason="강등",
        amended_at=ts(100),
    )
    ds = Dataset([am])
    assert "missing_amendment_target" in kinds(ds)


def test_forbidden_origin_amendment_is_flagged_and_not_applied():
    spec = make_spec()
    event = make_event(
        spec, origin=Origin.UNKNOWN, origin_evidence=OriginEvidence.NO_CONTEXT
    )
    promotion = Amendment(
        amendment_id="am-1",
        target_id=event.event_id,
        field="origin",
        previous_value_hash="sha256:" + "a" * 64,
        new_value="operation",
        reason="아마 운영",
        amended_at=ts(100),
    )
    ds = Dataset([spec, event, promotion])
    assert "forbidden_origin_amendment" in kinds(ds)
    # 금지된 수정은 유효 출처에 적용되지 않는다 — unknown→operation 승격 금지.
    assert ds.effective_origin(event.event_id) is Origin.UNKNOWN
