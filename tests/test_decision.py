"""가드 수명주기 결정 (T5, spec §3.5, §5 "세션 뒤" 5, §7).

완료 조건 고정:
- 네 가지 정책/유용성 조합(옳은 차단+유용, 옳은 차단+불필요, 그른 차단+유용,
  그른 차단+불필요)을 모두 표현한다.
- 가치 검증에 세는 결정의 "서로 다른 둘 이상 operation 세션" 조건은 우회할 수
  없다 — 산입 여부는 항상 파생 계산이고 입력으로 강제할 수 없다.
"""

from __future__ import annotations

import inspect

import pytest

from rejectbench import (
    AppendStore,
    Dataset,
    Decision,
    DecisionError,
    EnforcementStatus,
    SchemaError,
    Utility,
    Verdict,
    annotate_decision,
    build_guard_view,
    check_enforcement,
    decision_completion,
    decision_history,
    demote_to_test,
    enforcement_ref_for,
    is_post_remove,
    post_remove_event_ids,
    record_decision,
    record_modify,
    record_review,
    render_guard_view,
)
from tests.factories import make_event, make_review, make_spec, make_verdict, ts

SPEC_FIELDS = dict(
    project="reject-bench",
    purpose="위험 git 명령이 이력을 파괴하는 사고 방지",
    policy="force push와 hard reset을 차단한다",
    exceptions=(),
    allow_examples=("git status",),
    block_examples=("git push --force",),
)


@pytest.fixture()
def store(tmp_path) -> AppendStore:
    return AppendStore(tmp_path / "store")


def load_dataset(store: AppendStore) -> Dataset:
    return Dataset(store.load().records)


def seed_two_session_guard(store: AppendStore):
    """guard-a: 서로 다른 두 세션의 판정 가능(두 축 확정) 사건 ev-1·ev-2."""
    spec = make_spec()
    store.append(spec)
    e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
    e2 = make_event(spec, event_id="ev-2", session_id="claude:s-2", occurred_at=ts(20))
    store.append(e1)
    store.append(e2)
    store.append(make_verdict(e1, verdict_id="vd-1"))
    store.append(make_verdict(e2, verdict_id="vd-2", verdict=Verdict.INCORRECT_BLOCK))
    store.append(make_review(e1, review_id="rv-1"))
    store.append(make_review(e2, review_id="rv-2", utility=Utility.UNNECESSARY))
    return spec


