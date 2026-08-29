"""전수 검토 큐와 append 검토·`test` 강등 (T5, spec §3.4·§3.6, §5 "세션 뒤" 4).

- 큐는 amendment를 반영한 유효 출처 기준의 새 `operation` 사건 전부다.
  선택적으로 불리한 사건을 빼는 매개변수 자체가 없다.
- 검토·강등은 append 전용이다. 원본 레코드는 절대 바뀌지 않는다.
"""

from __future__ import annotations

import inspect

import pytest

from rejectbench import (
    ORIGIN_FIELD,
    AppendStore,
    CaptureStatus,
    Dataset,
    Origin,
    OriginEvidence,
    ReviewError,
    Status,
    Utility,
    demote_to_test,
    record_review,
    review_queue,
    review_status,
)
from tests.factories import make_event, make_review, make_spec, ts


@pytest.fixture()
def store(tmp_path) -> AppendStore:
    return AppendStore(tmp_path / "store")


def load_dataset(store: AppendStore) -> Dataset:
    return Dataset(store.load().records)


class TestReviewQueue:
    def test_lists_all_unreviewed_operation_events_in_append_order(self, store):
        spec = make_spec()
        store.append(spec)
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec, event_id="ev-2", session_id="claude:s-2", occurred_at=ts(20))
        e3 = make_event(spec, event_id="ev-3", session_id="claude:s-3", occurred_at=ts(30))
        for event in (e1, e2, e3):
            store.append(event)
        store.append(make_review(e2))

        queue = review_queue(load_dataset(store))
        assert queue.pending_event_ids == ("ev-1", "ev-3")
        assert queue.reviewed == 1

    def test_excludes_test_unknown_unregistered_but_counts_them(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-op"))
        store.append(
            make_event(
                spec,
                event_id="ev-test",
                origin=Origin.TEST,
                origin_evidence=OriginEvidence.EXPLICIT_FLAG,
                occurred_at=ts(20),
            )
        )
        store.append(
            make_event(
                spec,
                event_id="ev-unknown",
                origin=Origin.UNKNOWN,
                origin_evidence=OriginEvidence.NO_CONTEXT,
                occurred_at=ts(30),
            )
        )
        store.append(
            make_event(
                None,
                event_id="ev-unreg",
                unregistered=True,
                guard_hint="~/.claude/hooks/unknown.sh",
                occurred_at=ts(40),
            )
        )

        queue = review_queue(load_dataset(store))
        assert queue.pending_event_ids == ("ev-op",)
        assert (queue.test, queue.unknown, queue.unregistered) == (1, 1, 1)

    def test_partial_capture_event_stays_in_queue(self, store):
        # spec §3.2: partial 사건도 검토 대상이다 — 분모 자격에서 빼지 않는다.
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-1", capture_status=CaptureStatus.PARTIAL))

        queue = review_queue(load_dataset(store))
        assert queue.pending_event_ids == ("ev-1",)

    def test_demoted_event_leaves_queue_via_effective_origin(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-1"))
        demote_to_test(store, event_id="ev-1", reason="강제 발동 확인")

        queue = review_queue(load_dataset(store))
        assert queue.pending_event_ids == ()
        assert queue.test == 1

    def test_queue_has_no_filtering_parameter(self):
        # 선택적으로 불리한 사건을 빼는 경로가 없어야 한다 — 입력은 dataset 하나뿐.
        assert list(inspect.signature(review_queue).parameters) == ["dataset"]


class TestRecordReview:
    def test_appends_review_and_roundtrips(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-1"))

        review = record_review(
            store, event_id="ev-1", utility=Utility.USEFUL, note="실제 사고를 막았다"
        )

        dataset = load_dataset(store)
        latest = dataset.latest_review("ev-1")
        assert latest is not None
        assert latest.review_id == review.review_id
        assert latest.utility is Utility.USEFUL
        assert latest.note == "실제 사고를 막았다"
        assert dataset.check_integrity() == []

    def test_re_review_appends_and_preserves_history(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-1"))

        first = record_review(store, event_id="ev-1", utility=Utility.UNCERTAIN)
        second = record_review(store, event_id="ev-1", utility=Utility.USEFUL, note="다시 보니 유용")

        dataset = load_dataset(store)
        reviews = dataset.reviews_for("ev-1")
        assert [r.review_id for r in reviews] == [first.review_id, second.review_id]
        assert dataset.latest_review("ev-1").utility is Utility.USEFUL

    def test_uncertain_is_held_not_confirmed(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-1"))
        record_review(store, event_id="ev-1", utility=Utility.UNCERTAIN)

        assert review_status(load_dataset(store), "ev-1") is Status.HELD

    def test_unknown_event_rejected(self, store):
        with pytest.raises(ReviewError):
            record_review(store, event_id="ev-없음", utility=Utility.USEFUL)

    def test_utility_must_be_enum(self, store):
        with pytest.raises(ReviewError):
            record_review(store, event_id="ev-1", utility="useful")


class TestDemoteToTest:
    def test_demote_appends_amendment_and_keeps_original(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-1"))

        amendment = demote_to_test(store, event_id="ev-1", reason="시험 발동이었다")

        dataset = load_dataset(store)
        assert dataset.effective_origin("ev-1") is Origin.TEST
        stored = dataset.amendments_for("ev-1")
        assert [a.amendment_id for a in stored] == [amendment.amendment_id]
        assert stored[0].field == ORIGIN_FIELD
        assert stored[0].reason == "시험 발동이었다"
        assert stored[0].new_value == Origin.TEST.value
        # 원본 사건 레코드는 절대 덮지 않는다.
        assert dataset.events["ev-1"].origin is Origin.OPERATION
        assert dataset.check_integrity() == []

    def test_unknown_to_test_allowed(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(
            make_event(
                spec,
                event_id="ev-1",
                origin=Origin.UNKNOWN,
                origin_evidence=OriginEvidence.NO_CONTEXT,
            )
        )

        demote_to_test(store, event_id="ev-1", reason="시험 맥락으로 확인")
        assert load_dataset(store).effective_origin("ev-1") is Origin.TEST

    def test_reason_required(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-1"))

        with pytest.raises(ReviewError):
            demote_to_test(store, event_id="ev-1", reason="   ")

    def test_already_test_rejected(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-1"))
        demote_to_test(store, event_id="ev-1", reason="시험 발동")

        with pytest.raises(ReviewError):
            demote_to_test(store, event_id="ev-1", reason="다시 강등")

    def test_missing_event_rejected(self, store):
        with pytest.raises(ReviewError):
            demote_to_test(store, event_id="ev-없음", reason="사유")
