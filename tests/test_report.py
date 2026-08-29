"""보고서 계약 (T6, spec §6).

- 대표 지표·진단 지표는 원수 `N/D`와 백분율을 병기하고, 분모 0은 `미검증`이다.
- 판정 가능 정의는 metrics의 단일 정의를 재사용한다 (분자⊆분모 구성 강제).
- spec §6의 병기 목록 전부가 렌더링에 존재해야 한다.
- 시험 사건은 운영 지표 밖 — "기술 검증" 절에만 별도 표시.
- 보고서에 홈 경로·세션 식별자가 존재하지 않는다 (경로는 store 상대 표기).
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest

from rejectbench import (
    Amendment,
    AppendStore,
    EnforcementRef,
    JudgeCalibration,
    LossKind,
    LossRecord,
    Origin,
    OriginEvidence,
    append_calibration,
    value_hash,
)
from rejectbench.judge import CALIBRATION_FILENAME
from rejectbench.report import (
    BASELINE_FILENAME,
    REPORTS_DIRNAME,
    TEST_EVIDENCE_MARK,
    Ratio,
    build_report,
    default_report_path,
    generate_report,
    render_report,
)
from rejectbench.records import CaptureStatus, Decision, Utility, Verdict
from rejectbench.rubric import rubric_hash
from tests.factories import (
    make_decision,
    make_event,
    make_review,
    make_spec,
    make_verdict,
    ts,
)

NOW = ts(2000)
HOME_HINT = str(Path.home() / ".claude" / "hooks" / "ghost-guard.sh")


@pytest.fixture()
def store(tmp_path) -> AppendStore:
    return AppendStore(tmp_path / "store")


def build_rich_store(store: AppendStore) -> None:
    """guard-a(판정 가능·결정 완료), guard-b(발동 사건 없음), guard-c(관측 도중 추가).

    사건 구성 — 유효 출처 기준:
    - operation 5: ev-1(correct+useful), ev-2(incorrect+useful), ev-3(correct+unnecessary),
      ev-4(insufficient 보류 + 검토 미처리), ev-c(판정 미처리 + uncertain 보류)
    - test 2: ev-t(명시 플래그, drift), ev-d(강등 amendment)
    - unknown 1: ev-u(partial)
    - unregistered 1: ev-x (홈 경로 guard_hint — 렌더링 비노출 검증용)
    """
    spec_a = make_spec()  # guard-a v1 — 첫 사건 전 등록
    spec_b = make_spec(guard_id="guard-b", purpose="발동 사건 없는 가드")
    store.append(spec_a)
    store.append(spec_b)

    e1 = make_event(spec_a, event_id="ev-1", session_id="claude:s-1", occurred_at=ts(10))
    e2 = make_event(spec_a, event_id="ev-2", session_id="claude:s-2", occurred_at=ts(20))
    e3 = make_event(spec_a, event_id="ev-3", session_id="claude:s-1", occurred_at=ts(30))
    e4 = make_event(spec_a, event_id="ev-4", session_id="claude:s-3", occurred_at=ts(40))
    et = make_event(
        spec_a,
        event_id="ev-t",
        session_id="claude:s-t",
        origin=Origin.TEST,
        origin_evidence=OriginEvidence.EXPLICIT_FLAG,
        drift=True,
        occurred_at=ts(45),
    )
    ed = make_event(spec_a, event_id="ev-d", session_id="claude:s-1", occurred_at=ts(50))
    eu = make_event(
        spec_a,
        event_id="ev-u",
        session_id="claude:s-u",
        origin=Origin.UNKNOWN,
        origin_evidence=OriginEvidence.NO_CONTEXT,
        capture_status=CaptureStatus.PARTIAL,
        occurred_at=ts(55),
    )
    ex = make_event(
        None,
        event_id="ev-x",
        session_id="claude:s-x",
        unregistered=True,
        guard_hint=HOME_HINT,
        occurred_at=ts(58),
    )
    for event in (e1, e2, e3, e4, et, ed, eu, ex):
        store.append(event)

    store.append(make_verdict(e1, verdict_id="vd-1"))
    store.append(make_verdict(e2, verdict_id="vd-2", verdict=Verdict.INCORRECT_BLOCK))
    store.append(make_verdict(e3, verdict_id="vd-3"))
    store.append(
        make_verdict(
            e4, verdict_id="vd-4", verdict=Verdict.INSUFFICIENT_CONTEXT, judged_at=ts(70)
        )
    )
    store.append(make_review(e1, review_id="rv-1"))
    store.append(make_review(e2, review_id="rv-2"))
    store.append(make_review(e3, review_id="rv-3", utility=Utility.UNNECESSARY))

    # ev-d: operation → test 강등 amendment
    store.append(
        Amendment(
            amendment_id="am-1",
            target_id="ev-d",
            field="origin",
            previous_value_hash=value_hash("operation"),
            new_value="test",
            reason="시험 발동으로 확인",
            amended_at=ts(95),
        )
    )
    store.append(
        LossRecord(
            loss_id="loss-1",
            recorded_at=ts(96),
            kind=LossKind.VERDICT_FAILURE,
            detail="policy_verdict 실패: TransportError",
            subject_ref="ev-4",
        )
    )
    # 결정: guard-a keep(산입), guard-b keep(발동 사건 없음 — 별도 표기)
    store.append(make_decision(evidence_event_ids=("ev-1", "ev-2")))
    store.append(make_decision(guard_id="guard-b", decision_id="dc-2"))

    # guard-c: 첫 사건 뒤 등록 — 관측 도중 추가된 신규 가드
    spec_c = make_spec(
        guard_id="guard-c",
        purpose="관측 도중 추가된 가드",
        enforcement_ref=EnforcementRef(script_path=HOME_HINT, file_hash="sha256:" + "9" * 64),
    )
    store.append(spec_c)
    ec = make_event(
        spec_c, event_id="ev-c", session_id="claude:s-9", occurred_at=ts(1910)
    )
    store.append(ec)
    store.append(
        make_review(ec, review_id="rv-c", utility=Utility.UNCERTAIN, reviewed_at=ts(1990))
    )


class TestRatio:
    def test_zero_denominator_is_unverified_not_success(self):
        ratio = Ratio(numerator=0, denominator=0)
        assert ratio.unverified
        assert ratio.percentage is None
        assert ratio.render() == "미검증 (분모 0 — 성공 아님)"

    def test_renders_raw_fraction_with_percentage(self):
        assert Ratio(numerator=1, denominator=3).render() == "1/3 (33.3%)"
        assert Ratio(numerator=2, denominator=2).render() == "2/2 (100.0%)"

    def test_numerator_must_be_subset_of_denominator(self):
        with pytest.raises(AssertionError):
            Ratio(numerator=2, denominator=1)


class TestBuildReportMetrics:
    def test_headline_completion_reuses_metrics_definition(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.completion.fraction == "1/1"
        assert data.completion.percentage == 100.0

    def test_diagnostic_ratios(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        # 정책 판정 확정 operation 사건: ev-1, ev-2, ev-3 — incorrect: ev-2
        assert data.policy_mismatch.fraction == "1/3"
        # 유용성 검토 확정: ev-1, ev-2, ev-3 — unnecessary: ev-3
        assert data.unnecessary_block.fraction == "1/3"
        # 두 판단 확정: 3 — 방향 불일치: ev-2(그른 차단+유용), ev-3(옳은 차단+불필요)
        assert data.disagreement.fraction == "2/3"
        assert data.correct_but_unnecessary.fraction == "1/3"
        assert data.incorrect_but_useful.fraction == "1/3"

    def test_origin_counts_exclude_test_unknown_unregistered(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.operation_count == 5
        assert data.test_count == 2  # 명시 플래그 1 + 강등 1
        assert data.unknown_count == 1
        assert data.unregistered_count == 1

    def test_pending_and_held_with_max_elapsed(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.verdict_pending.unprocessed == 1  # ev-c
        assert data.verdict_pending.unprocessed_max_elapsed == timedelta(minutes=90)
        assert data.verdict_pending.held == 1  # ev-4 insufficient_context
        assert data.verdict_pending.held_max_elapsed == NOW - ts(70)
        assert data.review_pending.unprocessed == 1  # ev-4
        assert data.review_pending.unprocessed_max_elapsed == NOW - ts(40)
        assert data.review_pending.held == 1  # ev-c uncertain
        assert data.review_pending.held_max_elapsed == timedelta(minutes=10)

    def test_loss_partial_amendment_demotion_drift(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        assert data.loss_total == 1
        assert dict(data.loss_by_kind)["verdict_failure"] == 1
        assert data.partial_capture_count == 1
        assert data.amendment_count == 1
        assert data.demotion_count == 1
        assert data.drift_count == 1
        assert data.post_remove_count == 0

    def test_guard_rows_and_new_guard_flag(self, store):
        build_rich_store(store)
        data = build_report(store, now=NOW)
        rows = {row.guard_id: row for row in data.guards}
        assert set(rows) == {"guard-a", "guard-b", "guard-c"}
        a = rows["guard-a"]
        assert a.operation_session_count == 3  # s-1, s-2, s-3
        assert a.guard_decidable
        assert a.decision_count == 1 and a.countable_decision_count == 1
        assert not a.new_during_observation
        b = rows["guard-b"]
        assert b.no_event_decision_count == 1
        c = rows["guard-c"]
        assert c.new_during_observation
        assert data.new_guard_ids == ("guard-c",)

    def test_post_remove_uses_derived_function_not_stored_flag(self, store):
        spec = make_spec()
        store.append(spec)
        e1 = make_event(spec, event_id="ev-1", session_id="claude:s-1")
        store.append(e1)
        store.append(
            make_decision(decision_id="dc-r", decision=Decision.REMOVE, rationale="제거")
        )
        # remove 결정 뒤 발동 — 레코드의 post_remove 저장값은 False다.
        after = make_event(spec, event_id="ev-2", session_id="claude:s-2", occurred_at=ts(200))
        assert after.post_remove is False
        store.append(after)
        data = build_report(store, now=NOW)
        assert data.post_remove_count == 1


class TestRenderReport:
    def test_empty_store_reports_operation_unverified(self, store):
        report = generate_report(store, now=NOW)
        assert "운영: 미검증" in report
        assert "미검증 (분모 0 — 성공 아님)" in report
        assert "operation 0 · test 0 · unknown 0 · unregistered 0" in report
        assert TEST_EVIDENCE_MARK in report
        assert "test evidence only" in report
        assert "미측정" in report  # 기준선
        assert "교정 레코드 없음" in report
        # 한계 문구 (spec §6·§9)
        assert "단측 증거" in report
        assert "1인 자기측정" in report
        assert "docs/관찰-프로토콜.md" in report
        assert "일반화하지 않는다" in report

    def test_rich_store_renders_raw_numbers_and_directions(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert "1/1 (100.0%)" in report  # 대표 지표
        assert "운영: 미검증" not in report
        assert report.count("1/3 (33.3%)") >= 2  # 정책 불일치율·불필요 차단율
        assert "2/3 (66.7%)" in report  # 불일치율
        assert "정책상 옳은 차단 + 사용자 불필요: 1/3" in report
        assert "정책상 그른 차단 + 사용자 유용: 1/3" in report
        assert "operation 5 · test 2 · unknown 1 · unregistered 1" in report

    def test_rich_store_renders_pending_elapsed(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert "1시간 30분" in report  # verdict 미처리 최장 경과 (ev-c)
        assert "1일 8시간" in report  # 보류 최장 경과 (ev-4)

    def test_new_guard_marked_separately(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert "관측 도중 추가" in report
        assert "guard-c" in report

    def test_test_events_only_in_technical_section(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert TEST_EVIDENCE_MARK in report
        assert "시험(test) 사건: 2건" in report

    def test_no_home_path_or_session_ids(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert str(Path.home()) not in report
        assert "/Users/" not in report
        assert "claude:" not in report  # 세션 식별자 비노출 — 수는 숫자만
        assert str(store.root) not in report
        for event_id in ("ev-1", "ev-c", "ev-x"):
            assert event_id not in report


class TestBaselineAndCalibration:
    def test_baseline_absent_is_reported_as_unmeasured(self, store):
        build_rich_store(store)
        report = generate_report(store, now=NOW)
        assert "기준선" in report
        assert "미측정" in report

    def test_baseline_file_rendered(self, store):
        build_rich_store(store)
        store.root.mkdir(parents=True, exist_ok=True)
        (store.root / BASELINE_FILENAME).write_text(
            '{"복원 시간(분)": 12, "복원도": "4/5"}', encoding="utf-8"
        )
        report = generate_report(store, now=NOW)
        assert "복원 시간(분): 12" in report
        assert "복원도: 4/5" in report

    def test_calibration_sidecar_status(self, store):
        build_rich_store(store)
        spec = make_spec()
        current = rubric_hash()
        stale = JudgeCalibration(
            calibration_id="cal-1",
            calibrated_at=ts(100),
            guard_spec_hash=spec.content_hash,
            rubric_hash=current,
            model_id="judge-model-1",
            model_settings_hash="sha256:" + "3" * 64,
            examples_total=2,
            examples_passed=1,
            passed=False,
            failures=("allow[0]: 기대 incorrect_block, 실제 correct_block",),
        )
        fresh = JudgeCalibration(
            calibration_id="cal-2",
            calibrated_at=ts(110),
            guard_spec_hash=spec.content_hash,
            rubric_hash=current,
            model_id="judge-model-1",
            model_settings_hash="sha256:" + "3" * 64,
            examples_total=2,
            examples_passed=2,
            passed=True,
            failures=(),
        )
        append_calibration(store.root, stale)
        append_calibration(store.root, fresh)
        with open(store.root / CALIBRATION_FILENAME, "a", encoding="utf-8") as fh:
            fh.write("{broken json\n")
        report = generate_report(store, now=NOW)
        assert "guard-a v1" in report
        assert "통과 (2/2)" in report
        assert "교정 레코드 없음" not in report
        assert "손상" in report


class TestDefaultReportPath:
    def test_path_under_store_reports_with_utc_stamp(self, store):
        path = default_report_path(store, now=NOW)
        assert path.parent == store.root / REPORTS_DIRNAME
        assert re.fullmatch(r"report-\d{8}T\d{6}Z\.md", path.name)