class TestRecordDecision:
    def test_keep_with_two_sessions_counts(self, store):
        seed_two_session_guard(store)
        outcome = record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.KEEP,
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="두 세션 근거로 유지",
        )
        assert outcome.annotation.countable is True
        assert outcome.annotation.no_event_guard is False
        assert outcome.annotation.reasons == ()

        dataset = load_dataset(store)
        assert [d.decision_id for d in dataset.decisions] == [outcome.decision.decision_id]
        assert dataset.check_integrity() == []
        completion = decision_completion(dataset)
        assert completion.fraction == "1/1"

    def test_single_session_evidence_not_countable_and_not_counted(self, store):
        spec = make_spec()
        store.append(spec)
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec, event_id="ev-2", session_id="claude:s-1", occurred_at=ts(20))
        store.append(e1)
        store.append(e2)
        store.append(make_verdict(e1, verdict_id="vd-1"))
        store.append(make_verdict(e2, verdict_id="vd-2"))
        store.append(make_review(e1, review_id="rv-1"))
        store.append(make_review(e2, review_id="rv-2"))

        outcome = record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.KEEP,
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="단일 세션 근거",
        )
        assert outcome.annotation.countable is False
        assert any("세션" in reason for reason in outcome.annotation.reasons)

        completion = decision_completion(load_dataset(store))
        assert completion.numerator == 0
        assert completion.unverified is True  # 분모 0은 성공이 아니라 미검증

    def test_held_verdict_evidence_rejected(self, store):
        spec = make_spec()
        store.append(spec)
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        store.append(e1)
        store.append(
            make_verdict(e1, verdict_id="vd-1", verdict=Verdict.INSUFFICIENT_CONTEXT)
        )
        store.append(make_review(e1, review_id="rv-1"))

        with pytest.raises(DecisionError):
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.KEEP,
                evidence_event_ids=("ev-1",),
                rationale="보류 판정 근거 시도",
            )

    def test_unprocessed_verdict_evidence_rejected(self, store):
        spec = make_spec()
        store.append(spec)
        e1 = make_event(spec, event_id="ev-1")
        store.append(e1)
        store.append(make_review(e1, review_id="rv-1"))

        with pytest.raises(DecisionError):
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.KEEP,
                evidence_event_ids=("ev-1",),
                rationale="판정 미처리 근거 시도",
            )

    def test_held_or_missing_review_evidence_rejected(self, store):
        spec = make_spec()
        store.append(spec)
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        e2 = make_event(spec, event_id="ev-2", session_id="claude:s-2", occurred_at=ts(20))
        store.append(e1)
        store.append(e2)
        store.append(make_verdict(e1, verdict_id="vd-1"))
        store.append(make_verdict(e2, verdict_id="vd-2"))
        store.append(make_review(e1, review_id="rv-1", utility=Utility.UNCERTAIN))

        with pytest.raises(DecisionError):  # 보류 검토
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.KEEP,
                evidence_event_ids=("ev-1",),
                rationale="보류 검토 근거 시도",
            )
        with pytest.raises(DecisionError):  # 미처리 검토
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.KEEP,
                evidence_event_ids=("ev-2",),
                rationale="미검토 근거 시도",
            )

    def test_demoted_evidence_rejected(self, store):
        seed_two_session_guard(store)
        demote_to_test(store, event_id="ev-2", reason="시험 발동으로 확인")

        with pytest.raises(DecisionError):
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.KEEP,
                evidence_event_ids=("ev-1", "ev-2"),
                rationale="강등 사건 근거 시도",
            )

    def test_other_guard_evidence_rejected(self, store):
        seed_two_session_guard(store)
        other = make_spec(guard_id="guard-b", purpose="다른 가드")
        store.append(other)

        with pytest.raises(DecisionError):
            record_decision(
                store,
                guard_id="guard-b",
                decision=Decision.KEEP,
                evidence_event_ids=("ev-1",),
                rationale="다른 가드 사건 근거 시도",
            )

    def test_empty_evidence_with_events_rejected(self, store):
        seed_two_session_guard(store)
        with pytest.raises(DecisionError):
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.KEEP,
                evidence_event_ids=(),
                rationale="근거 없는 결정 시도",
            )

    def test_unknown_guard_rejected(self, store):
        with pytest.raises(DecisionError):
            record_decision(
                store,
                guard_id="guard-없음",
                decision=Decision.KEEP,
                evidence_event_ids=(),
                rationale="미등록 가드",
            )

    def test_duplicate_evidence_rejected(self, store):
        seed_two_session_guard(store)
        with pytest.raises(SchemaError):
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.KEEP,
                evidence_event_ids=("ev-1", "ev-1"),
                rationale="중복 근거",
            )

    def test_countable_cannot_be_forced_by_input(self):
        # 산입 여부는 항상 파생 계산 — record_decision에 강제 입력 자리가 없다.
        params = inspect.signature(record_decision).parameters
        assert "countable" not in params
        assert "annotation" not in params

    def test_no_event_guard_decision_marked_outside_metrics(self, store):
        store.append(make_spec(guard_id="guard-b", purpose="발동 없는 가드"))
        outcome = record_decision(
            store,
            guard_id="guard-b",
            decision=Decision.KEEP,
            evidence_event_ids=(),
            rationale="발동 사건 없는 가드 유지",
        )
        assert outcome.annotation.no_event_guard is True
        assert outcome.annotation.countable is False

        completion = decision_completion(load_dataset(store))
        assert completion.denominator == 0
        assert completion.unverified is True


class TestModify:
    def test_record_modify_creates_new_version_via_registry(self, store):
        seed_two_session_guard(store)
        outcome = record_modify(
            store,
            guard_id="guard-a",
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="릴리스 브랜치 예외 추가",
            **{**SPEC_FIELDS, "exceptions": ("release/* 브랜치는 허용",)},
        )
        assert outcome.spec.version == 2
        assert outcome.decision.decision is Decision.MODIFY
        assert outcome.decision.resulting_guard_version == 2
        assert outcome.annotation.countable is True

        dataset = load_dataset(store)
        assert dataset.check_integrity() == []
        assert (("guard-a", 2) in dataset.specs_by_key) is True

    def test_modify_same_content_rejected_without_new_version(self, store):
        seed_two_session_guard(store)
        with pytest.raises(DecisionError):
            record_modify(
                store,
                guard_id="guard-a",
                evidence_event_ids=("ev-1", "ev-2"),
                rationale="내용 동일",
                **SPEC_FIELDS,
            )
        dataset = load_dataset(store)
        assert dataset.decisions == []
        assert ("guard-a", 2) not in dataset.specs_by_key

    def test_record_decision_modify_requires_existing_registry_version(self, store):
        seed_two_session_guard(store)
        with pytest.raises(DecisionError):
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.MODIFY,
                evidence_event_ids=("ev-1", "ev-2"),
                rationale="등록부 밖 버전",
                resulting_guard_version=9,
            )

    def test_modify_without_resulting_version_rejected(self, store):
        seed_two_session_guard(store)
        with pytest.raises(DecisionError):
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.MODIFY,
                evidence_event_ids=("ev-1", "ev-2"),
                rationale="버전 없는 modify",
            )

    def test_non_modify_with_resulting_version_rejected(self, store):
        seed_two_session_guard(store)
        with pytest.raises(SchemaError):
            record_decision(
                store,
                guard_id="guard-a",
                decision=Decision.KEEP,
                evidence_event_ids=("ev-1", "ev-2"),
                rationale="keep에 버전",
                resulting_guard_version=1,
            )


class TestEnforcementCheck:
    def test_in_sync(self, tmp_path):
        script = tmp_path / "guard.sh"
        script.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        spec = make_spec(enforcement_ref=enforcement_ref_for(script))
        check = check_enforcement(spec)
        assert check.status is EnforcementStatus.IN_SYNC

    def test_drift_when_implementation_changed(self, tmp_path):
        script = tmp_path / "guard.sh"
        script.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        spec = make_spec(enforcement_ref=enforcement_ref_for(script))
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        check = check_enforcement(spec)
        assert check.status is EnforcementStatus.DRIFT

    def test_no_ref_unverifiable(self):
        assert check_enforcement(make_spec()).status is EnforcementStatus.UNVERIFIABLE

    def test_missing_file_unverifiable(self, tmp_path):
        script = tmp_path / "guard.sh"
        script.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        spec = make_spec(enforcement_ref=enforcement_ref_for(script))
        script.unlink()
        assert check_enforcement(spec).status is EnforcementStatus.UNVERIFIABLE

    def test_record_modify_reports_enforcement_states(self, store, tmp_path):
        seed_two_session_guard(store)
        script = tmp_path / "guard.sh"
        script.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
        outcome = record_modify(
            store,
            guard_id="guard-a",
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="구현물 반영 확인",
            enforcement_ref=enforcement_ref_for(script),
            **{**SPEC_FIELDS, "policy": "force push·hard reset·filter-branch를 차단한다"},
        )
        assert outcome.enforcement.status is EnforcementStatus.IN_SYNC
        # 이후 구현물이 바뀌면 같은 경로가 drift를 드러낸다.
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        assert check_enforcement(outcome.spec).status is EnforcementStatus.DRIFT


class TestPostRemove:
    def test_event_after_remove_flagged(self, store):
        spec = seed_two_session_guard(store)
        record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.REMOVE,
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="불필요 우세",
        )
        store.append(
            make_event(spec, event_id="ev-3", session_id="claude:s-3", occurred_at=ts(200))
        )

        dataset = load_dataset(store)
        assert post_remove_event_ids(dataset) == ["ev-3"]
        assert is_post_remove(dataset, "ev-3") is True
        assert is_post_remove(dataset, "ev-1") is False

    def test_flag_follows_latest_decision(self, store):
        spec = seed_two_session_guard(store)
        record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.REMOVE,
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="제거",
        )
        store.append(
            make_event(spec, event_id="ev-3", session_id="claude:s-3", occurred_at=ts(200))
        )
        record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.KEEP,
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="번복 — 유지",
        )
        store.append(
            make_event(spec, event_id="ev-4", session_id="claude:s-4", occurred_at=ts(300))
        )

        dataset = load_dataset(store)
        assert post_remove_event_ids(dataset) == ["ev-3"]
        assert is_post_remove(dataset, "ev-4") is False

    def test_stored_flag_respected(self, store):
        spec = make_spec()
        store.append(spec)
        store.append(make_event(spec, event_id="ev-1", post_remove=True))

        assert post_remove_event_ids(load_dataset(store)) == ["ev-1"]


class TestDecisionHistory:
    def test_history_appends_never_overwrites(self, store):
        seed_two_session_guard(store)
        first = record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.KEEP,
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="유지",
        )
        second = record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.REMOVE,
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="번복 — 제거",
        )

        dataset = load_dataset(store)
        history = decision_history(dataset, "guard-a")
        assert [d.decision_id for d in history] == [
            first.decision.decision_id,
            second.decision.decision_id,
        ]
        assert [d.decision for d in history] == [Decision.KEEP, Decision.REMOVE]
        # 이전 결정 레코드는 저장소에 그대로 남는다.
        assert len([r for r in dataset.records if r.record_id == first.decision.decision_id]) == 1


class TestGuardView:
    def test_four_policy_utility_combinations_expressed(self, store):
        """완료 조건: 네 가지 정책/유용성 조합을 모두 표현한다."""
        spec = make_spec()
        store.append(spec)
        combos = [
            ("ev-cu", "claude:s-1", Verdict.CORRECT_BLOCK, Utility.USEFUL),
            ("ev-cn", "claude:s-2", Verdict.CORRECT_BLOCK, Utility.UNNECESSARY),
            ("ev-iu", "claude:s-3", Verdict.INCORRECT_BLOCK, Utility.USEFUL),
            ("ev-in", "claude:s-4", Verdict.INCORRECT_BLOCK, Utility.UNNECESSARY),
        ]
        for i, (event_id, session_id, verdict, utility) in enumerate(combos):
            event = make_event(
                spec, event_id=event_id, session_id=session_id, occurred_at=ts(10 + i)
            )
            store.append(event)
            store.append(make_verdict(event, verdict_id=f"vd-{event_id}", verdict=verdict))
            store.append(make_review(event, review_id=f"rv-{event_id}", utility=utility))

        outcome = record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.KEEP,
            evidence_event_ids=tuple(c[0] for c in combos),
            rationale="네 조합 근거 유지",
        )
        assert outcome.annotation.countable is True

        dataset = load_dataset(store)
        view = build_guard_view(dataset, "guard-a")
        axes = {row.event_id: (row.verdict_label, row.utility_label) for row in view.events}
        assert axes == {
            "ev-cu": ("correct_block", "useful"),
            "ev-cn": ("correct_block", "unnecessary"),
            "ev-iu": ("incorrect_block", "useful"),
            "ev-in": ("incorrect_block", "unnecessary"),
        }
        assert view.operation_session_count == 4
        assert view.guard_decidable is True
        assert decision_completion(dataset).fraction == "1/1"

    def test_view_shows_sessions_axes_and_decidability(self, store):
        spec = seed_two_session_guard(store)
        pending = make_event(
            spec, event_id="ev-3", session_id="claude:s-1", occurred_at=ts(30)
        )
        store.append(pending)

        view = build_guard_view(load_dataset(store), "guard-a")
        assert view.operation_session_count == 2
        assert view.decidable_session_count == 2
        assert view.guard_decidable is True
        rows = {row.event_id: row for row in view.events}
        assert rows["ev-1"].decidable is True
        assert rows["ev-3"].decidable is False
        assert rows["ev-3"].verdict_label == "미처리"
        assert rows["ev-3"].utility_label == "미처리"

        text = render_guard_view(view)
        for token in ("guard-a", "ev-1", "ev-3", "correct_block", "useful", "미처리", "세션"):
            assert token in text

    def test_view_marks_post_remove_and_annotations(self, store):
        spec = seed_two_session_guard(store)
        record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.REMOVE,
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="제거",
        )
        store.append(
            make_event(spec, event_id="ev-3", session_id="claude:s-3", occurred_at=ts(200))
        )

        view = build_guard_view(load_dataset(store), "guard-a")
        assert view.post_remove_count == 1
        text = render_guard_view(view)
        assert "post-remove" in text

    def test_view_no_event_guard(self, store):
        store.append(make_spec(guard_id="guard-b", purpose="발동 없는 가드"))
        view = build_guard_view(load_dataset(store), "guard-b")
        assert view.events == ()
        assert view.guard_decidable is False

    def test_view_unknown_guard_rejected(self, store):
        with pytest.raises(DecisionError):
            build_guard_view(load_dataset(store), "guard-없음")

    def test_annotation_recomputed_from_data_only(self, store):
        """산입 표기는 저장 필드가 아니라 파생 계산 — 데이터가 바뀌면 표기도 바뀐다."""
        seed_two_session_guard(store)
        outcome = record_decision(
            store,
            guard_id="guard-a",
            decision=Decision.KEEP,
            evidence_event_ids=("ev-1", "ev-2"),
            rationale="유지",
        )
        assert outcome.annotation.countable is True
        # 검토 재기록으로 ev-2가 보류가 되면 같은 결정이 산입에서 빠진다.
        record_review(store, event_id="ev-2", utility=Utility.UNCERTAIN)
        dataset = load_dataset(store)
        annotation = annotate_decision(dataset, outcome.decision)
        assert annotation.countable is False
        assert decision_completion(dataset).numerator == 0
